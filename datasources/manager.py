"""数据源统一管理与编排"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from loguru import logger

from config.settings import settings
from datasources.base import BaseSourceAdapter
from datasources.corpus_adapter import CorpusAdapter
from datasources.tianapi_adapter import TianapiAdapter
from datasources.bing_adapter import BingAdapter
from datasources.rss_adapter import RSSAdapter
from datasources.models import SourceHealth, ImportResult
from models.article import ArticleData


class DataSourceManager:
    """数据源统一管理与编排"""

    def __init__(self):
        self._adapters: dict[str, BaseSourceAdapter] = {}
        self._register_adapters()

    @property
    def adapters(self) -> dict[str, BaseSourceAdapter]:
        return self._adapters

    def _register_adapters(self) -> None:
        """根据配置自动注册可用适配器"""
        try:
            corpus = CorpusAdapter()
            if corpus.health_check().status.value == "available":
                self._adapters["corpus"] = corpus
                logger.info("数据源注册: corpus (静态数据集)")
            else:
                logger.info("数据源跳过: corpus (数据集文件未配置)")
        except Exception as e:
            logger.warning(f"数据源注册失败: corpus - {e}")

        try:
            tianapi = TianapiAdapter()
            if tianapi.health_check().status.value != "disabled":
                self._adapters["tianapi"] = tianapi
                logger.info("数据源注册: tianapi (天行API)")
            else:
                logger.info("数据源跳过: tianapi (TIANAPI_KEY 未配置)")
        except Exception as e:
            logger.warning(f"数据源注册失败: tianapi - {e}")

        try:
            bing = BingAdapter()
            if bing.health_check().status.value != "disabled":
                self._adapters["bing"] = BingAdapter()
                logger.info("数据源注册: bing (Bing Search API)")
            else:
                logger.info("数据源跳过: bing (BING_API_KEY 未配置)")
        except Exception as e:
            logger.warning(f"数据源注册失败: bing - {e}")

        try:
            rss = RSSAdapter()
            self._adapters["rss"] = rss
            logger.info("数据源注册: rss (RSS数据源)")
        except Exception as e:
            logger.warning(f"数据源注册失败: rss - {e}")

        logger.info(f"数据源注册完成: {list(self._adapters.keys())}")

    def fetch_realtime(
        self,
        sources: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        parallel: bool = True,
    ) -> list[ArticleData]:
        """编排实时数据源获取

        Args:
            sources: 要使用的数据源列表，None=全部已注册
            keywords: 搜索关键词（Bing使用）
            parallel: 是否并行获取
        """
        realtime_sources = ["tianapi", "bing", "rss"]
        if sources:
            realtime_sources = [s for s in sources if s in self._adapters and s in realtime_sources]
        else:
            realtime_sources = [s for s in realtime_sources if s in self._adapters]

        if not realtime_sources:
            logger.warning("无可用实时数据源")
            return []

        if parallel and len(realtime_sources) > 1:
            return self._fetch_parallel(realtime_sources, keywords)
        return self._fetch_sequential(realtime_sources, keywords)

    def _fetch_sequential(self, source_keys: list[str], keywords: Optional[list[str]]) -> list[ArticleData]:
        """串行获取"""
        all_articles = []
        for key in source_keys:
            adapter = self._adapters[key]
            try:
                if key == "tianapi":
                    articles = adapter.fetch()
                elif key == "bing":
                    articles = adapter.fetch(keywords=keywords)
                elif key == "rss":
                    articles = adapter.fetch()
                else:
                    articles = adapter.fetch()

                all_articles.extend(articles)
                logger.info(f"数据源 {key} 获取 {len(articles)} 条文章")
            except Exception as e:
                logger.error(f"数据源 {key} 获取失败: {e}")

        return all_articles

    def _fetch_parallel(self, source_keys: list[str], keywords: Optional[list[str]]) -> list[ArticleData]:
        """并行获取"""
        all_articles = []
        futures = {}

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="datasource") as pool:
            for key in source_keys:
                adapter = self._adapters[key]
                if key == "tianapi":
                    future = pool.submit(adapter.fetch)
                elif key == "bing":
                    future = pool.submit(adapter.fetch, keywords=keywords)
                elif key == "rss":
                    future = pool.submit(adapter.fetch)
                else:
                    future = pool.submit(adapter.fetch)
                futures[future] = key

            for future in as_completed(futures):
                key = futures[future]
                try:
                    articles = future.result()
                    all_articles.extend(articles)
                    logger.info(f"数据源 {key} 获取 {len(articles)} 条文章")
                except Exception as e:
                    logger.error(f"数据源 {key} 获取失败: {e}")

        return all_articles

    def import_corpus(
        self,
        dataset_type: str = "news2016zh",
        resume: bool = True,
        max_count: int = 0,
        auto_download: bool = True,
    ) -> ImportResult:
        """执行静态数据集导入（未下载时自动下载样本）"""
        corpus = CorpusAdapter()

        file_path = corpus._resolve_dataset_file(dataset_type)
        if not file_path or not file_path.exists():
            if auto_download:
                try:
                    logger.info(f"数据集 {dataset_type} 未找到，开始自动下载样本...")
                    corpus.download_dataset(dataset_type)
                except Exception as e:
                    return ImportResult(dataset_type=dataset_type, error=f"下载失败: {e}")
            else:
                return ImportResult(dataset_type=dataset_type, error="数据集文件未下载且auto_download=False")

        corpus.fetch(dataset_type=dataset_type, resume=resume, max_count=max_count)
        return ImportResult(dataset_type=dataset_type, imported=1)

    def get_all_status(self) -> list[SourceHealth]:
        """聚合所有已注册适配器的健康状态"""
        return [adapter.health_check() for adapter in self._adapters.values()]

    def get_enabled_sources(self) -> list[str]:
        """返回已启用的数据源标识列表"""
        return [
            key for key, adapter in self._adapters.items()
            if adapter.health_check().status.value == "available"
        ]
