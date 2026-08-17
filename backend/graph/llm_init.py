"""LLM、Embedding、Milvus、工具初始化(从原项目 my_llm.py / embeddings_utils.py /
collections_operator.py / db_retriever.py / tools.py 抽离并适配企业版配置)。

所有配置从环境变量 / core.config.settings 读取，不硬编码。
"""
import os
import threading
import time
from http import HTTPStatus
from typing import Dict, List, Optional, Tuple

import dashscope
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from loguru import logger
from pymilvus import AnnSearchRequest, MilvusClient, WeightedRanker

from core.config import settings

# ========= 配置区 =========
# 模型/温度/限流属基础设施配置, 归 .env(core/config.settings / os.getenv), 不进配置中心。
# core.config 已 load_dotenv(), 故 .env 里的 LLM_MODEL 等对 os.getenv 同样生效(重启后)。
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

# 评估 LLM 配置(关闭 thinking，避免与 ragas 的 n>1 调用冲突)
EVAL_LLM_MODEL = os.getenv("EVAL_LLM_MODEL", "qwen3.7-plus")

# Milvus 配置(企业版从 settings.MILVUS_URI 读取)
MILVUS_URI = settings.MILVUS_URI
COLLECTION_NAME = os.getenv("MILVUS_COLLECTION_NAME", "t_doc_collection")
CONTEXT_COLLECTION_NAME = os.getenv("MILVUS_CONTEXT_COLLECTION_NAME", "t_context_collection")

# DashScope 限流配置
RPM_LIMIT = int(os.getenv("DASHSCOPE_RPM_LIMIT", "120"))
WINDOW_SECONDS = int(os.getenv("DASHSCOPE_WINDOW_SECONDS", "60"))
MAX_IMAGE_BYTES = int(os.getenv("DASHSCOPE_MAX_IMAGE_BYTES", str(3 * 1024 * 1024)))


# ========= LLM 实例(统一工厂收敛, 消除四处分散的 ChatOpenAI 构造) =========
def _make_llm(model: str, *, temperature: float = 0.2, streaming: bool = True,
              enable_thinking: Optional[bool] = None) -> ChatOpenAI:
    """按配置创建 ChatOpenAI 实例。

    Args:
        model: 模型名
        temperature: 采样温度
        streaming: 是否流式(生成节点需要, 改写/摘要/评审关闭)
        enable_thinking: 是否开启 thinking; None 表示不传 extra_body(保持模型默认)
    """
    kwargs = dict(
        model=model,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        temperature=temperature,
        streaming=streaming,
    )
    if enable_thinking is not None:
        kwargs["extra_body"] = {"enable_thinking": enable_thinking}
    return ChatOpenAI(**kwargs)


# 主对话 LLM(流式, 生成回答; 供 ingestion/convert 的表格描述等通用文本任务)
# 温度属基础设施配置, 固定默认值
llm = _make_llm(LLM_MODEL, temperature=0.2, streaming=True)

# 多模态大模型(对话/图片理解/重生成; 保持原默认温度 0.7 不变)
multiModal_llm = _make_llm(MULTIMODAL_LLM_MODEL, temperature=0.7, streaming=True)

# 改写/摘要专用 LLM(Fast 档: 不流式, 低温, 关 thinking; 供 query_rewriter 与摘要压缩)
rewriter_llm = _make_llm(EVAL_LLM_MODEL, temperature=0.1, streaming=False, enable_thinking=False)

# 评审 LLM(LLM as Judge, 用于纯文本回答的评估)
# 独立于生成模型(qwen3-vl-plus), 消除"自评自答"的同源偏置。
# 生产环境建议配置为与生成模型不同系列/不同供应商的模型。
# 注意: 有图片输入时评审仍需多模态, 走 multiModal_llm(见 nodes.py 的选取逻辑)。
JUDGE_LLM_MODEL = os.getenv("JUDGE_LLM_MODEL", EVAL_LLM_MODEL)
judge_llm = _make_llm(JUDGE_LLM_MODEL, temperature=0, streaming=False, enable_thinking=False)

embedding = OpenAIEmbeddings(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    model=EMBEDDING_MODEL,
    dimensions=EMBEDDING_DIMENSIONS,
    check_embedding_ctx_length=False,  # 关键参数
)

# ========= Milvus 客户端与检索器 =========
milvus_client = MilvusClient(uri=MILVUS_URI)


class MilvusRetriever:
    """Milvus 检索器：稠密检索 + 混合检索。"""

    def __init__(self, collection_name: str, milvus_client: MilvusClient, top_k: int = 3):
        self.collection_name = collection_name
        self.milvus_client = milvus_client
        self.top_k = top_k

    def dense_search(self, query_dense_embedding, limit=10):
        search_params = {"metric_type": "IP", "params": {"nprobe": 10}}
        res = self.milvus_client.search(
            collection_name=self.collection_name,
            data=[query_dense_embedding],
            anns_field="dense",
            limit=limit,
            output_fields=["text", 'category', 'filename', 'image_path', 'title'],
            search_params=search_params,
        )
        return res[0]

    def hybrid_search(
            self,
            query_dense_embedding,
            query_sparse_embedding,
            sparse_weight=1.0,
            dense_weight=1.0,
            limit=10,
    ):
        filter_expr = None
        dense_search_params = {"metric_type": "IP", "params": {"nprobe": 10}}
        dense_req = AnnSearchRequest(
            [query_dense_embedding], "dense", dense_search_params, limit=limit, expr=filter_expr
        )
        sparse_search_params = {"metric_type": "BM25", 'params': {'drop_ratio_search': 0.2}}
        sparse_req = AnnSearchRequest(
            [query_sparse_embedding], "sparse", sparse_search_params, limit=limit, expr=filter_expr
        )
        rerank = WeightedRanker(sparse_weight, dense_weight)
        return self.milvus_client.hybrid_search(
            collection_name=self.collection_name,
            reqs=[sparse_req, dense_req],
            ranker=rerank,  # 重排算法
            limit=limit,
            output_fields=["text", 'category', 'filename', 'image_path', 'title'],
        )[0]


# 全局检索器实例
m_re = MilvusRetriever(COLLECTION_NAME, milvus_client)


# ========= Embedding 工具(达摩院多模态嵌入，从 embeddings_utils.py 抽离) =========
class FixedWindowRateLimiter:
    """固定窗口速率限制器类，用于控制 API 调用频率。

    线程安全(C2 修复): acquire 可能被多个线程并发调用(检索侧 asyncio.to_thread
    并行 _embed、入库侧 daemon 线程), count/window_start 的读改写必须互斥,
    否则同一窗口会超发请求或重复等待。等待期间持有锁, 并发调用被串行化,
    总速率严格不超过 limit/window。
    """

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self.window_start = time.monotonic()  # 当前时间窗口的开始时间
        self.count = 0  # 当前时间窗口内的请求计数
        self._lock = threading.Lock()

    def acquire(self):
        """获取请求许可，如果需要会阻塞直到可以继续请求"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.window_start

            if elapsed >= self.window_seconds:
                self.window_start = now
                self.count = 0

            if self.count >= self.limit:
                sleep_sec = self.window_seconds - elapsed
                if sleep_sec > 0:
                    logger.info("DashScope 限速：达到 {} 次请求，等待 {:.2f}s", self.limit, sleep_sec)
                    time.sleep(sleep_sec)
                self.window_start = time.monotonic()
                self.count = 0

            self.count += 1


limiter = FixedWindowRateLimiter(RPM_LIMIT, WINDOW_SECONDS)


def image_to_base64(img: str) -> Tuple[str, str]:
    """将图片转换为base64编码"""
    try:
        import base64
        import mimetypes
        mime = mimetypes.guess_type(img)[0] or "image/png"
        with open(img, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        api_img = f"data:{mime};base64,{b64}"
        return api_img, img
    except Exception as e:
        logger.exception("图片转 base64 失败: {}", e)
        return "", ""


def normalize_image(img: str) -> Tuple[str, str]:
    """规范化图像输入，处理URL和本地文件两种类型。

    返回元组 (api_image, store_image)
    若图片无效或超过限制，则返回 ("", "")
    """
    if not img:
        return "", ""

    raw = img.strip()
    low = raw.lower()

    if low.startswith("http://") or low.startswith("https://"):
        try:
            import requests
            head = requests.head(raw, timeout=5, allow_redirects=True)
            if head.status_code == 200:
                size = int(head.headers.get("Content-Length") or 0)
                if size and size > MAX_IMAGE_BYTES:
                    logger.warning("图片 URL 大小 {} > {}，跳过该图", size, MAX_IMAGE_BYTES)
                    return "", ""
            else:
                logger.warning("图片 URL 不可达，status {}", head.status_code)
                return "", ""
        except Exception as e:
            logger.warning("图片 HEAD 检查异常: {}", e)
        return raw, raw

    if low.startswith("file:///"):
        return "", ""

    if os.path.isfile(raw):
        return image_to_base64(raw)

    return "", ""


def call_dashscope_once(input_data: List[Dict]) -> Tuple[bool, List[float], Optional[int], Optional[float]]:
    """调用达摩院多模态嵌入API一次。

    Returns:
        Tuple: (成功标志, 嵌入向量, HTTP状态码, 重试等待时间)
    """
    limiter.acquire()

    try:
        response = dashscope.MultiModalEmbedding.call(
            model=DASHSCOPE_EMBEDDING_MODEL,
            input=input_data,
            api_key=DASHSCOPE_API_KEY,
        )
    except Exception as e:
        logger.exception("调用 DashScope 异常: {}", e)
        return False, [], None, None

    status = getattr(response, "status_code", None)
    retry_after = None

    try:
        headers = getattr(response, "headers", None)
        if headers and isinstance(headers, dict):
            ra = headers.get("Retry-After") or headers.get("retry-after")
            if ra:
                retry_after = float(ra)
    except Exception:
        pass

    resp_code = getattr(response, "code", "")
    resp_msg = getattr(response, "message", "")

    if status == HTTPStatus.OK:
        try:
            emb = response.output['embeddings'][0]['embedding']
            return True, emb, status, retry_after
        except Exception as e:
            logger.exception("解析嵌入失败: {}", e)
            return False, [], status, retry_after
    else:
        logger.warning("DashScope 请求失败，状态码：{}，code：{}，message：{}", status, resp_code, resp_msg)
        return False, [], status, retry_after


# 注: 原 tool-calling 时代遗留的 search_context / my_search(智谱网络搜索) 工具已移除。
# 检索现在是确定性节点(graph/retrieval.py 的 unified_retrieve), 不再走工具调用,
# 避免工具消息污染干净对话历史; 网络搜索无引用方, 属死代码。
