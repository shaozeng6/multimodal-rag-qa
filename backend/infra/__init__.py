"""基础设施层(infra): LLM / Embedding / Milvus / DashScope 的初始化与配置。

从原 graph/llm_init.py 拆分(架构整理 2026-08):
- graph 只保留"对话图"职责(节点/状态/检索/上下文), 基础设施下沉到独立包
- 解决依赖倒挂: ingestion(入库管道)与 db(建集合)原先反向依赖 graph.llm_init,
  现统一依赖 infra; 模型/维度/限流等基础设施配置归属 .env, 不进配置中心
"""
