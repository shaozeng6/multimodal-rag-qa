"""Markdown 目录分片(移植自入库项目 splitters/splitter_md.py)。

改动:
- embedding 改为构造函数注入(复用 infra.embedding)
- 去掉对 milvus_db.db_operator / my_llm 的顶层 import(历史残留)
- SemanticChunker 不可用时回退纯文本切分
"""
import base64
import hashlib
import io
import os
import re
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from loguru import logger
from PIL import Image

from services.config_service import get_int


def get_sorted_md_files(input_dir: str) -> List[str]:
    r"""列出目录下所有 .md, 按文件名中 _page_(\d+) 数字排序, 无数字的排最后。"""
    if not os.path.isdir(input_dir):
        return []
    md_files = [os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.endswith(".md")]

    def _page_no(path):
        m = re.search(r"_page_(\d+)", os.path.basename(path))
        return int(m.group(1)) if m else float("inf")

    return sorted(md_files, key=_page_no)


class MarkdownDirSplitter:
    """把 OCR 生成的 md 目录切成 text/image 两类 Documents。"""

    def __init__(self, embedding, images_output_dir: str, text_chunk_size: int = None):
        """分片: 阈值 text_chunk_size 从 sys_config 读(hot, 对下一个入库任务生效)。

        chunk_size 是「按标题切分后, 超过该字符数再按语义切分」的阈值;
        语义切分下无重叠概念(chunk_overlap 已撤), 回退字符切分固定重叠 50。
        """
        self.images_output_dir = images_output_dir
        self.text_chunk_size = (
            text_chunk_size if text_chunk_size is not None else get_int("ingestion.chunk_size", 1000)
        )
        os.makedirs(self.images_output_dir, exist_ok=True)

        # 标题层级配置
        self.headers_to_split_on = [
            ("#", "Header 1"),
            ("##", "Header 2"),
            ("###", "Header 3"),
        ]
        self.text_splitter = MarkdownHeaderTextSplitter(self.headers_to_split_on)

        # 语义分割器(嵌入不可用时回退纯文本切分)
        self.semantic_splitter = None
        try:
            from langchain_experimental.text_splitter import SemanticChunker

            self.semantic_splitter = SemanticChunker(
                embedding, breakpoint_threshold_type="percentile"
            )
        except Exception as e:
            logger.warning("SemanticChunker 初始化失败, 回退纯文本切分: {}", e)

    def save_base64_to_image(self, base64_str: str, output_path: str) -> None:
        """将 base64 字符串解码为图像并保存。

        D8 修复: 先写临时文件再 os.replace 原子替换 —— 多文档含同图并发入库时,
        直接 img.save 同一 md5 路径会截断+交错写坏; 原子替换保证任意时刻
        目标路径要么是完整旧文件要么是完整新文件(内容相同, 谁赢都一样)。
        """
        if base64_str.startswith("data:image"):
            base64_str = base64_str.split(",", 1)[1]
        img_data = base64.b64decode(base64_str)
        img = Image.open(io.BytesIO(img_data))
        tmp_path = f"{output_path}.tmp"
        try:
            img.save(tmp_path)
            os.replace(tmp_path, output_path)
        finally:
            if os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def process_images(self, content: str, source: str) -> List[Document]:
        """处理 Markdown 中的 base64 图片, 提取为图片 Document。"""
        image_docs = []
        pattern = r'data:image/(.*?);base64,(.*?)\)'  # 正则匹配 base64 图片

        def replace_image(match):
            img_type = match.group(1).split(';')[0]
            base64_data = match.group(2)
            # 生成唯一文件名
            hash_key = hashlib.md5(base64_data.encode()).hexdigest()
            filename = f"{hash_key}.{img_type if img_type in ['png', 'jpg', 'jpeg'] else 'png'}"
            img_path = os.path.join(self.images_output_dir, filename)
            self.save_base64_to_image(base64_data, img_path)

            image_docs.append(Document(
                page_content=str(img_path),
                metadata={"source": source, "alt_text": "图片", "embedding_type": "image"},
            ))
            return "[图片]"

        re.sub(pattern, replace_image, content, flags=re.DOTALL)
        return image_docs

    def remove_base64_images(self, text: str) -> str:
        """移除所有 base64 图片标记。"""
        pattern = r'!\[\]\(data:image/(.*?);base64,(.*?)\)'
        return re.sub(pattern, '', text)

    def _chunk(self, doc: Document) -> List[Document]:
        """超长文档切分: 优先语义切分, 回退按字符硬切。"""
        if len(doc.page_content) <= self.text_chunk_size:
            return [doc]
        if self.semantic_splitter:
            return self.semantic_splitter.split_documents([doc])
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.text_chunk_size, chunk_overlap=50
        )
        return splitter.split_documents([doc])

    def process_md_file(self, md_file: str) -> List[Document]:
        """处理单个 md 文件: 切分 + 提取图片。"""
        with open(md_file, "r", encoding="utf-8") as file:
            content = file.read()

        split_documents = self.text_splitter.split_text(content)
        documents = []
        for doc in split_documents:
            # D6 修复: 原来只匹配 'data:image/png;base64', jpeg/jpg/gif/webp 的 base64
            # 走 else 分支把 base64 原文当 text 嵌入(污染 dense/BM25)且图片通道缺失;
            # 改为匹配任意图片类型(process_images/remove_base64_images 的 regex 本就支持)
            if '![](data:image/' in doc.page_content:
                image_docs = self.process_images(doc.page_content, md_file)
                cleaned_content = self.remove_base64_images(doc.page_content)
                if cleaned_content.strip():
                    doc.metadata['embedding_type'] = 'text'
                    documents.append(Document(page_content=cleaned_content, metadata=doc.metadata))
                documents.extend(image_docs)
            else:
                doc.metadata['embedding_type'] = 'text'
                documents.append(doc)

        final_docs = []
        for d in documents:
            final_docs.extend(self._chunk(d))
        return final_docs

    def add_title_hierarchy(self, documents: List[Document], source_filename: str) -> List[Document]:
        """为文档补充标题层级结构。"""
        current_titles = {1: "", 2: "", 3: ""}
        processed_docs = []

        for doc in documents:
            new_metadata = doc.metadata.copy()
            new_metadata['source'] = source_filename

            for level in range(1, 4):
                header_key = f'Header {level}'
                if header_key in new_metadata:
                    current_titles[level] = new_metadata[header_key]
                    for lower_level in range(level + 1, 4):
                        current_titles[lower_level] = ""

            for level in range(1, 4):
                header_key = f'Header {level}'
                if header_key not in new_metadata:
                    new_metadata[header_key] = current_titles[level]

            processed_docs.append(Document(page_content=doc.page_content, metadata=new_metadata))

        return processed_docs

    def process_md_dir(self, md_dir: str, source_filename: str) -> List[Document]:
        """处理一个 md 目录(按页序), 返回全部 Documents。"""
        md_files = get_sorted_md_files(md_dir)
        all_documents = []
        for md_file in md_files:
            all_documents.extend(self.process_md_file(md_file))
        return self.add_title_hierarchy(all_documents, source_filename)
