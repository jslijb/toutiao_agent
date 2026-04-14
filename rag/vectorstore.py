"""FAISS 向量索引管理"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Optional

import faiss
import numpy as np
from loguru import logger

from config.settings import settings, PROJECT_ROOT


class FAISSVectorStore:
    """FAISS 向量索引管理器"""

    def __init__(self, index_dir: str | Path | None = None, dimension: Optional[int] = None):
        self.index_dir = Path(index_dir) if index_dir else PROJECT_ROOT / "data" / "db"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.dimension = dimension or settings.models.embedding.dimension

        self.index_path = self.index_dir / "faiss.index"
        self.meta_path = self.index_dir / "faiss_meta.json"
        self.indexed_urls_path = self.index_dir / "indexed_urls.json"

        self.index: Optional[faiss.IndexFlatIP] = None
        self.id_map: dict[int, dict] = {}  # 向量ID -> 元数据
        self.indexed_urls: set[str] = set()  # 已索引的文章URL

    def _normalize(self, vectors: np.ndarray) -> np.ndarray:
        """L2 归一化（配合 IndexFlatIP 实现余弦相似度）"""
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        return vectors / norms

    def create_index(self, vectors: np.ndarray, metas: list[dict] | None = None, urls: list[str] | None = None):
        """创建 FAISS 索引"""
        if len(vectors) == 0:
            logger.warning("空向量列表，跳过索引创建")
            return

        n, d = vectors.shape
        assert d == self.dimension, f"向量维度不匹配: 期望 {self.dimension}, 实际 {d}"

        # L2 归一化
        vectors = self._normalize(vectors)

        self.index = faiss.IndexFlatIP(d)
        self.index.add(vectors)

        # 存储元数据映射
        if metas:
            self.id_map = {i: meta for i, meta in enumerate(metas)}
        else:
            self.id_map = {i: {} for i in range(n)}

        # 记录已索引的文章URL
        if urls:
            self.indexed_urls = set(urls)

        logger.info(f"FAISS 索引已创建: {n} 条向量, 维度 {d}")

    def save(self):
        """持久化索引和元数据到磁盘"""
        if self.index is None:
            logger.warning("索引为空，跳过保存")
            return

        faiss.write_index(self.index, str(self.index_path))
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self.id_map, f, ensure_ascii=False, indent=2)
        with open(self.indexed_urls_path, "w", encoding="utf-8") as f:
            json.dump(sorted(self.indexed_urls), f, ensure_ascii=False, indent=2)

        logger.info(f"索引已保存: {self.index_path}")

    def load(self) -> bool:
        """从磁盘加载索引"""
        if not self.index_path.exists():
            logger.warning(f"索引文件不存在: {self.index_path}")
            return False

        try:
            self.index = faiss.read_index(str(self.index_path))
            if self.meta_path.exists():
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self.id_map = json.load(f)
                    self.id_map = {int(k): v for k, v in self.id_map.items()}
            if self.indexed_urls_path.exists():
                with open(self.indexed_urls_path, "r", encoding="utf-8") as f:
                    self.indexed_urls = set(json.load(f))
            logger.info(f"索引已加载: {self.index.ntotal} 条向量, {len(self.indexed_urls)} 篇文章")
            return True
        except Exception as e:
            logger.error(f"索引加载失败: {e}")
            return False

    def search(self, query_vector: np.ndarray, top_k: int = 10) -> list[tuple[int, float, dict]]:
        """搜索最相似的向量，返回 [(id, score, metadata), ...]"""
        if self.index is None or self.index.ntotal == 0:
            return []

        # 归一化查询向量
        query_vector = self._normalize(query_vector.reshape(1, -1))

        scores, indices = self.index.search(query_vector, min(top_k, self.index.ntotal))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            meta = self.id_map.get(int(idx), {})
            results.append((int(idx), float(score), meta))

        return results

    def clear(self):
        """清空索引和元数据（全量重建前调用）"""
        self.index = None
        self.id_map = {}
        self.indexed_urls = set()
        logger.info("FAISS 索引已清空")

    def remove_zero_vectors(self) -> int:
        """移除零向量（embedding 失败产生的占位符），返回移除数量。

        当 embedding API 报错时，会填充零向量。此方法检测并移除这些
        无效向量，同时重建索引以保证 ID 连续。
        """
        if self.index is None or self.index.ntotal == 0:
            return 0

        # 提取所有向量，找出零向量
        n = self.index.ntotal
        all_vectors = np.zeros((n, self.dimension), dtype=np.float32)
        for i in range(n):
            all_vectors[i] = self.index.reconstruct(i)

        # 检测零向量（L2范数为0）
        norms = np.linalg.norm(all_vectors, axis=1)
        zero_mask = norms == 0
        zero_count = int(np.sum(zero_mask))

        if zero_count == 0:
            logger.info("未发现零向量，无需清理")
            return 0

        valid_mask = ~zero_mask
        valid_count = int(np.sum(valid_mask))
        logger.info(f"发现 {zero_count} 条零向量，{valid_count} 条有效向量")

        # 提取有效向量和元数据
        valid_vectors = all_vectors[valid_mask]
        valid_metas = []
        valid_urls = []
        old_id_to_new = {}
        new_id = 0

        for old_id in range(n):
            if valid_mask[old_id]:
                valid_metas.append(self.id_map.get(old_id, {}))
                url = self.id_map.get(old_id, {}).get("url", "")
                if url:
                    valid_urls.append(url)
                old_id_to_new[old_id] = new_id
                new_id += 1

        # 重建索引
        self.index = faiss.IndexFlatIP(self.dimension)
        valid_vectors = self._normalize(valid_vectors)
        self.index.add(valid_vectors)

        # 重建 id_map
        self.id_map = {new_id: meta for new_id, meta in enumerate(valid_metas)}

        # 更新 indexed_urls（移除被删除的 URL）
        removed_urls = set()
        for old_id in range(n):
            if zero_mask[old_id]:
                url = self.id_map.get(old_id, {}).get("url", "")
                if url:
                    removed_urls.add(url)
        self.indexed_urls -= removed_urls

        # 保存
        self.save()
        logger.info(f"零向量清理完成: 移除 {zero_count} 条, 保留 {self.index.ntotal} 条")
        return zero_count

    def get_zero_vector_urls(self) -> list[str]:
        """获取零向量对应的文章 URL 列表（用于增量重建）"""
        if self.index is None or self.index.ntotal == 0:
            return []

        n = self.index.ntotal
        zero_urls = []

        for i in range(n):
            vec = self.index.reconstruct(i)
            norm = np.linalg.norm(vec)
            if norm == 0:
                url = self.id_map.get(i, {}).get("url", "")
                if url:
                    zero_urls.append(url)

        return zero_urls

    @property
    def total(self) -> int:
        """索引中的向量总数"""
        return self.index.ntotal if self.index else 0

    def add_vectors(self, vectors: np.ndarray, metas: list[dict] | None = None, urls: list[str] | None = None):
        """向现有索引中追加向量

        Args:
            vectors: 向量矩阵 (n, d)
            metas: 元数据列表
            urls: 对应的文章URL列表（用于增量索引时去重）

        Raises:
            ValueError: 向量维度与索引维度不匹配
        """
        if len(vectors) == 0:
            return

        # 维度校验
        if self.index is not None and vectors.shape[1] != self.index.d:
            raise ValueError(
                f"向量维度({vectors.shape[1]})与索引维度({self.index.d})不匹配，"
                f"请先清空索引或使用 rebuild_incremental 重建"
            )

        vectors = self._normalize(vectors)

        if self.index is None:
            self.create_index(vectors, metas)
            if urls:
                self.indexed_urls.update(urls)
            return

        start_id = self.index.ntotal
        self.index.add(vectors)

        if metas:
            for i, meta in enumerate(metas):
                self.id_map[start_id + i] = meta

        if urls:
            self.indexed_urls.update(urls)

        logger.info(f"已追加 {len(vectors)} 条向量，当前总数: {self.index.ntotal}")
