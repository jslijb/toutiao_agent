"""Pipeline 编排器 - 爬虫/RAG批处理流程串联"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import PROJECT_ROOT, settings
from models.article import ArticleData
from models.article_store import get_article_store
from models.pipeline import PipelineContext, StageResult, StageStatus
from rag.cleaner import TextCleaner
from rag.chunker import TextChunker
from rag.embedder import DashScopeEmbedder
from rag.vectorstore import FAISSVectorStore
from rag.retriever import Retriever


class CrawlPipeline:
    """爬虫 Pipeline: 多平台并行爬取 → 去重 → 详情页抓取 → 存储"""

    def __init__(self):
        from crawlers.toutiao_crawler import ToutiaoCrawler
        from crawlers.zhihu_crawler import ZhihuCrawler
        from crawlers.wechat_crawler import WechatCrawler
        from crawlers.baijiahao_crawler import BaijiahaoCrawler
        from crawlers.kr36_crawler import Kr36Crawler

        self.crawlers = {
            "toutiao": ToutiaoCrawler(),
            "zhihu": ZhihuCrawler(),
            "wechat": WechatCrawler(),
            "baijiahao": BaijiahaoCrawler(),
            "kr36": Kr36Crawler(),
        }
        self.raw_dir = PROJECT_ROOT / "data" / "raw"

    def run(
        self,
        platforms: list[str] | None = None,
        keywords: list[str] | None = None,
        max_per_platform: int | None = None,
        parallel: bool = True,
    ) -> PipelineContext:
        """执行爬虫 Pipeline（平台间并行爬取）

        Args:
            platforms: 要爬取的平台列表
            keywords: 搜索关键词
            max_per_platform: 每平台最大文章数
            parallel: 是否平台间并行（默认True）
        """
        context = PipelineContext()
        platforms = platforms or ["toutiao", "zhihu", "kr36"]
        max_per_platform = max_per_platform or settings.crawler.max_articles_per_platform

        start_time = datetime.now()
        result = StageResult(
            stage_name="crawl",
            status=StageStatus.RUNNING,
            started_at=start_time.isoformat(),
        )

        try:
            if parallel and len(platforms) > 1:
                all_articles = self._run_parallel(platforms, keywords, max_per_platform)
            else:
                all_articles = self._run_sequential(platforms, keywords, max_per_platform)

            # 统计有正文的文章
            with_content = sum(1 for a in all_articles if len(a.content) >= 200)
            store = get_article_store()
            store_stats = store.count_by_platform()

            context.articles = all_articles
            result.status = StageStatus.SUCCESS
            result.message = (
                f"共爬取 {len(all_articles)} 篇文章"
                f"（有正文: {with_content}, "
                f"知识库总计: {store.count()} 篇）"
            )

        except Exception as e:
            result.status = StageStatus.FAILED
            result.message = str(e)
            logger.error(f"爬虫 Pipeline 失败: {e}")

        result.finished_at = datetime.now().isoformat()
        context.add_stage_result(result)
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"[Pipeline] 爬虫完成: {result.message} (耗时 {elapsed:.0f}秒)")

        # 清理浏览器池，释放资源
        try:
            from crawlers.base import cleanup_browser_pool
            cleanup_browser_pool()
        except Exception:
            pass

        return context

    def _run_sequential(self, platforms, keywords, max_per_platform) -> list[ArticleData]:
        """串行爬取（降级模式）"""
        all_articles = []
        for platform in platforms:
            crawler = self.crawlers.get(platform)
            if not crawler:
                logger.warning(f"未知平台: {platform}")
                continue
            logger.info(f"{'='*50}")
            logger.info(f"开始爬取: {platform}")
            logger.info(f"{'='*50}")
            articles = crawler.crawl(keywords=keywords, max_count=max_per_platform)
            all_articles.extend(articles)
            self._save_crawled_data(platform, articles)
        return all_articles

    def _run_parallel(self, platforms, keywords, max_per_platform) -> list[ArticleData]:
        """平台间并行爬取"""
        all_articles = []
        futures = {}

        # 限制最多3个并行线程（避免触发反爬）
        max_workers = min(len(platforms), 3)

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="crawl") as pool:
            for platform in platforms:
                crawler = self.crawlers.get(platform)
                if not crawler:
                    logger.warning(f"未知平台: {platform}")
                    continue
                logger.info(f"{'='*50}")
                logger.info(f"提交爬取任务: {platform}")
                logger.info(f"{'='*50}")
                future = pool.submit(crawler.crawl, keywords=keywords, max_count=max_per_platform)
                futures[future] = platform

            for future in as_completed(futures):
                platform = futures[future]
                try:
                    articles = future.result()
                    all_articles.extend(articles)
                    self._save_crawled_data(platform, articles)
                    logger.info(f"[Pipeline] {platform} 完成: {len(articles)} 篇")
                except Exception as e:
                    logger.error(f"[Pipeline] {platform} 失败: {e}")

        return all_articles

    def _save_crawled_data(self, platform: str, articles: list[ArticleData]):
        """保存爬取数据到 JSON（兼容旧逻辑）"""
        today = datetime.now().strftime("%Y%m%d")
        filename = f"{platform}_{today}.json"
        filepath = self.raw_dir / platform / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        data = [a.to_dict() for a in articles]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[Pipeline] 原始数据已保存: {filepath}")


class RAGPipeline:
    """RAG 知识库构建 Pipeline: 文本清洗 → 分块 → Embedding → FAISS 索引"""

    def __init__(self):
        self.cleaner = TextCleaner()
        self.chunker = TextChunker(chunk_size=500, chunk_overlap=100)
        self.embedder = DashScopeEmbedder()
        self.vectorstore = FAISSVectorStore()

    def run(self, articles: list[ArticleData] | None = None, force_full: bool = False) -> PipelineContext:
        """构建 RAG 知识库

        Args:
            articles: 文章列表（为空则从 ArticleStore 加载）
            force_full: 是否强制全量重建（清空现有索引后重新构建）
        """
        context = PipelineContext()
        start_time = datetime.now()
        result = StageResult(
            stage_name="rag_build",
            status=StageStatus.RUNNING,
            started_at=start_time.isoformat(),
        )

        try:
            if not articles:
                # 优先从 ArticleStore 加载（有正文的文章）
                articles = self._load_articles_from_store()
                if not articles:
                    # 降级：从旧 JSON 文件加载
                    articles = self._load_all_crawled_data()
                if not articles:
                    result.status = StageStatus.FAILED
                    result.message = "没有可用的爬取数据，请先执行爬虫"
                    result.finished_at = datetime.now().isoformat()
                    context.add_stage_result(result)
                    return context

            # 只处理有正文的文章
            valid_articles = [a for a in articles if len(a.content) >= 100]
            if len(valid_articles) < len(articles):
                logger.info(
                    f"[Pipeline] 跳过 {len(articles) - len(valid_articles)} 篇无正文文章"
                )

            # 全量重建模式
            if force_full:
                logger.info("[Pipeline] 强制全量重建: 清空现有索引...")
                self.vectorstore.clear()

            # 尝试增量构建（非全量重建时）
            if not force_full:
                loaded = self.vectorstore.load() if self.vectorstore.total == 0 else True
                if loaded and self.vectorstore.total > 0:
                    # 检测维度是否匹配 — 如果换了模型需要全量重建
                    if self.vectorstore.index is not None:
                        index_dim = self.vectorstore.index.d
                        config_dim = settings.models.embedding.dimension
                        if index_dim != config_dim:
                            logger.warning(
                                f"[Pipeline] 检测到维度变更: 索引={index_dim}d, 配置={config_dim}d, "
                                f"自动全量重建"
                            )
                            self.vectorstore.clear()
                            logger.info(f"[Pipeline] 全量重建 RAG 知识库，共 {len(valid_articles)} 篇有效文章...")
                            self._build_and_add(valid_articles)
                            context.chunks = []
                            result.status = StageStatus.SUCCESS
                            result.message = f"RAG 知识库构建完成（维度变更重建）: {self.vectorstore.total} 条向量, {len(self.vectorstore.indexed_urls)} 篇文章"
                            result.finished_at = datetime.now().isoformat()
                            context.add_stage_result(result)
                            logger.info(f"[Pipeline] RAG 完成: {result.message}")
                            return context

                    # 增量模式：过滤已索引的文章
                    already_indexed = self.vectorstore.indexed_urls
                    new_articles = [a for a in valid_articles if a.url not in already_indexed]

                    if not new_articles:
                        logger.info(f"[Pipeline] 无新文章需要索引（已索引 {len(already_indexed)} 篇）")
                        result.status = StageStatus.SUCCESS
                        result.message = f"RAG 索引已是最新: {self.vectorstore.total} 条向量, {len(already_indexed)} 篇文章"
                        result.finished_at = datetime.now().isoformat()
                        context.add_stage_result(result)
                        return context

                    logger.info(f"[Pipeline] 增量索引: {len(new_articles)} 篇新文章 (已索引 {len(already_indexed)} 篇)")
                    self._build_and_add(new_articles)
                else:
                    # 全量模式：首次构建
                    logger.info(f"[Pipeline] 开始构建 RAG 知识库，共 {len(valid_articles)} 篇有效文章...")
                    self._build_and_add(valid_articles)
            else:
                # 全量重建：构建所有文章
                logger.info(f"[Pipeline] 全量重建 RAG 知识库，共 {len(valid_articles)} 篇有效文章...")
                self._build_and_add(valid_articles)

            context.chunks = []
            result.status = StageStatus.SUCCESS
            result.message = f"RAG 知识库构建完成: {self.vectorstore.total} 条向量, {len(self.vectorstore.indexed_urls)} 篇文章"

        except Exception as e:
            result.status = StageStatus.FAILED
            result.message = str(e)
            logger.error(f"[Pipeline] RAG 构建失败: {e}")

        result.finished_at = datetime.now().isoformat()
        context.add_stage_result(result)
        logger.info(f"[Pipeline] RAG 完成: {result.message}")
        return context

    def rebuild_incremental(self) -> PipelineContext:
        """增量重建：清理零向量 + 重新索引未索引的文章

        适用场景：
        - embedding 模型更换后维度不匹配，需全量重建
        - embedding API 临时报错导致零向量，需清理并补齐
        - 部分文章未成功索引，需补齐
        """
        context = PipelineContext()
        start_time = datetime.now()
        result = StageResult(
            stage_name="rag_rebuild_incremental",
            status=StageStatus.RUNNING,
            started_at=start_time.isoformat(),
        )

        try:
            # 检查维度是否匹配 — 如果换了模型需要全量重建
            loaded = self.vectorstore.load()
            current_dim = settings.models.embedding.dimension

            if loaded and self.vectorstore.index is not None:
                index_dim = self.vectorstore.index.d
                if index_dim != current_dim:
                    logger.warning(
                        f"[Pipeline] 检测到维度变更: 索引={index_dim}d, 配置={current_dim}d, "
                        f"需要全量重建"
                    )
                    # 维度不同，必须全量重建
                    self.vectorstore.clear()
                    articles = self._load_articles_from_store()
                    if not articles:
                        articles = self._load_all_crawled_data()
                    if not articles:
                        result.status = StageStatus.FAILED
                        result.message = "没有可用的文章数据"
                        result.finished_at = datetime.now().isoformat()
                        context.add_stage_result(result)
                        return context

                    valid_articles = [a for a in articles if len(a.content) >= 100]
                    logger.info(f"[Pipeline] 维度变更全量重建: {len(valid_articles)} 篇文章")
                    self._build_and_add(valid_articles)

                    result.status = StageStatus.SUCCESS
                    result.message = (
                        f"维度变更全量重建完成: {self.vectorstore.total} 条向量, "
                        f"{len(self.vectorstore.indexed_urls)} 篇文章 "
                        f"(维度 {index_dim}d → {current_dim}d)"
                    )
                    result.finished_at = datetime.now().isoformat()
                    context.add_stage_result(result)
                    return context

            # 维度匹配 → 清理零向量 + 增量补齐
            zero_removed = 0
            if loaded and self.vectorstore.total > 0:
                zero_removed = self.vectorstore.remove_zero_vectors()

            # 找出未索引的文章
            already_indexed = self.vectorstore.indexed_urls if loaded else set()
            articles = self._load_articles_from_store()
            if not articles:
                articles = self._load_all_crawled_data()

            valid_articles = [a for a in articles if len(a.content) >= 100]
            new_articles = [a for a in valid_articles if a.url not in already_indexed]

            if new_articles:
                logger.info(f"[Pipeline] 增量补齐: {len(new_articles)} 篇未索引文章")
                self._build_and_add(new_articles)

            total_vectors = self.vectorstore.total
            total_articles = len(self.vectorstore.indexed_urls)

            result.status = StageStatus.SUCCESS
            parts = []
            if zero_removed > 0:
                parts.append(f"清理 {zero_removed} 条零向量")
            if new_articles:
                parts.append(f"补齐 {len(new_articles)} 篇新文章")
            if not parts:
                parts.append("索引已是最新")

            result.message = (
                f"增量重建完成: {total_vectors} 条向量, {total_articles} 篇文章 "
                f"({', '.join(parts)})"
            )

        except Exception as e:
            result.status = StageStatus.FAILED
            result.message = str(e)
            logger.error(f"[Pipeline] 增量重建失败: {e}")

        result.finished_at = datetime.now().isoformat()
        context.add_stage_result(result)
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"[Pipeline] 增量重建完成: {result.message} (耗时 {elapsed:.0f}秒)")
        return context

    def _build_and_add(self, articles: list[ArticleData]):
        """清洗、分块、向量化并添加到索引（自动去重）"""
        # RAG 知识库内部去重检查
        if settings.dedup.check_rag_duplicates:
            articles = self._dedup_before_index(articles)

        all_chunks = []
        all_metas = []
        all_urls = []

        for article in articles:
            cleaned_content = self.cleaner.clean(article.content)
            if not cleaned_content:
                continue
            chunks = self.chunker.chunk_text(cleaned_content)

            for chunk in chunks:
                all_chunks.append(chunk)
                all_metas.append({
                    "text": chunk,
                    "title": article.title,
                    "source": article.source,
                    "url": article.url,
                    "author": article.author,
                    "quality_score": article.quality_score,
                })
                all_urls.append(article.url)

        logger.info(f"[Pipeline] 文本分块完成: {len(all_chunks)} 个块")

        if not all_chunks:
            return

        # Embedding
        logger.info(f"[Pipeline] 开始向量化 {len(all_chunks)} 个块...")
        vectors = self.embedder.embed_texts(all_chunks)
        logger.info(f"[Pipeline] 向量化完成: {vectors.shape}")

        # 追加到索引
        self.vectorstore.add_vectors(vectors, all_metas, all_urls)
        self.vectorstore.save()

    def _load_articles_from_store(self) -> list[ArticleData]:
        """从 ArticleStore 加载有正文的文章"""
        store = get_article_store()
        articles = store.get_with_content(min_chars=100)
        logger.info(f"[Pipeline] 从 ArticleStore 加载 {len(articles)} 篇有正文的文章")
        return articles

    def _load_all_crawled_data(self) -> list[ArticleData]:
        """从旧 JSON 文件加载已爬取数据（降级兼容）"""
        raw_dir = PROJECT_ROOT / "data" / "raw"
        articles = []

        if not raw_dir.exists():
            return articles

        for platform_dir in raw_dir.iterdir():
            if not platform_dir.is_dir():
                continue
            for json_file in platform_dir.glob("*.json"):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for item in data:
                        articles.append(ArticleData.from_dict(item))
                except Exception as e:
                    logger.warning(f"加载 {json_file} 失败: {e}")

        logger.info(f"[Pipeline] 从旧 JSON 文件加载 {len(articles)} 篇文章")
        return articles

    def get_retriever(self) -> Retriever:
        """获取检索器（需要先 load 或 run）"""
        if self.vectorstore.total == 0:
            self.vectorstore.load()
        return Retriever(vectorstore=self.vectorstore, embedder=self.embedder)

    def _dedup_before_index(self, articles: list[ArticleData]) -> list[ArticleData]:
        """RAG 索引前去重：检查待索引文章之间的内部重复"""
        from rag.dedup import ArticleDeduplicator

        if len(articles) < 2:
            return articles

        dedup = ArticleDeduplicator(
            title_threshold=settings.dedup.title_threshold,
            content_hamming_threshold=settings.dedup.content_hamming_threshold,
            min_content_length=settings.dedup.min_content_length,
        )
        article_dicts = [
            {"url": a.url, "title": a.title, "content": a.content, "source": a.source}
            for a in articles
        ]
        result = dedup.check_existing_articles(article_dicts)

        if result.duplicates > 0:
            duplicate_urls = {info.duplicate_of for info in result.details if info.is_duplicate}
            filtered = [a for a in articles if a.url not in duplicate_urls]
            logger.info(
                f"[Pipeline] RAG 索引前去重: {len(articles)} → {len(filtered)} 篇 "
                f"(移除 {result.duplicates} 篇重复)"
            )
            return filtered

        return articles

    def check_rag_duplicates(self) -> str:
        """检查 RAG 知识库中已存在的重复文章（人工触发）"""
        from rag.dedup import ArticleDeduplicator

        articles = self._load_articles_from_store()
        if not articles:
            articles = self._load_all_crawled_data()
        if not articles:
            return "知识库中没有文章，无法检查重复。"

        article_dicts = [
            {"url": a.url, "title": a.title, "content": a.content, "source": a.source}
            for a in articles
        ]

        dedup = ArticleDeduplicator(
            title_threshold=settings.dedup.title_threshold,
            content_hamming_threshold=settings.dedup.content_hamming_threshold,
            min_content_length=settings.dedup.min_content_length,
        )
        result = dedup.check_existing_articles(article_dicts)

        lines = [
            f"检查结果: 共 {result.total} 篇，{result.unique} 篇唯一，"
            f"{result.duplicates} 篇重复 (重复率 {result.duplicate_rate:.1%})\n"
        ]

        if result.details:
            lines.append("重复文章详情:")
            for info in result.details:
                lines.append(
                    f"  - \"{info.article_title[:40]}\" ≈ "
                    f"\"{info.duplicate_title[:40]}\" "
                    f"(类型={info.match_type}, "
                    f"标题相似={info.title_similarity:.2f}, "
                    f"内容相似={info.content_similarity:.2f})"
                )
        else:
            lines.append("未发现重复文章，知识库很干净！")

        return "\n".join(lines)
