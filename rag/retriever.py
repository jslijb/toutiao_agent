"""语义检索器"""
from __future__ import annotations

from typing import Optional

import numpy as np
from loguru import logger

from rag.embedder import DashScopeEmbedder
from rag.vectorstore import FAISSVectorStore
from config.settings import settings


class Retriever:
    """RAG 语义检索器：查询向量化 -> FAISS Top-K -> 返回文档片段+元数据"""

    def __init__(
        self,
        vectorstore: Optional[FAISSVectorStore] = None,
        embedder: Optional[DashScopeEmbedder] = None,
    ):
        self.embedder = embedder or DashScopeEmbedder()
        self.vectorstore = vectorstore or FAISSVectorStore()

    def retrieve(self, query: str, top_k: int = 10) -> list[dict]:
        """
        语义检索
        返回: [{"id": int, "score": float, "text": str, "title": str, "source": str, ...}, ...]
        """
        if self.vectorstore.total == 0:
            logger.warning("RAG 知识库为空，无法检索")
            return []

        top_k = min(top_k, settings.generation.rag_top_k)

        # 查询向量化
        query_vector = self.embedder.embed_query(query)

        # FAISS 检索
        results = self.vectorstore.search(query_vector, top_k)

        # 格式化结果
        retrieved = []
        for idx, score, meta in results:
            item = {
                "id": idx,
                "score": round(score, 4),
                "text": meta.get("text", ""),
                "title": meta.get("title", ""),
                "source": meta.get("source", ""),
                "url": meta.get("url", ""),
                "quality_score": meta.get("quality_score", 0),
            }
            retrieved.append(item)

        logger.debug(f"检索 '{query[:20]}...' 返回 {len(retrieved)} 条结果")
        return retrieved

    def retrieve_with_context(self, query: str, top_k: int = 10) -> str:
        """检索并拼接为上下文文本（用于 LLM 输入）"""
        results = self.retrieve(query, top_k)
        if not results:
            return ""

        context_parts = []
        for i, r in enumerate(results, 1):
            source = f"（来源: {r['source']}）" if r.get("source") else ""
            context_parts.append(f"[参考{i}]{source} {r['text']}")

        return "\n\n".join(context_parts)
