"""图片缓存管理器 - 按文章 ID 永久缓存配图，避免重复生成"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import PROJECT_ROOT


class ImageCache:
    """图片永久缓存管理

    目录结构:
        output/images/{article_id}/
            meta.json    — 场景描述 + 图片路径
            xxx_0.png    — 配图1
            xxx_1.png    — 配图2
            ...
    """

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or PROJECT_ROOT / "output" / "images"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _article_dir(self, article_id: str) -> Path:
        d = self.base_dir / article_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _meta_path(self, article_id: str) -> Path:
        return self._article_dir(article_id) / "meta.json"

    def get(self, article_id: str) -> Optional[dict]:
        """获取缓存的文章配图信息，返回 {"scenes": [...], "image_paths": [...]} 或 None"""
        meta_file = self._meta_path(article_id)
        if not meta_file.exists():
            return None

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 校验图片文件是否都存在
            valid_paths = []
            for p in data.get("image_paths", []):
                if p and Path(p).exists():
                    valid_paths.append(p)

            data["image_paths"] = valid_paths
            return data
        except Exception as e:
            logger.warning(f"[ImageCache] 读取缓存失败: {e}")
            return None

    def save(self, article_id: str, scenes: list[str], image_paths: list[str]) -> dict:
        """保存配图缓存"""
        data = {
            "article_id": article_id,
            "scenes": scenes,
            "image_paths": [p for p in image_paths if p],
        }

        meta_file = self._meta_path(article_id)
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"[ImageCache] 已缓存 {len(data['image_paths'])} 张配图: {article_id}")
        return data

    def has_cache(self, article_id: str) -> bool:
        """检查是否有完整缓存（4张图）"""
        data = self.get(article_id)
        if not data:
            return False
        return len(data.get("image_paths", [])) >= 4

    def clear(self, article_id: str) -> None:
        """清除某篇文章的配图缓存（重新生成时用）"""
        article_dir = self._article_dir(article_id)
        if article_dir.exists():
            import shutil
            shutil.rmtree(article_dir)
            logger.info(f"[ImageCache] 已清除缓存: {article_id}")


# 全局单例
image_cache = ImageCache()
