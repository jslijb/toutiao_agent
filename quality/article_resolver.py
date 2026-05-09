"""文章统一查询 — 优先 ArticleStore，其次 GeneratedArticleStore"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger


@dataclass
class ResolvedArticle:
    article_id: str
    title: str
    content: str
    source_type: str
    store_ref: str


def resolve_article(article_id: str) -> ResolvedArticle | None:
    """统一查询文章，优先 ArticleStore，其次 GeneratedArticleStore"""
    try:
        from models.article_store import get_article_store
        store = get_article_store()
        store._ensure_loaded()
        for article in store._index.values():
            if article.id == article_id:
                return ResolvedArticle(
                    article_id=article_id,
                    title=article.title,
                    content=article.content,
                    source_type="data_source",
                    store_ref="article_store",
                )
    except Exception as e:
        logger.warning(f"ArticleStore查询异常: {e}")

    try:
        from models.generated_store import get_generated_store
        gen_store = get_generated_store()
        gen = gen_store.get(article_id)
        if gen:
            return ResolvedArticle(
                article_id=article_id,
                title=gen.title,
                content=gen.content,
                source_type="generated",
                store_ref="generated_store",
            )
    except Exception as e:
        logger.warning(f"GeneratedArticleStore查询异常: {e}")

    return None
