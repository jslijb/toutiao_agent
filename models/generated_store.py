"""生成文章的持久化存储管理器

存储位置: output/articles/articles.json
功能: 保存/加载/查询 GeneratedArticle 列表
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import PROJECT_ROOT
from models.article import GeneratedArticle


class GeneratedArticleStore:
    """生成文章的持久化存储"""

    def __init__(self):
        self.store_dir = PROJECT_ROOT / "output" / "articles"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._articles: list[GeneratedArticle] = []
        self._loaded = False  # 延迟加载标志

    def _ensure_loaded(self):
        """确保数据已加载"""
        if not self._loaded:
            self._load()
            self._loaded = True

    def _store_file(self) -> Path:
        return self.store_dir / "articles.json"

    def _load(self):
        """从磁盘加载"""
        filepath = self._store_file()
        if not filepath.exists():
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._articles = [GeneratedArticle(**item) for item in data]
            logger.info(f"[GeneratedStore] 加载 {len(self._articles)} 篇生成文章")
        except Exception as e:
            logger.warning(f"[GeneratedStore] 加载失败: {e}")
            self._articles = []

    def _save(self):
        """保存到磁盘"""
        filepath = self._store_file()
        try:
            data = [a.to_dict() for a in self._articles]
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[GeneratedStore] 保存失败: {e}")

    def add(self, article: GeneratedArticle) -> str:
        """添加生成文章，返回文章 ID"""
        self._ensure_loaded()
        self._articles.append(article)
        self._save()
        logger.info(f"[GeneratedStore] 保存生成文章: {article.id} - {article.title[:30]}")
        return article.id

    def get(self, article_id: str) -> Optional[GeneratedArticle]:
        """按 ID 获取"""
        self._ensure_loaded()
        for a in self._articles:
            if a.id == article_id:
                return a
        return None

    def get_all(self) -> list[GeneratedArticle]:
        """获取所有生成文章（最新在前）"""
        self._ensure_loaded()
        return list(reversed(self._articles))

    def get_published(self) -> list[GeneratedArticle]:
        """获取已发布的文章"""
        self._ensure_loaded()
        return [a for a in self._articles if a.status == "published"]

    def get_drafts(self) -> list[GeneratedArticle]:
        """获取草稿"""
        self._ensure_loaded()
        return [a for a in self._articles if a.status == "draft"]

    def update(self, article_id: str, **kwargs) -> bool:
        """更新文章字段"""
        self._ensure_loaded()
        article = self.get(article_id)
        if not article:
            return False
        for key, value in kwargs.items():
            if hasattr(article, key):
                setattr(article, key, value)
        self._save()
        return True

    def delete(self, article_id: str) -> bool:
        """删除文章"""
        self._ensure_loaded()
        before = len(self._articles)
        self._articles = [a for a in self._articles if a.id != article_id]
        if len(self._articles) < before:
            self._save()
            return True
        return False

    def count(self) -> int:
        self._ensure_loaded()
        return len(self._articles)

    def count_by_status(self) -> dict[str, int]:
        self._ensure_loaded()
        counts: dict[str, int] = {}
        for a in self._articles:
            counts[a.status] = counts.get(a.status, 0) + 1
        return counts


# 全局单例
_gen_store: Optional[GeneratedArticleStore] = None


def get_generated_store() -> GeneratedArticleStore:
    """获取全局单例"""
    global _gen_store
    if _gen_store is None:
        _gen_store = GeneratedArticleStore()
    return _gen_store
