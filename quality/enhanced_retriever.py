"""增强检索器 - 按quality_label分离检索教训和经验"""
from __future__ import annotations

from loguru import logger

from config.settings import settings
from quality.models import EnhancedRetrievalResult
from quality.retrieval_match_logger import log_retrieval_match
from rag.retriever import Retriever
from rag.vectorstore import FAISSVectorStore


class EnhancedRetriever:
    """增强检索器：原有RAG检索 + 经验检索 + 教训检索"""

    def __init__(self, vectorstore: FAISSVectorStore | None = None):
        self._retriever = Retriever(vectorstore=vectorstore)
        self._vectorstore = vectorstore or self._retriever.vectorstore

    def retrieve_enhanced(
        self,
        query: str,
        top_k: int = 10,
        article_id: str = "",
    ) -> EnhancedRetrievalResult:
        """增强检索：RAG参考 + 经验 + 教训，分离注入"""
        result = EnhancedRetrievalResult()

        rag_results = self._retriever.retrieve(query, top_k=top_k)
        rag_scores = [r.get("score", 0) for r in rag_results]
        result.rag_match_count = len(rag_results)

        log_retrieval_match(
            query=query, retrieval_type="rag_reference",
            scores=rag_scores, injected_count=len(rag_results),
            target_article_id=article_id,
        )

        context_parts = []
        for i, r in enumerate(rag_results, 1):
            source = f"（来源: {r.get('source', '')}）" if r.get("source") else ""
            context_parts.append(f"[参考{i}]{source} {r['text']}")

        cap = getattr(settings, "quality", None)
        lesson_cap = getattr(cap, "negative_retrieval_cap", 3) if cap else 3
        exp_cap = getattr(cap, "positive_retrieval_cap", 3) if cap else 3

        lesson_results = self._retrieve_by_label(query, "negative_lesson", lesson_cap)
        lesson_scores = [r.get("score", 0) for r in lesson_results]
        result.lesson_match_count = len(lesson_results)

        if lesson_results:
            log_retrieval_match(
                query=query, retrieval_type="negative_lesson",
                scores=lesson_scores, injected_count=len(lesson_results),
                target_article_id=article_id,
            )
            avoidance_parts = []
            for i, r in enumerate(lesson_results, 1):
                causes = r.get("cause_categories", "")
                if causes:
                    try:
                        import json
                        causes = ", ".join(json.loads(causes))
                    except Exception:
                        pass
                cause_str = f"（原因: {causes}）" if causes else ""
                avoidance_parts.append(f"{i}. {r['text']}{cause_str}")
            result.avoidance_guide = "### 避坑指南（基于历史劣质文章教训）\n" + "\n".join(avoidance_parts)

        exp_results = self._retrieve_by_label(query, "positive_experience", exp_cap)
        exp_scores = [r.get("score", 0) for r in exp_results]
        result.experience_match_count = len(exp_results)

        if exp_results:
            log_retrieval_match(
                query=query, retrieval_type="positive_experience",
                scores=exp_scores, injected_count=len(exp_results),
                target_article_id=article_id,
            )
            exp_parts = []
            for i, r in enumerate(exp_results, 1):
                exp_parts.append(f"{i}. {r['text']}")
            result.experience_guide = "### 参考经验（基于历史优质文章经验）\n" + "\n".join(exp_parts)

        result.context = "\n\n".join(context_parts) if context_parts else ""
        return result

    def _retrieve_by_label(
        self,
        query: str,
        quality_label: str,
        top_k: int = 3,
    ) -> list[dict]:
        """按quality_label过滤检索"""
        try:
            embedder = self._retriever.embedder
            query_vector = embedder.embed_query(query)
            expand_k = top_k * 5
            results = self._vectorstore.search(query_vector, expand_k)

            filtered = []
            for idx, score, meta in results:
                if meta.get("quality_label") == quality_label and not meta.get("deprecated"):
                    filtered.append({
                        "id": idx,
                        "score": round(score, 4),
                        "text": meta.get("text", ""),
                        "title": meta.get("title", ""),
                        "cause_categories": meta.get("cause_categories", ""),
                    })
                    if len(filtered) >= top_k:
                        break

            return filtered
        except Exception as e:
            logger.warning(f"按label检索失败: {e}")
            return []
