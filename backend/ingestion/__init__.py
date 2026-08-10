"""入库管道包: PDF → OCR(dots_ocr) → 分片 → 描述 → 向量化 → Milvus。

复用 graph/llm_init 的 LLM/嵌入/Milvus 基础设施; 阻塞管道由 jobs 放到 daemon 线程执行。
"""
