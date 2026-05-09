"""文章质量评分器 — 统一加权：阅读量×0.4 + 点赞量×0.35 + 评论量×0.25

各数据源可用数据不同：
- corpus_news: 无互动数据
- corpus_qa: 点赞数(star)
- tianapi_weibo_hot: 热度(hotnum)
- tianapi_daily_brief/news: 部分互动数据
- bing_news: 无互动数据
- rss_*: 无互动数据

缺失指标时，将其权重按比例重新分配给已有指标。
"""
from __future__ import annotations

from models.article import ArticleData, ArticleMetrics
from loguru import logger


_WEIGHT_VIEWS = 0.40
_WEIGHT_LIKES = 0.35
_WEIGHT_COMMENTS = 0.25

_NORM = {
    "corpus_qa": {"views": 10000, "likes": 500, "comments": 100},
    "tianapi_weibo_hot": {"views": 10000000, "likes": 50000, "comments": 5000},
    "tianapi_news": {"views": 50000, "likes": 2000, "comments": 500},
    "tianapi_daily_brief": {"views": 50000, "likes": 2000, "comments": 500},
    "bing_news": {"views": 50000, "likes": 2000, "comments": 500},
    "default": {"views": 50000, "likes": 2000, "comments": 500},
}


def score_article(article: ArticleData) -> float:
    """统一加权评分：阅读量×0.4 + 点赞量×0.35 + 评论量×0.25

    缺失指标的权重按比例重新分配给已有指标。
    """
    m = article.metrics
    norm = _NORM.get(article.source, _NORM["default"])

    parts: list[tuple[float, float]] = []
    if m.views and m.views > 0:
        s = min(m.views / norm["views"], 1.0)
        parts.append((s * _WEIGHT_VIEWS, _WEIGHT_VIEWS))
    if m.likes and m.likes > 0:
        s = min(m.likes / norm["likes"], 1.0)
        parts.append((s * _WEIGHT_LIKES, _WEIGHT_LIKES))
    if m.comments and m.comments > 0:
        s = min(m.comments / norm["comments"], 1.0)
        parts.append((s * _WEIGHT_COMMENTS, _WEIGHT_COMMENTS))

    if not parts:
        return _fallback_score(article)

    total_weight = sum(w for _, w in parts)
    score = sum(s for s, _ in parts) / total_weight
    return min(score, 1.0)


def _fallback_score(article: ArticleData) -> float:
    """无互动数据时的降级评分"""
    score = 0.3
    if len(article.title) > 10:
        score += 0.2
    if len(article.content) > 500:
        score += 0.2
    if article.author:
        score += 0.1
    return min(score, 1.0)


def normalize_scores(articles: list[ArticleData], source: str) -> list[ArticleData]:
    """对同一数据源的文章进行归一化评分（Min-Max 归一化）"""
    source_articles = [a for a in articles if a.source == source]
    if not source_articles:
        return articles

    scores = [a.quality_score for a in source_articles]
    min_s, max_s = min(scores), max(scores)
    range_s = max_s - min_s if max_s > min_s else 1.0

    for a in source_articles:
        a.quality_score = (a.quality_score - min_s) / range_s
    return articles
