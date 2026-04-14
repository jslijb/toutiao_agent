"""文章存储管理器 — 去重、过期清理、持久化

设计原则:
  - 每次程序启动时自动清理过期文章
  - 爬取时自动跳过已存在的文章（URL 去重 + 标题去重）
  - 数据按平台分文件存储，方便管理
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import PROJECT_ROOT, settings
from models.article import ArticleData


class ArticleStore:
    """文章存储管理器"""

    def __init__(self):
        self.store_dir = PROJECT_ROOT / "data" / "store"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, ArticleData] = {}  # url -> ArticleData
        self._loaded = False  # 延迟加载标志

    def _ensure_loaded(self):
        """确保数据已加载"""
        if not self._loaded:
            self._load_all()
            self._loaded = True

    # ── 过期清理 ──────────────────────────────────────

    def cleanup_expired(self) -> int:
        """清理过期文章，返回清理数量。

        在程序启动时调用。根据每篇文章的 ttl_days 和 crawl_time 判断是否过期。
        """
        self._ensure_loaded()
        now = datetime.now()
        expired_urls = []

        for url, article in self._index.items():
            if article.ttl_days <= 0:
                continue  # 永不过期
            try:
                crawl_dt = datetime.fromisoformat(article.crawl_time)
                expire_at = crawl_dt + timedelta(days=article.ttl_days)
                if now >= expire_at:
                    expired_urls.append(url)
            except (ValueError, TypeError):
                # crawl_time 格式异常，视为不过期
                pass

        for url in expired_urls:
            del self._index[url]

        if expired_urls:
            self._save_all()
            logger.info(f"[ArticleStore] 清理 {len(expired_urls)} 篇过期文章")

        return len(expired_urls)

    # ── 去重 ──────────────────────────────────────────

    def is_duplicate(self, article: ArticleData) -> bool:
        """检查文章是否已存在

        三级去重策略:
          1. URL 精确匹配 → 直接判重
          2. 标题+来源精确匹配 → 判重（URL 为空时）
          3. 语义去重（标题 Jaccard + 正文 SimHash）→ 可通过配置开关
        """
        self._ensure_loaded()
        # 1. URL 精确匹配
        if article.url and article.url in self._index:
            return True

        # 2. URL 为空时用标题+来源精确匹配
        if not article.url:
            for existing in self._index.values():
                if existing.title == article.title and existing.source == article.source:
                    return True

        # 3. 语义去重（可配置开关）
        if settings.dedup.enable_semantic_dedup:
            return self._semantic_dedup_check(article)

        return False

    def _semantic_dedup_check(self, article: ArticleData) -> bool:
        """语义去重检查：标题 Jaccard + 正文 SimHash"""
        from rag.dedup import ArticleDeduplicator, _tokenize_for_jaccard, jaccard_similarity, SimHash, _tokenize_chinese

        title_threshold = settings.dedup.title_threshold
        content_hamming = settings.dedup.content_hamming_threshold
        min_content_len = settings.dedup.min_content_length

        new_title_tokens = _tokenize_for_jaccard(article.title)

        for existing in self._index.values():
            # 同一平台的文章才比较语义
            if existing.source != article.source:
                continue
            # 跳过自身
            if article.url and existing.url == article.url:
                continue

            # 标题 Jaccard 相似度
            if new_title_tokens and existing.title:
                existing_title_tokens = _tokenize_for_jaccard(existing.title)
                sim = jaccard_similarity(new_title_tokens, existing_title_tokens)
                if sim > title_threshold:
                    logger.debug(
                        f"[Dedup] 标题相似: '{article.title[:30]}' ≈ "
                        f"'{existing.title[:30]}' (Jaccard={sim:.2f})"
                    )
                    return True

            # 正文 SimHash
            if (article.content and len(article.content) >= min_content_len
                    and existing.content and len(existing.content) >= min_content_len):
                new_sh = SimHash(_tokenize_chinese(article.content))
                existing_sh = SimHash(_tokenize_chinese(existing.content))
                hamming = new_sh.hamming_distance(existing_sh)
                if hamming <= content_hamming:
                    logger.debug(
                        f"[Dedup] 内容相似: '{article.title[:30]}' ≈ "
                        f"'{existing.title[:30]}' (Hamming={hamming})"
                    )
                    return True

        return False

    def add(self, article: ArticleData) -> bool:
        """添加文章，已存在则跳过。返回是否为新文章。"""
        self._ensure_loaded()
        if self.is_duplicate(article):
            return False
        if article.url:
            self._index[article.url] = article
        else:
            # URL 为空时用临时 key
            self._index[f"_no_url_{article.id}"] = article
        return True

    def add_many(self, articles: list[ArticleData]) -> int:
        """批量添加文章，返回实际新增数量"""
        self._ensure_loaded()
        added = 0
        for art in articles:
            if self.add(art):
                added += 1
        if added > 0:
            self._save_all()
        return added

    # ── 查询 ──────────────────────────────────────────

    def get_all(self) -> list[ArticleData]:
        """获取所有文章"""
        self._ensure_loaded()
        return list(self._index.values())

    def get_by_platform(self, platform: str) -> list[ArticleData]:
        """按平台获取文章"""
        self._ensure_loaded()
        return [a for a in self._index.values() if a.source == platform]

    def get_with_content(self, min_chars: int = 100) -> list[ArticleData]:
        """获取有正文内容的文章（用于 RAG 知识库）"""
        self._ensure_loaded()
        return [a for a in self._index.values() if len(a.content) >= min_chars]

    def count(self) -> int:
        self._ensure_loaded()
        return len(self._index)

    def count_by_platform(self) -> dict[str, int]:
        self._ensure_loaded()
        counts: dict[str, int] = {}
        for a in self._index.values():
            counts[a.source] = counts.get(a.source, 0) + 1
        return counts

    # ── 持久化 ────────────────────────────────────────

    def _store_file(self) -> Path:
        return self.store_dir / "articles.json"

    def _load_all(self):
        """从磁盘加载所有文章"""
        filepath = self._store_file()
        if not filepath.exists():
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            for item in data:
                try:
                    article = ArticleData.from_dict(item)
                    key = article.url if article.url else f"_no_url_{article.id}"
                    self._index[key] = article
                except Exception:
                    continue

            logger.info(f"[ArticleStore] 加载 {len(self._index)} 篇文章")
        except Exception as e:
            logger.warning(f"[ArticleStore] 加载失败: {e}")

    def _save_all(self):
        """保存所有文章到磁盘"""
        filepath = self._store_file()
        try:
            data = [a.to_dict() for a in self._index.values()]
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[ArticleStore] 保存失败: {e}")


# 全局单例
_article_store: Optional[ArticleStore] = None


def get_article_store() -> ArticleStore:
    """获取全局 ArticleStore 单例"""
    global _article_store
    if _article_store is None:
        _article_store = ArticleStore()
        # 启动时自动清理过期文章（异步执行）
        import threading
        def cleanup_task():
            try:
                _article_store.cleanup_expired()
            except Exception as e:
                logger.warning(f"[ArticleStore] 清理过期文章失败: {e}")
        threading.Thread(target=cleanup_task, daemon=True).start()
    return _article_store
