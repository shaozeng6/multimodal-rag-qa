"""应用配置模块,使用 pydantic-settings 管理所有环境变量配置。"""
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from loguru import logger


class Settings(BaseSettings):
    """全局配置,从 .env 文件读取。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # MySQL 数据库连接串
    MYSQL_URL: str = "mysql+aiomysql://root:password@localhost:3306/rag_enterprise"
    # Redis 连接串
    REDIS_URL: str = "redis://localhost:6379/0"
    # JWT 配置
    JWT_SECRET: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440
    # Milvus 向量库
    MILVUS_URI: str = "./milvus_local.db"
    # LLM 配置
    LLM_BASE_URL: str = "http://localhost:8000/v1"
    LLM_API_KEY: str = "your-api-key"
    # CORS 允许的来源(逗号分隔)
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    # RAG 评估通过阈值(0~1), 低于该值的回答进入人工审批
    EVALUATE_THRESHOLD: float = 0.7

    # ---- 入库管道(Ingestion) ----
    # OCR: dots_ocr 依赖本地 vllm GPU 服务(模型 dots_ocr)
    OCR_VLLM_IP: str = "localhost"
    OCR_VLLM_PORT: int = 6006
    OCR_VLLM_MODEL: str = "dots_ocr"
    OCR_VLLM_API_KEY: str = "0"  # vllm 通常用假 key
    # OCR 输出 md 目录 / splitter 提取图片目录 / 上传临时 PDF 目录
    INGEST_OUTPUT_DIR: str = "ingest_output"
    INGEST_IMAGES_DIR: str = "ingest_output/images"
    INGEST_TMP_DIR: str = "ingest_tmp"
    INGEST_OCR_THREADS: int = 16
    INGEST_OCR_DPI: int = 200

    # ---- 消息图片存储(schema_v2: 不存 base64, 落文件后存引用) ----
    # 该目录通过 main.py 的 /uploads 静态挂载暴露给前端
    UPLOAD_IMAGES_DIR: str = "uploads/images"

    # 额外允许通过 /api/files 提供图片的根目录(分号分隔; 默认只允许入库输出目录)
    # 用于旧数据 image_path 指向其他目录(如旧项目 output)的情况
    KB_IMAGE_ROOTS: str = ""

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, v: str) -> str:
        """保证 CORS_ORIGINS 至少是有效字符串。"""
        if not v:
            return "http://localhost:5173,http://localhost:3000"
        return v

    @property
    def cors_origins_list(self) -> List[str]:
        """将逗号分隔的 CORS_ORIGINS 转为列表。"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()

logger.info("应用配置加载完成: MYSQL_URL={}", settings.MYSQL_URL)
