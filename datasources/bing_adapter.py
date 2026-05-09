"""Bing Search API适配器"""
from __future__ import annotations

import os
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings
from datasources.base import BaseSourceAdapter
from datasources.models import SourceHealth, SourceStatus
from datasources.quota_manager import get_quota_manager
from models.article import ArticleData, ArticleMetrics


class BingAdapter(BaseSourceAdapter):
    """Azure Bing News Search API适配器"""

    @property
    def name(self) -> str:
        return "bing"

    def __init__(self):
        self._api_key = os.environ.get("BING_API_KEY", "")
        self._quota = get_quota_manager()

    def fetch(
        self,
        keywords: Optional[list[str]] = None,
        max_results_per_keyword: int = 10,
        **kwargs,
    ) -> list[ArticleData]:
        """按关键词执行Bing News Search"""
        if not self._api_key:
            logger.warning("BING_API_KEY 未配置，跳过Bing数据源")
            return []

        keywords = keywords or settings.crawler.keywords
        all_articles = []

        for keyword in keywords:
            if not self._quota.check_bing_quota():
                logger.warning("Bing API月度配额已用完，停止搜索")
                break

            articles = self._search_single(keyword, max_results_per_keyword)
            all_articles.extend(articles)
            logger.info(f"Bing搜索 '{keyword}' 获取 {len(articles)} 条结果")

        return all_articles

    def health_check(self) -> SourceHealth:
        if not self._api_key:
            return SourceHealth(name=self.name, status=SourceStatus.disabled, message="BING_API_KEY 未配置")
        remaining = self._quota.get_bing_remaining()
        if remaining <= 0:
            return SourceHealth(
                name=self.name, status=SourceStatus.quota_exceeded,
                message="月度配额已用完", quota_remaining=0,
            )
        return SourceHealth(
            name=self.name, status=SourceStatus.available,
            message=f"剩余 {remaining} 次", quota_remaining=remaining,
        )

    def _search_single(self, keyword: str, count: int) -> list[ArticleData]:
        """单关键词Bing News搜索"""
        if not self._quota.check_bing_quota():
            return []

        cfg = getattr(settings, "datasource", None)
        endpoint = getattr(cfg, "bing_endpoint", "https://api.bing.microsoft.com/v7.0/news/search") if cfg else "https://api.bing.microsoft.com/v7.0/news/search"

        try:
            resp = httpx.get(
                endpoint,
                headers={"Ocp-Apim-Subscription-Key": self._api_key},
                params={
                    "q": keyword,
                    "count": count,
                    "mkt": "zh-CN",
                    "setLang": "zh-Hans",
                    "freshness": "Week",
                },
                timeout=10,
            )

            if resp.status_code == 401:
                logger.error("Bing API Key 无效 (401)")
                return []

            if resp.status_code == 429:
                logger.warning("Bing API 限流 (429)")
                return []

            if resp.status_code != 200:
                logger.warning(f"Bing API返回 {resp.status_code}")
                return []

            self._quota.consume_bing(1)

            data = resp.json()
            items = data.get("value", [])
            return [a for item in items if (a := self._map_result(item, keyword)) is not None]

        except httpx.TimeoutException:
            logger.warning(f"Bing搜索超时: {keyword}")
            return []
        except Exception as e:
            logger.error(f"Bing搜索异常: {e}")
            return []

    def _map_result(self, item: dict, keyword: str) -> Optional[ArticleData]:
        title = item.get("name", "").strip()
        content = item.get("description", "").strip()
        if not title:
            return None
        return ArticleData(
            source="bing_news",
            title=title,
            content=content,
            url=item.get("url", ""),
            author=item.get("provider", [{}])[0].get("name", "") if item.get("provider") else "",
            publish_time=item.get("datePublished", ""),
            quality_score=0.5,
            ttl_days=30,
        )
