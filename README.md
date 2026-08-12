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
- **知识库管理**:管理页支持上传 PDF、任务进度轮询、已入库文档列表(搜索 / 分页)、点击文档下钻查看 chunk 明细、删除文档(按 Milvus 主键精确删,同名重复上传互不影响)。
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

> **首次部署需初始化 Milvus 集合**(幂等,已存在则跳过;`--force` 删除重建会清空数据):
>
> ```bash
> cd backend && python -m db.milvus_setup
> ```

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

所有**基础设施**配置集中在 `backend/.env`(模板见 `backend/.env.example`)。

> ⚙️ **运行参数已迁移到「系统设置」页**(schema_v3 配置中心, 2026-08-11):召回 topK、分片字符数、
> 评估阈值、模型型号/温度、上下文窗口等**不再由 .env/代码控制**, 改为管理员在系统设置页可视化配置,
> 落库 `sys_config` 表并热加载生效(hot 组保存即生效, model 组重启生效)。见下文「系统设置」。

| 类别 | 变量 | 说明 |
|---|---|---|
| 存储 | `MYSQL_URL` | MySQL 连接串 |
| | `REDIS_URL` | Redis 连接串(checkpointer) |
| | `MILVUS_URI` | Milvus 地址(服务或本地文件) |
| 认证 | `JWT_SECRET` / `JWT_ALGORITHM` / `JWT_EXPIRE_MINUTES` | JWT 签发与有效期 |
| LLM | `LLM_BASE_URL` / `LLM_API_KEY` | 生成模型端点 |
| 入库管道 | `OCR_VLLM_IP` / `OCR_VLLM_PORT` / `OCR_VLLM_MODEL` | OCR 服务 |
| 入库管道 | `OCR_VLLM_IP` / `OCR_VLLM_PORT` / `OCR_VLLM_MODEL` | OCR 服务 |
| | `INGEST_OUTPUT_DIR` / `INGEST_IMAGES_DIR` / `INGEST_TMP_DIR` | 中间产物目录 |
| | `INGEST_OCR_THREADS` / `INGEST_OCR_DPI` | OCR 并发与分辨率 |
| 图片存储 | `UPLOAD_IMAGES_DIR` | 消息图片落盘目录(经 `/uploads` 静态暴露) |
| | `KB_IMAGE_ROOTS` | 额外允许 `/api/files` 提供的图片根目录(分号分隔) |
| 其他 | `CORS_ORIGINS` | 允许的前端来源(逗号分隔) |

## 系统设置(管理员)

管理员顶栏「系统设置」页(schema_v3)提供运行参数的可视化配置, 落库 `sys_config` 表:

- **分组**:入库 / 检索 / 评估 / 上下文 / 图片上限, 组内按逻辑子域(分片/向量化/候选数/融合/阈值…)
  分组渲染, 按组保存与恢复默认值。
- **生效方式**:全部「即时生效」(保存即写入并热加载)。
- **可视化配置边界(勿过度)**:只收录真正需要调参且已接线的运行参数——检索召回(topK)、评估阈值、
  分片字符数、上下文窗口、图片上限。**模型型号/温度/维度、OCR 线程/DPI、限流等基础设施配置归属 `.env`**
  (重启级, 见上节), 不在设置页。
- **实现**:`backend/models/config.py` + `backend/services/config_service.py`(内存缓存+懒加载),
  默认值单一事实来源在 `backend/core/config_defaults.py`, 新增配置项在此登记。

## 知识库管理(管理员)

「知识库管理」页除上传/文档/chunk 外, 现支持:

- **文件列表 = 已入库文档 + 进行中任务**:上传的文件**直接出现在文件列表**, 进行中/失败/已取消的任务行内嵌**进度条 + 管道步骤条**(解析:上传→OCR→分片 ‖ 索引:描述→向量化→入库, 分阶段显示完成/进行中/待执行)与阶段文案, 无需单独的"入库任务"页。
- **两段式管道(可复用)**:解析(OCR/分片)产物 **chunks.json 落盘**, 索引(描述/向量化/入库)从落盘读取——**换嵌入模型/重跑索引不必重新 OCR/分片**; 失败重试自动只重跑失败段(索引失败重试跳过解析)。
- **手动控制(分步)**:上传时勾选「手动控制(分步)」, 上传后**停在列表不做任何处理**, 分别点「**解析**」(OCR/分片) → 完成后点「**入库**」(描述/向量化/写入) 两段手动执行; 解析完成停在「待入库」, 可一直放着。
- **行内操作**:进行中任务可**暂停**(阶段边界+向量化循环内逐条)、**继续**、**取消**; 失败/已取消任务可**删除**(清记录+中间产物)或**重试**(复用原 job_id, 只重跑失败段); 每行可看**任务日志**。
- **先不处理**:上传时勾选「上传后先不处理」, 任务停在首阶段(如先只上传、暂不 OCR), 之后在列表点「继续」。
- **中间数据清理**:成功/取消后自动清理该任务 OCR md 目录与临时 PDF; 图片目录(md5 共享)保留。
- **文档统计**:文档列表与详情展示 chunk 数、图片数、**字符数**(文本块字符数展示)、源文件大小;
  按状态/关键字筛选; 文档内每个 chunk 展示字符数。
- **知识库统计条**:文档数 / 向量数 / 总字符数 / 失败任务数 / Milvus 状态。

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
| GET | `/knowledge/jobs` | 入库任务列表(按状态筛选) |
| GET | `/knowledge/jobs/{id}` | 入库任务状态(含进度 / 阶段明细 / 日志) |
| POST | `/knowledge/jobs/{id}/retry` | 重试失败入库任务(复用原 job_id) |
| POST | `/knowledge/jobs/{id}/cancel` | 取消 running/pending 任务(协作式) |
| GET | `/knowledge/status` | 知识库统计(文档数 / 向量数 / 字符数 / 失败任务) |
| GET | `/knowledge/documents` | 已入库文档分页列表(搜索 / 状态 / 类型筛选) |
| GET | `/knowledge/documents/{id}/chunks` | 某文档的 chunk 明细(文本 / 图片, 含字符数) |
| DELETE | `/knowledge/documents/{id}` | 删除文档(含 Milvus 向量与磁盘产物) |
| GET | `/config` | 系统设置:分组返回全部运行参数(仅管理员) |
| PUT | `/config` | 批量更新运行参数(仅管理员) |
| POST | `/config/{group}/reset` | 恢复某组默认值(仅管理员) |
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

## 工程化工具

静态检查与格式化(不跑业务测试, 提交/改动前建议先过一遍):

```bash
# 后端: Python lint(ruff, 查未用导入/未定义/常见 bug) + 格式化
cd backend && ruff check . && ruff format --check .

# 前端: ESLint(lint) + Prettier(格式) + vue-tsc(类型)
cd frontend && npm run lint && npm run format:check && npm run type-check
```

- 后端规则集中在 `backend/pyproject.toml`(`dots_ocr` 第三方库排除)。
- 前端规则在 `frontend/.eslintrc.cjs` + `frontend/.prettierrc`;自动修复用 `npm run lint:fix` / `npm run format`。
- 改动代码后保持上述命令通过;CI(如后续接入)会在此基础上自动门禁。

## 生产部署注意

- **Redis**:checkpointer 依赖 RediSearch + RedisJSON,须使用 **Redis 8.0+ 或 Redis Stack**;生产建议开启持久化(RDB/AOF),否则服务重启会丢失工作流状态与中断进度。Redis 不可用时自动降级为 `InMemorySaver`(仅开发用)。
- **凭据**:`.env` 中 `JWT_SECRET`、`LLM_API_KEY`、`MYSQL_URL` 的默认值仅用于本地开发,上线前必须替换;首次启动自动创建的 `admin/admin123` 请立即改密。
- **数据库**:表结构首次启动自动创建;`backend/db/schema_v2.sql` 提供参考 DDL。
- **评估模型**:生产建议评审模型(`model.judge`,系统设置页配置)与生成模型**不同系列**,避免自评偏置。
- **图片目录**:`/uploads` 与 `/api/files` 对外暴露图片文件,部署时注意访问权限与 `KB_IMAGE_ROOTS` 配置范围。

## License

本仓库暂未指定 License。
