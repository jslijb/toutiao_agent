"""文档分块器"""
from __future__ import annotations

import re
from typing import Optional
from loguru import logger

from rag.cleaner import TextCleaner


class TextChunker:
    """文档分块器，按段落分割"""

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 100,
        min_chunk_size: int = 50,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

    def chunk_text(self, text: str) -> list[str]:
        """将文本分块"""
        if not text or len(text.strip()) < self.min_chunk_size:
            return [text.strip()] if text.strip() else []

        # 先清洗文本
        text = TextCleaner.clean(text)

        # 按段落分割
        paragraphs = re.split(r"\n{2,}", text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        chunks = []
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) + 1 <= self.chunk_size:
                current_chunk = f"{current_chunk}\n{para}" if current_chunk else para
            else:
                # 保存当前块
                if len(current_chunk) >= self.min_chunk_size:
                    chunks.append(current_chunk.strip())
                # 新块，保留重叠
                if self.chunk_overlap > 0 and len(current_chunk) > self.chunk_overlap:
                    overlap_text = current_chunk[-self.chunk_overlap:]
                    current_chunk = f"{overlap_text}\n{para}"
                else:
                    current_chunk = para

        # 最后一块
        if len(current_chunk) >= self.min_chunk_size:
            chunks.append(current_chunk.strip())

        # 处理过长的单个段落
        final_chunks = []
        for chunk in chunks:
            if len(chunk) <= self.chunk_size:
                final_chunks.append(chunk)
            else:
                final_chunks.extend(self._split_long_chunk(chunk))

        logger.debug(f"文本分块完成: 原始 {len(text)} 字 -> {len(final_chunks)} 个块")
        return final_chunks

    def _split_long_chunk(self, text: str) -> list[str]:
        """分割过长的文本块"""
        chunks = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size
            chunk = text[start:end]
            if start > 0:
                # 保留重叠
                overlap_start = max(0, end - self.chunk_overlap)
                chunk = text[overlap_start:end]

            # 尝试在句子边界分割
            if end < len(text):
                last_period = chunk.rfind("。")
                if last_period > self.min_chunk_size:
                    chunk = text[start:start + last_period + 1]
                    end = start + last_period + 1

            chunks.append(chunk.strip())
            start = end

        return chunks

    def chunk_article(self, title: str, content: str) -> list[dict]:
        """分块并保留元数据"""
        chunks = self.chunk_text(content)
        return [
            {
                "text": chunk,
                "title": title,
                "index": i,
            }
            for i, chunk in enumerate(chunks)
        ]
