"""文档转换与描述生成(移植自入库项目 milvus_db/db_operator.py)。

LLM 客户端从 graph/llm_init 注入(multiModal_llm / llm / image_to_base64),
不在此新建客户端。
"""
import os
from typing import List, Dict, Tuple

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from loguru import logger


def get_surrounding_text_content(data_list: List[Dict], index: int) -> Tuple[str, str]:
    """在 dict 列表中向前/后找最近的"纯文本项"的 text(用于图片/表格描述的上下文)。"""
    def _find(dir_step):
        i = index + dir_step
        while 0 <= i < len(data_list):
            item = data_list[i]
            if (item.get('text')
                    and item.get('image_path') is None
                    and item.get('category') not in ('table', 'image')):
                return item.get('text')
            i += dir_step
        return None

    return (_find(-1) or ""), (_find(1) or "")


def doc_to_dict(docs: List[Document]) -> List[Dict]:
    """把 Splitter 产出的 Document 列表转成入库字典(text/image/table 三类)。"""
    result_list = []

    for doc in docs:
        doc_dict = {}
        metadata = doc.metadata

        # 1. text (text 和 table 类型都有文本内容)
        if metadata.get('embedding_type') in ('text', 'table'):
            doc_dict['text'] = doc.page_content
        else:
            doc_dict['text'] = None

        # 2. category
        doc_dict['category'] = metadata.get('embedding_type', '')

        # 3. filename
        source = metadata.get('source', '')
        doc_dict['filename'] = source

        # 4. filetype
        _, file_extension = os.path.splitext(source)
        doc_dict['filetype'] = file_extension.lower()

        # 5. image_path (仅 image 类型)
        if metadata.get('embedding_type') == 'image':
            doc_dict['image_path'] = doc.page_content
        else:
            doc_dict['image_path'] = None

        # 6. title (拼接 Header 层级)
        headers = []
        header_keys = [key for key in metadata.keys() if key.startswith('Header')]
        header_keys_sorted = sorted(
            header_keys,
            key=lambda x: int(x.split()[1]) if x.split()[1].isdigit() else x,
        )
        for key in header_keys_sorted:
            value = metadata.get(key, '').strip()
            if value:
                headers.append(value)
        doc_dict['title'] = ' --> '.join(headers) if headers else ''

        # 非图片/表格: text 前拼标题
        if not doc_dict['image_path'] and doc_dict['category'] != 'table':
            doc_dict['text'] = doc_dict['title'] + ' ：' + doc_dict['text']
        result_list.append(doc_dict)

    return result_list


def generate_image_description(data_list, multiModal_llm, image_to_base64):
    """为图片字典生成多模态描述(结合前后文), 写回 text 字段。"""
    results = []
    for index, item in enumerate(data_list):
        if not item.get('image_path'):  # 非图片字典
            results.append(item)
            continue

        prev_text, next_text = get_surrounding_text_content(data_list, index)
        image_data = image_to_base64(item['image_path'])[0]

        if prev_text and next_text:
            context_prompt = f"""
            前文内容: {prev_text}
            后文内容: {next_text}

            请根据以上上下文和图片内容, 生成对该图片的简洁描述, 描述内容长度最好不超过300个汉字。
            注意: 图片可能与前文、后文或两者都相关, 请综合分析。
            """
        elif prev_text:
            context_prompt = f"""
            前文内容: {prev_text}

            请根据以上上下文和图片内容, 生成对该图片的简洁描述, 描述内容长度最好不超过300个汉字。
            注意: 图片可能与前文内容相关, 请结合分析。
            """
        elif next_text:
            context_prompt = f"""
            后文内容: {next_text}

            请根据以上上下文和图片内容, 生成对该图片的简洁描述, 描述内容长度最好不超过300个汉字。
            注意: 图片可能与后文内容相关, 请结合分析。
            """
        else:
            context_prompt = "请描述这张图片的内容, 生成对该图片的简洁描述, 描述内容长度最好不超过300个汉字。"

        message = HumanMessage(content=[
            {"type": "text", "text": context_prompt},
            {"type": "image_url", "image_url": {"url": image_data}},
        ])
        response = multiModal_llm.invoke([message])
        item['text'] = response.content
        logger.info("[入库] 图片描述生成: {} ({} 字符)", item['image_path'], len(response.content))
        results.append(item)

    return results


def generate_table_description(data_list, llm):
    """为表格字典生成自然语言描述, text = 描述 + 原始 HTML。"""
    table_count = sum(1 for item in data_list if item.get('category') == 'table')
    if table_count:
        logger.info("[入库] 表格描述生成: {} 个表格待描述", table_count)

    for index, item in enumerate(data_list):
        if item.get('category') != 'table':
            continue

        prev_text, next_text = get_surrounding_text_content(data_list, index)
        table_html = item.get('text', '')

        if prev_text and next_text:
            context_prompt = f"""
            前文内容: {prev_text}
            后文内容: {next_text}

            请根据以上上下文和表格HTML内容, 生成对该表格的简洁描述, 描述内容长度最好不超过300个汉字。
            注意: 表格可能与前文、后文或两者都相关, 请综合分析。
            """
        elif prev_text:
            context_prompt = f"""
            前文内容: {prev_text}

            请根据以上上下文和表格HTML内容, 生成对该表格的简洁描述, 描述内容长度最好不超过300个汉字。
            注意: 表格可能与前文内容相关, 请结合分析。
            """
        elif next_text:
            context_prompt = f"""
            后文内容: {next_text}

            请根据以上上下文和表格HTML内容, 生成对该表格的简洁描述, 描述内容长度最好不超过300个汉字。
            注意: 表格可能与后文内容相关, 请结合分析。
            """
        else:
            context_prompt = "请描述以下表格的内容, 生成简洁描述, 描述内容长度最好不超过300个汉字。"

        message = HumanMessage(content=context_prompt + f"\n\n表格HTML:\n{table_html}")
        response = llm.invoke([message])
        # text = 描述 + 原始 HTML(描述用于检索匹配, HTML 用于展示)
        item['text'] = response.content + "\n\n" + table_html
        logger.info("[入库] 表格描述生成: {} 字符", len(item['text']))

    return data_list
