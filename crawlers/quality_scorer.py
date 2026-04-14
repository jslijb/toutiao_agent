"""文章质量评分器 — 统一加权：阅读量×0.4 + 点赞量×0.35 + 评论量×0.25

各平台可用数据不同：
- 头条: 点赞、评论（阅读量、收藏不公开）
- 知乎: 点赞(赞同)、评论、收藏
- 微信: 阅读、点赞(在看)、转发
- 36氪: 点赞、评论
- 百家号: 无互动数据

缺失指标时，将其权重按比例重新分配给已有指标。
"""
from __future__ import annotations

from models.article import ArticleData, ArticleMetrics
from loguru import logger


# 统一权重
_WEIGHT_VIEWS = 0.40
_WEIGHT_LIKES = 0.35
_WEIGHT_COMMENTS = 0.25

# 各平台的归一化基准值（达到此值即得满分1.0）
_NORM = {
    "toutiao":  {"views": 50000, "likes": 2000, "comments": 500},
    "zhihu":    {"views": 100000, "likes": 5000, "comments": 500},
    "wechat":   {"views": 100000, "likes": 5000, "comments": 1000},
    "kr36":     {"views": 50000, "likes": 200, "comments": 100},
    "baijiahao": {"views": 50000, "likes": 1000, "comments": 300},
}


def score_article(article: ArticleData) -> float:
    """统一加权评分：阅读量×0.4 + 点赞量×0.35 + 评论量×0.25

    缺失指标的权重按比例重新分配给已有指标。
    """
    m = article.metrics
    norm = _NORM.get(article.source, _NORM["toutiao"])

    # 收集已有指标及其权重
    parts: list[tuple[float, float]] = []  # (weighted_score, weight)
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
        # 无任何互动数据，给予基础分（根据平台微调）
        if article.source == "baijiahao":
            return _score_baijiahao_fallback(article)
        return 0.3

    # 将缺失指标的权重按比例重新分配
    total_weight = sum(w for _, w in parts)
    score = sum(s for s, _ in parts) / total_weight
    return min(score, 1.0)


def _score_baijiahao_fallback(article: ArticleData) -> float:
    """百家号降级评分（互动数据不可获取）"""
    score = 0.3
    if len(article.title) > 10:
        score += 0.2
    if len(article.content) > 500:
        score += 0.2
    if article.author:
        score += 0.1
    return min(score, 1.0)


def normalize_scores(articles: list[ArticleData], source: str) -> list[ArticleData]:
    """对同一平台的文章进行归一化评分（Min-Max 归一化）"""
    platform_articles = [a for a in articles if a.source == source]
    if not platform_articles:
        return articles

    scores = [a.quality_score for a in platform_articles]
    min_s, max_s = min(scores), max(scores)
    range_s = max_s - min_s if max_s > min_s else 1.0

    for a in platform_articles:
        a.quality_score = (a.quality_score - min_s) / range_s
    return articles
