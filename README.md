# 多模态 RAG 知识库问答系统

**Multimodal RAG Enterprise Q&A System**

A multimodal Retrieval-Augmented Generation (RAG) enterprise Q&A system built on **FastAPI + LangGraph + Milvus + Vue3**. It supports hybrid text & image retrieval with image-caption bridging, LLM-as-Judge answer evaluation with human-in-the-loop approval, multi-turn context management, and a full ingestion pipeline with OCR. Backend streaming answers over SSE to a Vue3 chat interface.

> 中文说明为主；英文简介见上。项目开发过程的审计/已知问题等文档为私有资料，未包含在本仓库中。

---

## 功能特性

- **多模态检索**：`image_analysis` 节点对图片生成 caption 桥接文本语义；文本路 / 图片路 / 记忆路三路并行召回，RRF 名次融合去重；图文关系一致性评估避免无关图片污染检索。
- **图工作流**：LangGraph 状态机驱动——输入 → 图片分析 → 查询改写 → 统一检索 → 生成 → LLM 评审 →（人工审批）→ 持久化；Redis checkpointer 支持断点续跑与跨会话记忆。
- **评估与人工审批**：LLM-as-Judge 用独立评审模型打分，低于阈值（`EVALUATE_THRESHOLD`）的回答进入人工审批，可要求重生成。
- **上下文管理**：多轮滑动窗口 + 超长自动摘要压缩；跨会话记忆检索（`t_context`）。
- **入库管道**：PDF 解析（PyMuPDF）+ vllm `dots_ocr` 图像 OCR + 智能分块 + 多模态向量化，MySQL + Milvus 双库写入。
- **多轮对话与追踪**：SSE 流式输出；会话 / 知识库 / 认证管理；`turn_trace` 全链路落库可观测。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python / FastAPI / SQLAlchemy(async) / LangGraph / LangChain / DashScope embedding / pymilvus |
| 前端 | Vue3 / Vite / TypeScript / Element Plus / Pinia |
| 存储 | MySQL（业务数据）、Milvus（向量）、Redis（graph checkpointer） |
| 依赖服务 | OpenAI 兼容 LLM 服务（默认 `localhost:8000/v1`）；vllm + `dots_ocr`（图像 OCR，可选） |

## 架构流程

```
用户 → /api/chat(SSE)
   → process_input ──有图──→ image_analysis（生成 caption / 图文一致性）
        │
        └──无图──→ query_rewriter（改写）
                     → unified_retrieve（文本路 + 图片路 + 记忆路，RRF 融合）
                     → generator_node（LLM 生成）
                     → evaluate_node（LLM-as-Judge 评分）
                          → ≥阈值 → persist_context → 应答
                          → <阈值 → human_approval ──通过──→ persist_context
                                          └─重生成─→ regenerate_node
```

## 目录结构

```
backend/
  api/         路由层（auth / chat / knowledge / sessions / files）
  core/        配置、依赖注入、JWT 安全
  db/          MySQL 连接与 schema_v2.sql
  graph/       LangGraph 工作流节点（检索 / 生成 / 评估 / 持久化 / 图片分析）
  ingestion/   入库管道（PDF / OCR / 分块 / 向量化 / 任务）
  models/      SQLAlchemy ORM 模型
  services/    业务服务层（认证 / 会话 / 消息 / 图片存储）
  main.py      FastAPI 应用入口
frontend/
  src/views/   页面
  src/api/     axios 封装（/api 代理到后端）
  vite.config.ts  dev 代理：/api、/uploads → http://localhost:8001
```

## 快速开始

前置依赖：MySQL、Redis、Milvus 已启动；一个 OpenAI 兼容的 LLM 服务（图像 OCR 为可选）。

```bash
# 1. 后端
cd backend
cp .env.example .env      # 按需修改数据库 / LLM / OCR 配置
pip install -r requirements.txt
uvicorn main:app --reload --port 8001

# 2. 前端（新终端）
cd frontend
npm install
npm run dev               # http://localhost:5173
```

## 环境变量

所有配置集中在 `backend/.env`，模板见 `backend/.env.example`，涵盖：

- **存储**：`MYSQL_URL`、`REDIS_URL`、`MILVUS_URI`
- **LLM**：`LLM_BASE_URL`、`LLM_API_KEY`、评审模型 `JUDGE_LLM_MODEL`
- **RAG 工作流**：窗口轮数、摘要触发、评估阈值 `EVALUATE_THRESHOLD`
- **入库管道**：vllm OCR 地址、并发线程、输出目录
- **图片存储**：`UPLOAD_IMAGES_DIR`（经 `/uploads` 静态暴露）

## License

本仓库暂未指定 License。
