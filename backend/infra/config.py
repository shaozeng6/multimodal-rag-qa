"""基础设施配置常量(从 graph/llm_init.py 拆分, 2026-08 架构整理)。

模型/温度/限流属基础设施配置, 归 .env(core/config.settings / os.getenv), 不进配置中心。
core.config 已 load_dotenv(), 故 .env 里的 LLM_MODEL 等对 os.getenv 同样生效(重启后)。
"""
import os

from core.config import settings

# 主 LLM 配置
LLM_BASE_URL = settings.LLM_BASE_URL
LLM_API_KEY = settings.LLM_API_KEY
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.7-plus")
MULTIMODAL_LLM_MODEL = os.getenv("MULTIMODAL_LLM_MODEL", "qwen3-vl-plus")

# Embedding 配置
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))

# DashScope 多模态 embedding(图像/文本向量化)，优先使用专用 key，回退到主 LLM key
DASHSCOPE_EMBEDDING_MODEL = os.getenv("DASHSCOPE_EMBEDDING_MODEL", "multimodal-embedding-v1")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY") or os.getenv("ALIBABA_API_KEY") or settings.LLM_API_KEY

# 评估/改写 LLM 配置(关闭 thinking，避免与 ragas 的 n>1 调用冲突)
EVAL_LLM_MODEL = os.getenv("EVAL_LLM_MODEL", "qwen3.7-plus")
# 评审 LLM(LLM as Judge, 独立于生成模型消除"自评自答"同源偏置; 生产建议不同系列)
JUDGE_LLM_MODEL = os.getenv("JUDGE_LLM_MODEL", EVAL_LLM_MODEL)

# Milvus 配置(企业版从 settings.MILVUS_URI 读取)
MILVUS_URI = settings.MILVUS_URI
COLLECTION_NAME = os.getenv("MILVUS_COLLECTION_NAME", "t_doc_collection")
CONTEXT_COLLECTION_NAME = os.getenv("MILVUS_CONTEXT_COLLECTION_NAME", "t_context_collection")

# DashScope 限流配置
RPM_LIMIT = int(os.getenv("DASHSCOPE_RPM_LIMIT", "120"))
WINDOW_SECONDS = int(os.getenv("DASHSCOPE_WINDOW_SECONDS", "60"))
MAX_IMAGE_BYTES = int(os.getenv("DASHSCOPE_MAX_IMAGE_BYTES", str(3 * 1024 * 1024)))
