"""统一检索与后处理。

参考 ragent 的设计:
- 多通道并行(文本路 / 图片路 / 记忆路), 单通道失败降级为空结果不中断整体
- 后处理链: 去重(id 或 sha256(text)) → RRF 名次融合 → topK 截断
- 刻意不做绝对分数阈值: 跨通道/跨模型的相似度分数量纲不可比,
  名次(RRF)是相对稳定的信号, 相关性判断交给生成 LLM
- 所有阻塞调用(DashScope 限流含 time.sleep、Milvus 同步客户端)均包 asyncio.to_thread,
  避免阻塞 async 事件循环
"""
import ast
import asyncio
import hashlib
import json
from typing import Dict, List, Optional

from loguru import logger
from pymilvus import AnnSearchRequest, WeightedRanker

from graph.llm_init import (
    CONTEXT_COLLECTION_NAME,
    call_dashscope_once,
    embedding,
    m_re,
    milvus_client,
)
from services.config_service import get_float, get_int

# ========= 配置常量(集中管理; 运行时由 sys_config 覆盖, 常量仅作默认值兜底) =========
TEXT_TOPK = 6        # 文本路候选数
IMAGE_TOPK = 4       # 图片路候选数
MEMORY_TOPK = 3      # 记忆路候选数
CONTEXT_TOPK = 8     # 融合后进入生成器的上下文条数
RRF_K = 60           # RRF 平滑常数
MEMORY_WEIGHT = 0.8  # 记忆路 RRF 权重(低于知识库路)


def _text_topk() -> int:
    return get_int("retrieval.text_topk", TEXT_TOPK)


def _image_topk() -> int:
    return get_int("retrieval.image_topk", IMAGE_TOPK)


def _memory_topk() -> int:
    return get_int("retrieval.memory_topk", MEMORY_TOPK)


def _context_topk() -> int:
    return get_int("retrieval.context_topk", CONTEXT_TOPK)


def _rrf_k() -> int:
    return get_int("retrieval.rrf_k", RRF_K)


def _memory_weight() -> float:
    return get_float("retrieval.memory_weight", MEMORY_WEIGHT)

# ========= 阻塞调用包装 =========

async def _embed(input_data: list) -> Optional[List[float]]:
    """包到线程中调用 DashScope 多模态 embedding(限流器含 time.sleep)。"""
    try:
        ok, emb, status_code, retry_after = await asyncio.to_thread(call_dashscope_once, input_data)
        if ok and emb:
            return emb
        logger.warning("嵌入向量获取失败, status={}, code={}", status_code, retry_after)
        return None
    except Exception as e:
        logger.exception("嵌入调用异常: {}", e)
        return None


async def _search_doc_text(emb: List[float], query: str, limit: int) -> list:
    """t_doc 文本路: hybrid_search(稠密 + BM25) 在异步线程中执行。

    注意: 必须用关键字 limit= 传参! m_re.hybrid_search 的签名是
    (query_dense_embedding, query_sparse_embedding, sparse_weight=1.0, dense_weight=1.0, limit=10),
    limit 排在两个 weight 之后, 位置传参会把 limit 塞进 sparse_weight 导致
    WeightedRanker(6.0, 1.0) 越界报 "rank param weight should be in range [0, 1]"。
    """
    try:
        return await asyncio.to_thread(m_re.hybrid_search, emb, query, limit=limit)
    except Exception as e:
        logger.warning("知识库文本检索失败(可能集合为空或不存在): {}", e)
        return []


async def _search_doc_image(emb: List[float], limit: int) -> list:
    """t_doc 图片路: dense_search(以图搜图) 在异步线程中执行。"""
    try:
        return await asyncio.to_thread(m_re.dense_search, emb, limit)
    except Exception as e:
        logger.warning("知识库图片检索失败(可能集合为空或不存在): {}", e)
        return []


async def _search_context(query: str, limit: int, user_id: Optional[int] = None) -> list:
    """t_context 记忆路: 按「问题 + 回答」双字段 hybrid_search(按 user_id 隔离)。

    记忆条目存「问题 + 回答」对, 各自建稠密 + BM25 索引:
    - 问题路(question_dense / question_sparse): 主检 —— query 与问题同为"问题形态", 语义最对齐
    - 回答路(context_dense / context_sparse): 兜 recall —— 语义相关但字面与问题不重叠时命中
    四条路经 WeightedRanker 融合, 问题路权重更高; 命中后输出 question + context_text(答案)。

    关键: question_dense/context_dense 均由 persist_context 用 embedding.embed_query(OpenAIEmbeddings)
    写入, 检索必须用同一 embedding 模型生成查询向量, 否则向量空间不一致, 语义检索失效。
    user_id: 当前用户数字 id; 记忆库跨会话共享但**必须按用户隔离**(修 KNOWN_ISSUES #3 隐私泄漏)。
    写入侧把 user_id 存为字符串, 这里过滤 `user == "user_id"`(改名不影响归属)。
    """
    def _sync():
        try:
            dense_vec = embedding.embed_query(query)
        except Exception as e:
            logger.warning("记忆检索向量生成失败: {}", e)
            return []
        filter_expr = None
        if user_id is not None:
            filter_expr = 'user == "{}"'.format(user_id)
        dense_params = {"metric_type": "IP", "params": {"nprobe": 10}}
        bm25_params = {"metric_type": "BM25", "params": {"drop_ratio_search": 0.2}}
        reqs = [
            AnnSearchRequest([dense_vec], "question_dense", dense_params, limit=limit, expr=filter_expr),
            AnnSearchRequest([query], "question_sparse", bm25_params, limit=limit, expr=filter_expr),
            AnnSearchRequest([dense_vec], "context_dense", dense_params, limit=limit, expr=filter_expr),
            AnnSearchRequest([query], "context_sparse", bm25_params, limit=limit, expr=filter_expr),
        ]
        res = milvus_client.hybrid_search(
            collection_name=CONTEXT_COLLECTION_NAME,
            reqs=reqs,
            # 权重须在 [0,1] 且与 reqs 顺序一致; 问题路主检, 回答路兜底
            ranker=WeightedRanker(0.7, 0.7, 0.4, 0.4),
            limit=limit,
            output_fields=["question", "context_text"],
        )
        return res[0] if res else []

    try:
        return await asyncio.to_thread(_sync)
    except Exception as e:
        logger.warning("历史上下文检索失败(可能集合为空或不存在): {}", e)
        return []


# ========= 后处理链 =========

def _hit_entity(hit: dict) -> dict:
    """从命中结果中提取 entity 字段字典。

    Milvus 3.0 将 output_fields 序列化为 JSON 字符串返回(entity 字段是 str,
    形如 "{'text': '...', 'category': '...'}")。兼容三种结构:
    - dict(旧版 pymilvus/2.4 行为)
    - Python repr 字符串(当前 3.0 行为)
    - JSON 字符串(兜底)
    """
    ent = hit.get("entity")
    if isinstance(ent, dict):
        return ent
    if isinstance(ent, str):
        try:
            return ast.literal_eval(ent)
        except (ValueError, SyntaxError):
            try:
                return json.loads(ent)
            except json.JSONDecodeError:
                return {}
    return {}


def _hit_text(hit: dict) -> str:
    """提取命中的正文: 兼容 Milvus 2.x(字段在顶层)与 3.0(entity 字符串化)。"""
    ent = _hit_entity(hit)
    return str(
        hit.get("text")
        or hit.get("context_text")
        or ent.get("text")
        or ent.get("context_text")
        or ""
    )


def _hit_key(hit: dict) -> str:
    """去重键: 优先 id, 缺失时退化为 text 的 sha256(避免 String.hashCode 碰撞)。"""
    hit_id = hit.get("id") or hit.get("pk")
    if hit_id:
        return f"id:{hit_id}"
    return "sha:" + hashlib.sha256(_hit_text(hit).encode("utf-8")).hexdigest()


def _dedupe(hits: list) -> list:
    """去重: 保留首次出现, 保持名次顺序。"""
    seen = set()
    out = []
    for hit in hits:
        key = _hit_key(hit)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def _rrf_fuse(channels: List[list], k: int = RRF_K, weights: Optional[List[float]] = None) -> list:
    """RRF 名次融合: score = Σ w/(k+rank+1), 按名次而非绝对分数融合多路结果。"""
    key_to_hit: Dict[str, dict] = {}
    scores: Dict[str, float] = {}
    for ci, channel in enumerate(channels):
        weight = (weights[ci] if weights else 1.0) if ci < len(channels) else 1.0
        for rank, hit in enumerate(channel):
            key = _hit_key(hit)
            key_to_hit.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank + 1)

    fused = []
    for key, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        hit = dict(key_to_hit[key])
        hit["score"] = score
        fused.append(hit)
    return fused


# ========= 统一检索入口 =========

def _retrieval_plan(state) -> dict:
    """根据输入模态决定检索方案: 跑哪些路、跳过哪条。

    路由规则(直接看 input_text/input_image):
    - 有文本: 文本路 hybrid 搜 t_doc
    - 有图片: 图片路 dense 搜 t_doc(以图搜图)
    - 有历史且有问题: 记忆路 hybrid 搜 t_context(跨会话回忆)
    - P2: 图文 unrelated(图片纯附件)时跳过以图搜图, 否则无关图检索结果占满槽位;
          纯图(relation="") / related / contradictory 均保留图片路
    """
    input_text = state.get("input_text") or ""
    input_image = state.get("input_image") or ""
    image_relation = state.get("image_relation") or ""
    return {
        "query": state.get("rewritten_query") or input_text or "",
        "input_image": input_image,
        "has_text": bool(input_text),
        "has_history": bool(state.get("messages")),
        "skip_image_path": bool(input_image) and image_relation == "unrelated",
    }


async def _embed_for_plan(query: str, input_image: str, skip_image_path: bool):
    """并行生成查询向量(文本 + 图片, 按需), 任一路失败降级为 None 不阻断。"""
    tasks = []
    if query:
        tasks.append(("text", _embed([{"text": query}])))
    if input_image and not skip_image_path:
        tasks.append(("image", _embed([{"image": input_image}])))
    if not tasks:
        return None, None

    results = await asyncio.gather(*[t for _, t in tasks])
    text_emb = image_emb = None
    for (name, _), res in zip(tasks, results):
        if name == "text":
            text_emb = res
        else:
            image_emb = res
    return text_emb, image_emb


async def _search_channels(query: str, text_emb, image_emb, user_id: Optional[int] = None):
    """三路并行检索 + 各自去重, 返回 (channels, weights) 供 RRF 融合。

    - 文本路 / 图片路: 知识库 t_doc
    - 记忆路: t_context(跨会话回忆, 按 user 隔离)。只要有问题就搜, 不依赖本会话历史——
      否则新会话第一问永远回忆不到以前答过的内容。
      命中统一为 _memo_to_doc 结构并降权 MEMORY_WEIGHT。
    """
    # 候选数与记忆权重运行时读取(配置中心 hot 生效)
    text_topk = _text_topk()
    image_topk = _image_topk()
    memory_topk = _memory_topk()
    memory_weight = _memory_weight()

    tasks = []
    if text_emb and query:
        tasks.append(("text", _search_doc_text(text_emb, query, text_topk)))
    if image_emb:
        tasks.append(("image", _search_doc_image(image_emb, image_topk)))
    if query:
        # 记忆路按当前用户 id 隔离(修 KNOWN_ISSUES #3)
        tasks.append(("memory", _search_context(query, memory_topk, user_id=user_id)))
    if not tasks:
        return [], []

    results = await asyncio.gather(*[t for _, t in tasks], return_exceptions=True)
    channels, weights = [], []
    for (name, _), res in zip(tasks, results):
        if isinstance(res, Exception) or not res:
            logger.warning("[检索] {} 路: 空或失败, 跳过", name)
            continue
        if name == "memory":
            channels.append([_memo_to_doc(h) for h in _dedupe(res)])
            weights.append(memory_weight)
        else:
            channels.append(_dedupe(res))
            weights.append(1.0)
        logger.info("[检索] {} 路: 去重后 {} 条", name, len(channels[-1]))
    return channels, weights


def _build_context(fused: list):
    """把 RRF 融合结果拆成 kb_context(带元数据)与 kb_images(图片路径列表)。"""
    docs, images = [], []
    for hit in fused:
        ent = _hit_entity(hit)
        category = hit.get("category") or ent.get("category") or "unknown"
        image_path = hit.get("image_path") or ent.get("image_path")
        filename = hit.get("filename") or ent.get("filename")
        if category == "image" and image_path:
            images.append(image_path)
        docs.append({
            "text": _hit_text(hit),
            "category": category,
            "image_path": image_path,
            "filename": filename,
            "score": hit.get("score", 0),
        })
    return docs, images


async def unified_retrieve(state) -> dict:
    """统一检索: 决策 → 向量化 → 三路检索 → RRF 融合 → 拆包, 返回新 state 片段。"""
    plan = _retrieval_plan(state)
    logger.info("[检索] 开始: 有文本={}, 有图片={}(跳过图片路={}), 有历史={}, query={}",
                plan["has_text"], bool(plan["input_image"]), plan["skip_image_path"],
                plan["has_history"], (plan["query"] or "")[:50])

    # 1. 查询向量(文本 + 图片, 按需并行)
    text_emb, image_emb = await _embed_for_plan(plan["query"], plan["input_image"], plan["skip_image_path"])

    # 2. 三路并行检索 + 去重(单路失败降级为空)
    channels, weights = await _search_channels(
        plan["query"], text_emb, image_emb, user_id=state.get("user_id"),
    )

    # 3. RRF 名次融合(相对名次, 跨通道量纲不可比) + topK(运行时读取)
    fused = _rrf_fuse(channels, k=_rrf_k(), weights=weights) if channels else []
    fused = fused[:_context_topk()]

    # 4. 拆成上下文列表与图片路径列表
    docs, images = _build_context(fused)

    logger.info("[检索] 完成: 融合 {} 条 → 上下文 {} 条 / 图片 {} 张",
                len(fused), len(docs), len(images))
    return {
        "kb_context": docs,
        "kb_images": images,
        "retrieval_ok": bool(fused),
    }


def _memo_to_doc(hit: dict) -> dict:
    """把 t_context 命中(hit 结构)统一为与文档路一致的结构。

    text 带上前置问题(问/答形式), 让生成器知道这条记忆的来由; 无问题时只给答案。
    """
    ent = _hit_entity(hit)
    answer = hit.get("context_text") or ent.get("context_text") or ""
    question = hit.get("question") or ent.get("question") or ""
    text = f"问: {question}\n答: {answer}" if question else answer
    return {"text": text, "category": "memory", "image_path": None, "filename": None}
