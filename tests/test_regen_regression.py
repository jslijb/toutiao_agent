"""任务8: 回归与兼容性验证"""
import pytest
from pathlib import Path


class TestGeneratedArticleUnchanged:
    def test_import(self):
        from models.article import GeneratedArticle
        assert GeneratedArticle is not None

    def test_key_fields(self):
        from models.article import GeneratedArticle
        a = GeneratedArticle(title="测试", content="测试内容")
        assert hasattr(a, 'id')
        assert hasattr(a, 'title')
        assert hasattr(a, 'content')
        assert hasattr(a, 'image_paths')
        assert hasattr(a, 'scenes')
        assert hasattr(a, 'status')


class TestImageCacheUnchanged:
    def test_import(self):
        from utils.image_cache import image_cache
        assert image_cache is not None

    def test_key_methods(self):
        from utils.image_cache import image_cache
        assert hasattr(image_cache, 'get')
        assert hasattr(image_cache, 'save')
        assert hasattr(image_cache, 'clear')


class TestRegenServiceDoesNotBreakExisting:
    def test_regen_service_is_additive(self):
        """regen_service 是新增模块，不修改现有行为"""
        from services.image_regen_service import regen_service
        assert hasattr(regen_service, 'regenerate_article_images')
        assert hasattr(regen_service, 'detect_failure_status')
        assert hasattr(regen_service, 'get_scenes_with_fallback')

    def test_failure_status_is_new(self):
        """FailureStatus 是新增数据类"""
        from services.image_regen_service import FailureStatus
        fs = FailureStatus(
            failure_type="none",
            failed_indices=[],
            success_count=4,
            error_messages={},
        )
        assert fs.failure_type == "none"
