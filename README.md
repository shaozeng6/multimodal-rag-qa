# 多模态 RAG 知识库问答系统

**Multimodal RAG Q&A System**

A multimodal Retrieval-Augmented Generation (RAG) Q&A system built on **FastAPI + LangGraph + Milvus + Vue3**. It supports hybrid text & image retrieval with image-caption bridging, LLM-as-Judge answer evaluation with human-in-the-loop approval, multi-turn context management, and a full ingestion pipeline with OCR. Backend streaming answers over SSE to a Vue3 chat interface.

> 中文文档为主;英文简介见上。项目开发过程中的审计 / 已知问题 / 流程等文档为私有资料,未包含在本仓库中。

---

## 目录

- [项目简介](#项目简介)
- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [多模态检索设计](#多模态检索设计)
- [技术栈](#技术栈)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [环境变量](#环境变量)
- [API 概览](#api-概览)
- [生产部署注意](#生产部署注意)
- [License](#license)

## 项目简介

一个**多模态知识库问答系统**:支持以文本、图片(或图文混合)提问,对知识库文档(PDF/图片/表格)进行检索增强生成。系统以 LangGraph 状态机编排完整链路,由 LLM-as-Judge 对回答进行质量评估,低置信回答进入人工审批。

核心差异化能力:**图片语义桥接**(caption 让文本检索理解图片)、**三路并行召回 + RRF 融合**、**图文一致性校验**(识别用户描述与图片的矛盾)、**跨会话记忆检索**。

## 核心特性

- **多模态输入与检索**:`image_analysis` 节点对图片生成 caption 桥接文本语义;文本路 / 图片路 / 记忆路三路并行召回,RRF 名次融合去重;图文关系一致性评估,避免无关图片污染检索。
- **LangGraph 图工作流**:输入 → 图片分析 → 查询改写 → 统一检索 → 生成 → LLM 评审 →(人工审批)→ 持久化;Redis checkpointer 支持**断点续跑**与多轮上下文管理。
- **LLM-as-Judge 评估与人工审批**:独立评审模型打分(消除自评偏置),低于 `EVALUATE_THRESHOLD` 的回答进入人工审批,可审批通过或驳回重生成。
- **上下文管理**:多轮滑动窗口 + 超长自动摘要压缩;跨会话记忆检索(`t_context`),仅高质量回答进入记忆。
- **入库管道**:PDF 解析(PyMuPDF)+ vllm `dots_ocr` 图像 OCR + 智能分块 + 多模态向量化,MySQL + Milvus 双库写入,异步任务 + 进度查询。
- **可观测性**:SSE 流式输出 + 节点执行链路可视化;`turn_trace` 全链路中间过程落库(`message_traces`),只写不回流,可审计、可回放。

## 系统架构

```
用户输入(文本 / 图片)
   ▼
process_input ──有图──▶ image_analysis     图→caption + 图文一致性
   │ 无图                          │
   ▼                              ▼
query_rewriter     指代消解 / 改写(按模态选源)
   ▼
unified_retrieve   文本路 + 图片路 + 记忆路(RRF 名次融合)
   ▼
generator_node     LLM 生成(输入图 + 检索图 + 历史图进模型)
   ▼
evaluate_node      LLM-as-Judge 评分
   ├─ ≥ 阈值 ─▶ persist_context ─▶ SSE done 事件
   └─ < 阈值 ─▶ human_approval(人工审批中断)
                  ├─ 通过 ─▶ persist_context
                  └─ 驳回 ─▶ regenerate_node ─▶ persist_context
```

- **持久化**:`persist_context` 将回答、图片引用、引用证据、中间 trace 写入 MySQL,高质量回答写入 Milvus 记忆。
- **流式**:后端通过 SSE 下发 `token` / `node_update` / `interrupt` / `done` / `error` 等事件,前端实现打字机效果与审批弹窗。
- **中断恢复**:审批中断点由 Redis checkpointer 保存执行状态,`/approve` 恢复后从中断处继续。

## 多模态检索设计

系统遵循**「入库配方 = 检索配方」对称性原则**,避免图文向量语义错位:

| 通道 | 入库配方 | 检索配方 |
|---|---|---|
| 图→图(以图搜图) | 图片纯视觉向量 | 图片纯视觉向量 |
| 文→图(关键词级) | 图片描述进 `text` → BM25 sparse | 文本 query 走 BM25 |
| 图→文(caption) | 文字文档正常入库 | caption 当文本 query 走文本通道 |

- **caption 桥接**:图片输入经 VL 模型生成描述,作为文本 query 检索知识库,解决「以图找文」。
- **图文一致性**:检测用户文字与图片是否矛盾(`related` / `unrelated` / `contradictory` 三态);矛盾时以图片为准,单独用 caption 检索,避免错误描述污染。
- **记忆检索**:跨会话的过往高质量问答进入 `t_context`,检索时并入统一召回结果并带来源标签。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python / FastAPI / SQLAlchemy(async) / LangGraph / LangChain / DashScope embedding / pymilvus |
| 前端 | Vue3 / Vite / TypeScript / Element Plus / Pinia |
| 存储 | MySQL(业务数据)、Milvus(向量)、Redis(graph checkpointer) |
| 依赖服务 | OpenAI 兼容 LLM 服务(默认 `localhost:8000/v1`);vllm + `dots_ocr`(图像 OCR,可选) |

## 目录结构

```
backend/
  api/         路由层(auth / chat / knowledge / sessions / files)
  core/        配置、依赖注入、JWT 安全
  db/          MySQL 连接与 schema_v2.sql(表结构参考)
  graph/       LangGraph 工作流节点(检索 / 生成 / 评估 / 持久化 / 图片分析)
  ingestion/   入库管道(PDF / OCR / 分块 / 向量化 / 任务)
  models/      SQLAlchemy ORM 模型
  services/    业务服务层(认证 / 会话 / 消息 / 图片存储)
  main.py      FastAPI 应用入口
frontend/
  src/views/   页面
  src/api/     axios 封装(/api 代理到后端)
  vite.config.ts  dev 代理:/api、/uploads → http://localhost:8001
```

## 快速开始

### 前置依赖

| 依赖 | 说明 |
|---|---|
| MySQL | 业务数据(会话 / 消息 / 入库任务)。首次启动自动建表(create_all + 幂等迁移) |
| Redis | **需 Redis 8.0+ 或 Redis Stack**(RediSearch + RedisJSON 模块),用于 LangGraph checkpointer |
| Milvus | 向量库。`MILVUS_URI` 可指向独立服务,或默认本地文件(Milvus Lite) |
| LLM 服务 | 任意 OpenAI 兼容端点(默认 `http://localhost:8000/v1`),建议配置独立评审模型 `JUDGE_LLM_MODEL` |
| vllm + dots_ocr | 图像 OCR,**可选**;未启动时入库管道走 PyMuPDF 基础解析 |

### 后端启动

```bash
cd backend
cp .env.example .env      # 按需修改数据库 / LLM / Redis / OCR 配置
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

### 前端启动

```bash
cd frontend
npm install
npm run dev               # http://localhost:5173
```

### 首次使用

首次启动会自动创建管理员账号:

```
用户名: admin
密码:   admin123
```

> ⚠️ 生产环境请在首次登录后**立即修改密码**,并同步修改 `.env` 中的 `JWT_SECRET` 等默认凭据。

## 环境变量

所有配置集中在 `backend/.env`(模板见 `backend/.env.example`):

| 类别 | 变量 | 说明 |
|---|---|---|
| 存储 | `MYSQL_URL` | MySQL 连接串 |
| | `REDIS_URL` | Redis 连接串(checkpointer) |
| | `MILVUS_URI` | Milvus 地址(服务或本地文件) |
| 认证 | `JWT_SECRET` / `JWT_ALGORITHM` / `JWT_EXPIRE_MINUTES` | JWT 签发与有效期 |
| LLM | `LLM_BASE_URL` / `LLM_API_KEY` | 生成模型端点 |
| | `JUDGE_LLM_MODEL` | 独立评审模型(生产建议与生成模型不同系列) |
| RAG 工作流 | `RAG_WINDOW_TURNS` / `RAG_SUMMARY_TRIGGER_TURNS` / `RAG_SUMMARY_MAX_CHARS` | 上下文窗口与摘要压缩 |
| | `EVALUATE_THRESHOLD` | 评估通过阈值(低于则人工审批) |
| | `RAG_TERM_MAPPING_PATH` | 术语归一化映射表(JSON) |
| 入库管道 | `OCR_VLLM_IP` / `OCR_VLLM_PORT` / `OCR_VLLM_MODEL` | OCR 服务 |
| | `INGEST_OUTPUT_DIR` / `INGEST_IMAGES_DIR` / `INGEST_TMP_DIR` | 中间产物目录 |
| | `INGEST_OCR_THREADS` / `INGEST_OCR_DPI` | OCR 并发与分辨率 |
| 图片存储 | `UPLOAD_IMAGES_DIR` | 消息图片落盘目录(经 `/uploads` 静态暴露) |
| | `KB_IMAGE_ROOTS` | 额外允许 `/api/files` 提供的图片根目录(分号分隔) |
| 其他 | `CORS_ORIGINS` | 允许的前端来源(逗号分隔) |

## API 概览

所有接口统一前缀 `/api`:

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 登录,返回 JWT |
| GET | `/auth/me` | 当前登录用户 |
| GET | `/sessions` | 会话列表 |
| POST | `/sessions` | 新建会话 |
| GET | `/sessions/{id}/messages` | 会话历史消息(含图片与引用证据) |
| PATCH | `/sessions/{id}` | 重命名会话 |
| DELETE | `/sessions/{id}` | 删除会话(同时清理 Redis checkpointer) |
| POST | `/sessions/{id}/chat` | 对话(SSE 流式) |
| POST | `/sessions/{id}/approve` | 人工审批(通过 / 驳回重生成) |
| POST | `/knowledge/upload` | 上传 PDF 触引入库任务 |
| GET | `/knowledge/jobs` | 入库任务列表 |
| GET | `/knowledge/jobs/{id}` | 入库任务状态 |
| GET | `/knowledge/status` | 知识库统计(文档数 / 向量数) |
| GET | `/files` | 图片服务(带路径穿越校验) |
| GET | `/health` | 健康检查 |

### 对话流 SSE 事件

`/chat` 与 `/approve` 返回 `text/event-stream`,事件体为 `{ type, ... }`:

| 事件 | 说明 |
|---|---|
| `token` | LLM 流式 token(打字机效果) |
| `node_update` | 节点执行进度(前端展示执行链路) |
| `title_update` | 首轮自动生成会话标题 |
| `interrupt` | 命中人工审批(携带草稿答案与评分) |
| `done` | 回答完成(携带最终文本、引用证据、置信分) |
| `error` | 执行异常 |

## 生产部署注意

- **Redis**:checkpointer 依赖 RediSearch + RedisJSON,须使用 **Redis 8.0+ 或 Redis Stack**;生产建议开启持久化(RDB/AOF),否则服务重启会丢失工作流状态与中断进度。Redis 不可用时自动降级为 `InMemorySaver`(仅开发用)。
- **凭据**:`.env` 中 `JWT_SECRET`、`LLM_API_KEY`、`MYSQL_URL` 的默认值仅用于本地开发,上线前必须替换;首次启动自动创建的 `admin/admin123` 请立即改密。
- **数据库**:表结构首次启动自动创建;`backend/db/schema_v2.sql` 提供参考 DDL。
- **评估模型**:生产建议 `JUDGE_LLM_MODEL` 与生成模型**不同系列**,避免自评偏置。
- **图片目录**:`/uploads` 与 `/api/files` 对外暴露图片文件,部署时注意访问权限与 `KB_IMAGE_ROOTS` 配置范围。

## License

本仓库暂未指定 License。
