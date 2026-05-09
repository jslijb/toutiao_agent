"""图片重新生成服务 - 封装配图重新生成的核心逻辑"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

from loguru import logger

from config.settings import PROJECT_ROOT
from image_gen.cartoon_gen import WanxImageGenerator
from image_gen.prompt_builder import CartoonPromptBuilder
from image_gen.scene_extractor import SceneExtractor
from models.article import GeneratedArticle
from models.generated_store import get_generated_store
from utils.image_cache import image_cache


@dataclass
class RegenProgress:
    article_id: str
    scope: str
    total: int = 4
    completed: int = 0
    success_count: int = 0
    current_paths: list[str | None] = field(default_factory=lambda: [None] * 4)
    status_message: str = ""


@dataclass
class FailureStatus:
    failure_type: str
    failed_indices: list[int]
    success_count: int
    error_messages: dict[int, str]


class ImageRegenService:
    """图片重新生成服务 - 封装配图重新生成的核心逻辑"""

    def __init__(self):
        self.generator: WanxImageGenerator | None = None
        self.prompt_builder = CartoonPromptBuilder()

    def regenerate_article_images(
        self,
        article: GeneratedArticle,
        scope: str = "all",
    ) -> Generator[tuple[list[str | None], str], None, None]:
        if not article:
            yield ([None] * 4, "请先在「文章生成」Tab 中生成文章")
            return

        scenes = self.get_scenes_with_fallback(article, n=4)

        if scope == "all":
            image_cache.clear(article.id)
            targets = [0, 1, 2, 3]
            result_paths: list[str | None] = [None] * 4
        else:
            cached = image_cache.get(article.id)
            if cached and cached.get("image_paths"):
                existing_paths = cached["image_paths"]
            else:
                existing_paths = article.image_paths

            padded_paths: list[str | None] = list(existing_paths) + [None] * (4 - len(existing_paths))
            padded_paths = padded_paths[:4]

            failure_status = self.detect_failure_status(padded_paths)
            targets = failure_status.failed_indices

            result_paths = list(padded_paths)

        if not targets:
            yield (result_paths, "所有配图均有效，无需重新生成")
            return

        article_img_dir = PROJECT_ROOT / "output" / "images" / article.id
        article_img_dir.mkdir(parents=True, exist_ok=True)

        self.generator = WanxImageGenerator(output_dir=article_img_dir)

        completed = 0
        for i in targets:
            prompt = self.prompt_builder.build(scenes[i])
            logger.info(f"[重新生成] 配图 {i+1}/4: {scenes[i][:30]}...")

            try:
                paths = self.generator.generate(prompt=prompt, n=1)
                if paths:
                    result_paths[i] = paths[0]
                    logger.info(f"[重新生成] 配图 {i+1} 完成: {paths[0]}")
                else:
                    result_paths[i] = None
                    logger.warning(f"[重新生成] 配图 {i+1} 失败: 返回空列表")
            except Exception as e:
                result_paths[i] = None
                logger.error(f"[重新生成] 配图 {i+1} 异常: {e}")

            completed += 1
            yield (list(result_paths), f"重新生成中 {completed}/{len(targets)}...")

        valid_paths = [p for p in result_paths if p]
        image_cache.save(article.id, scenes, valid_paths)

        article.image_paths = valid_paths
        try:
            store = get_generated_store()
            store.update(article.id, image_paths=valid_paths)
        except Exception as e:
            logger.warning(f"[重新生成] 更新文章存储失败: {e}")

        n_success = sum(1 for p in result_paths if p)
        yield (list(result_paths), f"重新生成完成: {n_success}/4 张")

    def detect_failure_status(
        self,
        image_paths: list[str | None],
    ) -> FailureStatus:
        failed_indices = []
        error_messages = {}

        for i, p in enumerate(image_paths[:4]):
            if p is None:
                failed_indices.append(i)
                error_messages[i] = "路径为空"
            elif not Path(p).exists():
                failed_indices.append(i)
                error_messages[i] = "文件不存在"
            else:
                pass

        success_count = 4 - len(failed_indices)

        if len(failed_indices) == 0:
            failure_type = "none"
        elif len(failed_indices) == 4:
            failure_type = "full"
        else:
            failure_type = "partial"

        return FailureStatus(
            failure_type=failure_type,
            failed_indices=failed_indices,
            success_count=success_count,
            error_messages=error_messages,
        )

    def get_scenes_with_fallback(
        self,
        article: GeneratedArticle,
        n: int = 4,
    ) -> list[str]:
        if article.scenes and len(article.scenes) >= n:
            return article.scenes[:n]

        cached = image_cache.get(article.id)
        if cached and cached.get("scenes"):
            cache_scenes = cached["scenes"]
            if len(cache_scenes) >= n:
                return cache_scenes[:n]

        try:
            extractor = SceneExtractor()
            extracted = extractor.extract(article.content, n=n)
            if extracted and len(extracted) >= n:
                return extracted[:n]
        except Exception as e:
            logger.warning(f"[重新生成] 场景提取失败: {e}")

        return SceneExtractor.DEFAULT_SCENES[:n]


regen_service = ImageRegenService()
