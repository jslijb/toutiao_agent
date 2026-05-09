"""RSS数据源适配器"""
from __future__ import annotations

import re
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings
from datasources.base import BaseSourceAdapter
from datasources.models import SourceHealth, SourceStatus, RSSSourceConfig
from models.article import ArticleData, ArticleMetrics


DEFAULT_RSS_SOURCES = [
    RSSSourceConfig(name="36kr", url="https://36kr.com/feed", enabled=True),
    RSSSourceConfig(name="huxiu", url="https://www.huxiu.com/rss/0.xml", enabled=False),
    RSSSourceConfig(name="zhihu_daily", url="https://daily.zhihu.com/rss", enabled=True),
    RSSSourceConfig(name="sspai", url="https://sspai.com/feed", enabled=True),
    RSSSourceConfig(name="ithome", url="https://www.ithome.com/rss/", enabled=True),
]


class RSSAdapter(BaseSourceAdapter):
    """RSS数据源适配器"""

    @property
    def name(self) -> str:
        return "rss"

    def __init__(self):
        self._sources = self._load_sources()
        cfg = getattr(settings, "datasource", None)
        self._fetch_fulltext = getattr(cfg, "rss_fetch_fulltext", False) if cfg else False

    def fetch(self, sources: Optional[list[str]] = None, **kwargs) -> list[ArticleData]:
        """获取并解析RSS源

        Args:
            sources: 要获取的RSS源名称列表，None=全部启用的源
        """
        import feedparser

        target_sources = self._sources
        if sources is not None:
            target_sources = [s for s in self._sources if s.name in sources]

        if not target_sources:
            target_sources = DEFAULT_RSS_SOURCES

        all_articles = []

        for source_config in target_sources:
            if not source_config.enabled:
                continue
            try:
                articles = self._fetch_single_source(source_config)
                all_articles.extend(articles)
                logger.info(f"RSS {source_config.name} 获取 {len(articles)} 条")
            except Exception as e:
                logger.error(f"RSS {source_config.name} 获取失败: {e}")

        return all_articles

    def health_check(self) -> SourceHealth:
        return SourceHealth(
            name=self.name, status=SourceStatus.available,
            message=f"{len(self._sources)} 个RSS源已配置",
        )

    def _fetch_single_source(self, source_config: RSSSourceConfig) -> list[ArticleData]:
        """获取并解析单个RSS源"""
        import feedparser

        try:
            resp = httpx.get(source_config.url, timeout=30, follow_redirects=True)
            feed = feedparser.parse(resp.text)
        except httpx.HTTPError as e:
            logger.warning(f"RSS源不可达 {source_config.name}: {e}")
            return []

        articles = []
        for entry in feed.entries:
            article = self._parse_entry(entry, source_config.name)
            if article is not None:
                if self._fetch_fulltext and article.url:
                    fulltext = self._fetch_fulltext(article.url)
                    if fulltext:
                        article.content = fulltext
                articles.append(article)

        return articles

    def _parse_entry(self, entry, source_name: str) -> Optional[ArticleData]:
        """解析单个RSS条目"""
        title = getattr(entry, "title", "").strip()
        if not title:
            return None

        summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        content = self._clean_html(summary)

        return ArticleData(
            source=f"rss_{source_name}",
            title=title,
            content=content,
            url=getattr(entry, "link", ""),
            author=getattr(entry, "author", ""),
            publish_time=getattr(entry, "published", ""),
            quality_score=0.5,
            ttl_days=30,
        )

    def _fetch_fulltext(self, url: str) -> Optional[str]:
        """使用readability-lxml提取原文正文"""
        try:
            from readability import Document
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            doc = Document(resp.text)
            content = doc.summary()
            return self._clean_html(content)
        except Exception as e:
            logger.debug(f"全文获取失败 {url}: {e}")
            return None

    def _clean_html(self, text: str) -> str:
        """清洗HTML标签"""
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def _load_sources(self) -> list[RSSSourceConfig]:
        """从配置加载RSS源列表"""
        cfg = getattr(settings, "datasource", None)
        if cfg is None:
            return DEFAULT_RSS_SOURCES
        rss_sources = getattr(cfg, "rss_sources", [])
        if not rss_sources:
            return DEFAULT_RSS_SOURCES
        return [
            RSSSourceConfig(name=s.get("name", ""), url=s.get("url", ""), enabled=s.get("enabled", True))
            for s in rss_sources
            if s.get("url")
        ]
