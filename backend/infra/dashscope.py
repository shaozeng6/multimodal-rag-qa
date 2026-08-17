"""DashScope 多模态嵌入: 限流器 + 图片转换 + 单次调用(从 graph/llm_init.py 拆分)。

- FixedWindowRateLimiter: 线程安全固定窗口限速(检索侧 to_thread 并行与入库 daemon 共用)
- image_to_base64 / normalize_image: 图片输入规范化
- call_dashscope_once: 单次多模态嵌入调用, 返回(成功, 向量, 状态码, Retry-After)
"""
import os
import threading
import time
from http import HTTPStatus
from typing import Dict, List, Optional, Tuple

import dashscope
from loguru import logger

from infra.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_EMBEDDING_MODEL,
    MAX_IMAGE_BYTES,
    RPM_LIMIT,
    WINDOW_SECONDS,
)


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
