"""对话路由:接入 LangGraph workflow 的 SSE 流式对话 + 人机审批中断恢复。

Phase A+B 重构后:
- 对话历史(干净[human,ai]对 + 摘要)由 persist_context 节点维护, 不再依赖 messages 字段
- AI 回复持久化(MySQL + Milvus)移入 persist_context 节点
- 本模块只负责: 参数校验、构造输入、SSE 流式转发、审批中断恢复
"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from db.mysql import get_db
from models.user import User
from core.deps import get_current_user
from services.session_service import verify_session_owner, auto_title_session
from services.message_service import save_message
from services.image_store import save_image_from_data_uri, resolve_image_url
from graph.workflow_service import graph

router = APIRouter(prefix="/sessions", tags=["对话"])

# SSE 响应头
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

# 仅这些节点产生的 token 需要流式推送给前端(用户可见的回答)
# query_rewriter / evaluate_node 等节点内部也会调用 LLM, 其 token 不应展示给用户
_ANSWER_NODES = {"generator_node", "regenerate_node"}


class ChatRequest(BaseModel):
    """对话请求体。"""

    text: Optional[str] = None
    image: Optional[str] = None  # base64 data URI


class ApproveRequest(BaseModel):
    """审批请求体。"""

    approved: bool
    reason: Optional[str] = None


def _sse(data: dict) -> str:
    """将 dict 序列化为 SSE 事件行。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_graph_events(input_data, config):
    """通用:消费 graph.astream(stream_mode=['messages','updates']) 并产出 SSE 事件流。

    多模式订阅:
    - messages: LLM token 流, 用于前端打字效果(仅 _ANSWER_NODES 的 token)
    - updates:  节点完成时的状态增量, 用于前端展示执行链路

    流结束后检查是否被中断(human_approval)或正常结束(done)。
    """
    # 节点中文描述, 前端展示执行链路用
    _NODE_LABELS = {
        "process_input": "处理输入",
        "image_analysis": "分析图片",
        "query_rewriter": "改写问题",
        "retriever_node": "检索知识库",
        "generator_node": "生成回答",
        "evaluate_node": "评估回答质量",
        "human_approval": "等待人工审批",
        "regenerate_node": "重新生成回答",
        "persist_context": "保存对话记录",
    }

    async for mode, chunk in graph.astream(
        input_data, config=config, stream_mode=["messages", "updates"]
    ):
        if mode == "messages":
            # messages 模式: (AIMessageChunk, metadata) 元组
            msg_chunk, metadata = chunk
            node = metadata.get("langgraph_node", "")
            # 仅推送回答节点的 token, 过滤改写/评估节点内部 LLM 调用的 token
            if node not in _ANSWER_NODES:
                continue
            content = getattr(msg_chunk, "content", None)
            if content:
                content = content if isinstance(content, str) else str(content)
                yield _sse({"type": "token", "content": content})

        elif mode == "updates":
            # updates 模式: {node_name: state_update} 字典
            for node_name in chunk:
                label = _NODE_LABELS.get(node_name, node_name)
                yield _sse({"type": "node_update", "node": node_name, "label": label})

    # 流结束后,检查工作流状态
    # AsyncRedisSaver 的同步方法(get_tuple)在主线程被禁, 必须用异步接口 aget_state
    state = await graph.aget_state(config)
    if state.next and "human_approval" in state.next:
        # 被人工审批中断
        # 前端 ApprovalDialog 以 0-100 展示分数, 后端 evaluate_score 为 0-1, 这里换算
        raw_score = state.values.get("evaluate_score")
        score_display = round(float(raw_score) * 100, 1) if raw_score is not None else None
        query = state.values.get("input_text", "")
        draft = state.values.get("answer", "")
        yield _sse({
            "type": "interrupt",
            "approval": {"score": score_display, "query": query, "draft": draft},
        })
    else:
        # 正常结束,返回最终回答(附带评估置信分, 前端展示"置信章")
        answer = state.values.get("answer", "")
        # 方案B: 下发引用证据(图片缩略图 + 文本来源卡片), image_path 转前端可加载 URL
        evidence = []
        for e in state.values.get("evidence") or []:
            item = {
                "type": e.get("type") or "image",
                "filename": e.get("filename") or "",
                "indexes": e.get("indexes") or [],  # 引用它的 kb_context 编号列表, 供徽标跳转
            }
            if e.get("type") == "text":
                item["text"] = e.get("text") or ""
                item["label"] = e.get("label") or e.get("filename") or "文档"
                evidence.append(item)
            else:
                url = resolve_image_url(e.get("image_path"))
                if url:
                    item["url"] = url
                    evidence.append(item)
        raw_score = state.values.get("evaluate_score")
        score_display = round(float(raw_score) * 100, 1) if raw_score is not None else None
        yield _sse({"type": "done", "text": answer, "evidence": evidence, "score": score_display})


@router.post("/{session_id}/chat")
async def chat(
    session_id: str,
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对话接口,返回 SSE 流。

    接入真实 LangGraph workflow,流式返回节点执行进度与 token,
    若命中人工审批中断点则返回 interrupt 事件,等待 /approve 恢复。
    """
    session = await verify_session_owner(db, session_id, current_user.id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )

    if not req.text and not req.image:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text 和 image 至少需要提供一个",
        )

    logger.info("用户 {} 在会话 {} 发起对话: {}", current_user.username, session_id, (req.text or "")[:50])

    config = {"configurable": {"thread_id": session_id, "user_name": current_user.username}}

    # 输入模态由 input_text/input_image 决定, 无需单独传 input_type(process_input 本地派生)
    input_data = {
        "user": current_user.username,
        "session_id": session_id,
        "input_text": req.text or "",
        "input_image": req.image or "",
    }

    async def event_stream():
        # 持久化用户消息(流开始前先存,即使后续流失败也保留用户输入)
        # schema_v2: 输入图片 base64 → 落文件存引用, 不塞 JSON
        saved_image = await asyncio.to_thread(save_image_from_data_uri, req.image) if req.image else None
        await save_message(
            session_id, "human", req.text or "",
            images=[saved_image] if saved_image else [],
        )
        # 自动生成会话标题(仅首次对话,标题仍为"新会话"时触发)
        if req.text or req.image:
            new_title = await auto_title_session(
                db, session_id, current_user.id, req.text or "", req.image or None
            )
            if new_title:
                yield _sse({"type": "title_update", "title": new_title})
        try:
            async for chunk in _stream_graph_events(input_data, config):
                yield chunk
        except Exception as e:
            logger.exception("对话流执行异常: {}", e)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/{session_id}/approve")
async def approve(
    session_id: str,
    req: ApproveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """人机审批接口,接收 approve/reject 并驱动 workflow 恢复执行。

    更新 state 中的 human_answer 后,用 astream(None) 从中断点恢复。
    持久化(MySQL/Milvus/摘要)在恢复后的 persist_context 节点中完成。
    """
    session = await verify_session_owner(db, session_id, current_user.id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )

    if not isinstance(req.approved, bool):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="approved 必须是布尔值",
        )

    action = "approve" if req.approved else "reject"
    logger.info("用户 {} 在会话 {} 提交审批: {}, reason: {}", current_user.username, session_id, action, req.reason or "(无)")

    config = {"configurable": {"thread_id": session_id}}

    # 更新 state 中的 human_answer 和 human_reason
    # human_answer 驱动路由: approve -> persist_context, reject -> regenerate_node
    # human_reason 传递给 regenerate_node, 让它知道为什么草稿被驳回
    # AsyncRedisSaver 的同步方法在主线程被禁, 必须用异步接口 aupdate_state
    await graph.aupdate_state(config, {"human_answer": action, "human_reason": req.reason or ""})

    async def event_stream():
        try:
            async for chunk in _stream_graph_events(None, config):
                yield chunk
        except Exception as e:
            logger.exception("审批恢复流执行异常: {}", e)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
