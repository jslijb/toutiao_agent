"""天行数据API适配器"""
from __future__ import annotations

import os
import time
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings
from datasources.base import BaseSourceAdapter
from datasources.models import SourceHealth, SourceStatus
from datasources.quota_manager import get_quota_manager
from models.article import ArticleData, ArticleMetrics


TIANAPI_ENDPOINTS = {
    "weibo_hot": "/weibohot/index",
    "daily_brief": "/bulletin/index",
    "news": "/allnews/index",
}


class TianapiAdapter(BaseSourceAdapter):
    """天行数据API适配器（微博热搜/每日简报/新闻）"""

    @property
    def name(self) -> str:
        return "tianapi"

    def __init__(self):
        self._api_key = os.environ.get("TIANAPI_KEY", "")
        self._base_url = "https://apis.tianapi.com"
        self._quota = get_quota_manager()
        self._last_call_time = 0.0

    def fetch(self, apis: Optional[list[str]] = None, **kwargs) -> list[ArticleData]:
        """调用天行API获取数据

        Args:
            apis: 要调用的API列表，如 ["weibo_hot", "news"]，None=全部
        """
        if not self._api_key:
            logger.warning("TIANAPI_KEY 未配置，跳过天行数据源")
            return []

        apis = apis or list(TIANAPI_ENDPOINTS.keys())
        all_articles = []

        for api_key in apis:
            if api_key not in TIANAPI_ENDPOINTS:
                logger.warning(f"未知天行API: {api_key}")
                continue

            try:
                articles = self._call_api(api_key)
                all_articles.extend(articles)
                logger.info(f"天行 {api_key} 获取 {len(articles)} 条数据")
            except Exception as e:
                logger.error(f"天行 {api_key} 调用失败: {e}")

        return all_articles

    def health_check(self) -> SourceHealth:
        if not self._api_key:
            return SourceHealth(name=self.name, status=SourceStatus.disabled, message="TIANAPI_KEY 未配置")
        remaining = self._quota.get_tianapi_remaining_beans()
        cfg = getattr(settings, "datasource", None)
        threshold = getattr(cfg, "tianapi_balance_alert_threshold", 100) if cfg else 100
        if remaining < threshold:
            return SourceHealth(
                name=self.name, status=SourceStatus.quota_exceeded,
                message=f"天豆余额不足: {remaining:.0f}", balance_remaining=remaining,
            )
        return SourceHealth(
            name=self.name, status=SourceStatus.available,
            message="天行API就绪", balance_remaining=remaining,
        )

    def _call_api(self, endpoint_key: str) -> list[ArticleData]:
        """调用单个天行API"""
        self._rate_limit()
        endpoint = TIANAPI_ENDPOINTS[endpoint_key]
        url = f"{self._base_url}{endpoint}"

        for attempt in range(3):
            try:
                params = {"key": self._api_key}
                if endpoint_key == "news":
                    params["col"] = 7
                    params["num"] = 20
                resp = httpx.get(url, params=params, timeout=10)
                data = resp.json()

                if data.get("code") != 200:
                    logger.warning(f"天行API返回错误: {data.get('msg', 'unknown')}")
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    return []

                beans = data.get("beans", 0)
                if beans > 0:
                    self._quota.consume_tianapi_beans(beans)

                items = data.get("result", {}) if isinstance(data.get("result"), dict) else data.get("result", [])
                if isinstance(items, dict):
                    items = items.get("newslist", items.get("list", []))

                mapper = {
                    "weibo_hot": self._map_weibo_hot,
                    "daily_brief": self._map_daily_brief,
                    "news": self._map_news,
                }[endpoint_key]

                return [a for item in items if (a := mapper(item)) is not None]

            except httpx.TimeoutException:
                logger.warning(f"天行API超时 (尝试 {attempt + 1}/3)")
                if attempt < 2:
                    time.sleep(1)
            except Exception as e:
                logger.error(f"天行API异常: {e}")
                if attempt < 2:
                    time.sleep(1)

        return []

    def _rate_limit(self) -> None:
        """限流：API调用间隔 >= 1秒"""
        elapsed = time.time() - self._last_call_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_call_time = time.time()

    def _map_weibo_hot(self, item: dict) -> Optional[ArticleData]:
        title = item.get("word", "").strip()
        if not title:
            return None
        return ArticleData(
            source="tianapi_weibo_hot",
            title=title,
            content=item.get("word", ""),
            url=item.get("url", ""),
            metrics=ArticleMetrics(
                views=item.get("hotnum"),
            ),
            quality_score=min(1.0, (item.get("hotnum", 0) or 0) / 10000000),
            ttl_days=30,
        )

    def _map_daily_brief(self, item: dict) -> Optional[ArticleData]:
        title = item.get("title", "").strip()
        if not title:
            return None
        return ArticleData(
            source="tianapi_daily_brief",
            title=title,
            content=item.get("digest", item.get("description", "")),
            url=item.get("url", ""),
            author=item.get("author", ""),
            publish_time=item.get("mtime", item.get("pubDate", "")),
            quality_score=0.5,
            ttl_days=30,
        )

    def _map_news(self, item: dict) -> Optional[ArticleData]:
        title = item.get("title", "").strip()
        if not title:
            return None
        return ArticleData(
            source="tianapi_news",
            title=title,
            content=item.get("description", item.get("content", "")),
            url=item.get("url", ""),
            author=item.get("source", item.get("author", "")),
            publish_time=item.get("ctime", item.get("pubDate", "")),
            metrics=ArticleMetrics(
                views=item.get("viewcount"),
                likes=item.get("likenum"),
            ),
            quality_score=0.5,
            ttl_days=30,
        )
