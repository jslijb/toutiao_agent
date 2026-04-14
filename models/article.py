"""文章数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
import uuid


@dataclass
class ArticleMetrics:
    """文章互动指标（各字段可为 None，不同平台可获取的数据不同）"""
    views: Optional[int] = None       # 阅读量（微信可获取）
    likes: Optional[int] = None       # 点赞/赞同数
    comments: Optional[int] = None    # 评论数
    favorites: Optional[int] = None   # 收藏数
    shares: Optional[int] = None      # 转发量


@dataclass
class ArticleData:
    """爬取的文章数据"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    source: str = ""                  # toutiao / zhihu / wechat / baijiahao / kr36
    title: str = ""
    content: str = ""                 # 正文纯文本
    url: str = ""
    author: str = ""
    publish_time: str = ""            # ISO 格式
    metrics: ArticleMetrics = field(default_factory=ArticleMetrics)
    quality_score: float = 0.0        # 平台内归一化评分 0-1
    crawl_time: str = field(default_factory=lambda: datetime.now().isoformat())
    ttl_days: int = 30                # 文章保留天数，0=永不过期

    def to_dict(self) -> dict:
        d = asdict(self)
        d["metrics"] = asdict(self.metrics)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ArticleData":
        metrics_data = data.pop("metrics", {})
        return cls(
            **data,
            metrics=ArticleMetrics(**metrics_data) if isinstance(metrics_data, dict) else ArticleMetrics(),
        )


@dataclass
class GeneratedArticle:
    """AI 生成的文章"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""                   # 爆款标题
    content: str = ""                 # 正文（约1000字）
    hot_topic: str = ""               # 关联的热点话题
    rag_sources: list[str] = field(default_factory=list)  # RAG 检索到的来源
    scenes: list[str] = field(default_factory=list)       # 4个配图场景描述
    image_paths: list[str] = field(default_factory=list)  # 配图文件路径
    word_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "draft"             # draft / published
    article_type: str = "article"     # article / micro_toutiao
    published_at: str = ""            # 发布时间
    published_url: str = ""           # 发布后的 URL
    metrics: dict = field(default_factory=dict)  # 发布后的数据指标 {views, likes, comments, ...}
    analysis: str = ""                # 文章分析结果

    def to_dict(self) -> dict:
        return asdict(self)
