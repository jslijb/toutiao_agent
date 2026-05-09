"""任务5: 服务层单元测试 — ImageRegenService"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from pathlib import Path

from services.image_regen_service import (
    ImageRegenService,
    RegenProgress,
    FailureStatus,
    regen_service,
)


class TestDetectFailureStatus:
    def setup_method(self):
        self.service = ImageRegenService()

    def test_all_success(self):
        """4张有效路径"""
        with patch.object(Path, 'exists', return_value=True):
            status = self.service.detect_failure_status(
                ["/path/a.png", "/path/b.png", "/path/c.png", "/path/d.png"]
            )
        assert status.failure_type == "none"
        assert status.success_count == 4
        assert status.failed_indices == []

    def test_partial_failure(self):
        """2张有效 + 2张 None"""
        with patch.object(Path, 'exists', return_value=True):
            status = self.service.detect_failure_status(
                ["/path/a.png", None, "/path/c.png", None]
            )
        assert status.failure_type == "partial"
        assert status.success_count == 2
        assert status.failed_indices == [1, 3]

    def test_full_failure(self):
        """4张 None"""
        status = self.service.detect_failure_status(
            [None, None, None, None]
        )
        assert status.failure_type == "full"
        assert status.success_count == 0
        assert status.failed_indices == [0, 1, 2, 3]

    def test_file_not_exists(self):
        """路径非 None 但文件缺失"""
        with patch.object(Path, 'exists', return_value=False):
            status = self.service.detect_failure_status(
                ["/missing/a.png", "/missing/b.png", "/missing/c.png", "/missing/d.png"]
            )
        assert status.failure_type == "full"
        assert status.failed_indices == [0, 1, 2, 3]
        for i in [0, 1, 2, 3]:
            assert status.error_messages[i] == "文件不存在"

    def test_mixed_none_and_missing(self):
        """混合 None 和文件不存在"""
        with patch.object(Path, 'exists', return_value=False):
            status = self.service.detect_failure_status(
                [None, "/missing/b.png", None, "/missing/d.png"]
            )
        assert status.failure_type == "full"
        assert status.error_messages[0] == "路径为空"
        assert status.error_messages[1] == "文件不存在"

    def test_short_list_padded(self):
        """列表不足4项"""
        with patch.object(Path, 'exists', return_value=True):
            status = self.service.detect_failure_status(["/path/a.png", "/path/b.png"])
        assert status.failure_type == "partial"
        assert status.success_count == 2


class TestGetScenesWithFallback:
    def setup_method(self):
        self.service = ImageRegenService()

    def test_article_scenes_sufficient(self):
        """article.scenes 有效且长度 >= 4"""
        article = MagicMock()
        article.scenes = ["场景A", "场景B", "场景C", "场景D"]
        article.id = "test001"

        result = self.service.get_scenes_with_fallback(article, n=4)
        assert len(result) == 4
        assert result[0] == "场景A"

    @patch("services.image_regen_service.image_cache")
    def test_article_scenes_insufficient_use_cache(self, mock_cache):
        """article.scenes 不足，从 ImageCache 获取"""
        article = MagicMock()
        article.scenes = ["场景A"]
        article.id = "test002"

        mock_cache.get.return_value = {
            "scenes": ["缓存A", "缓存B", "缓存C", "缓存D"]
        }

        result = self.service.get_scenes_with_fallback(article, n=4)
        assert len(result) == 4
        assert result[0] == "缓存A"

    @patch("services.image_regen_service.SceneExtractor")
    @patch("services.image_regen_service.image_cache")
    def test_cache_empty_use_extractor(self, mock_cache, mock_extractor_cls):
        """ImageCache 也无，调用 SceneExtractor 提取"""
        article = MagicMock()
        article.scenes = []
        article.id = "test003"
        article.content = "测试文章内容"

        mock_cache.get.return_value = None

        extractor = MagicMock()
        extractor.extract.return_value = ["提取A", "提取B", "提取C", "提取D"]
        mock_extractor_cls.return_value = extractor

        result = self.service.get_scenes_with_fallback(article, n=4)
        assert len(result) == 4

    @patch("services.image_regen_service.SceneExtractor")
    @patch("services.image_regen_service.image_cache")
    def test_all_fail_use_default(self, mock_cache, mock_extractor_cls):
        """提取也失败，使用默认场景"""
        article = MagicMock()
        article.scenes = []
        article.id = "test004"
        article.content = "测试内容"

        mock_cache.get.return_value = None
        mock_extractor_cls.side_effect = Exception("提取失败")

        result = self.service.get_scenes_with_fallback(article, n=4)
        assert len(result) == 4
        assert all(isinstance(s, str) for s in result)


class TestRegenerateArticleImages:
    def setup_method(self):
        self.service = ImageRegenService()

    def test_article_none(self):
        """article 为 None 时 yield 错误提示"""
        results = list(self.service.regenerate_article_images(None))
        assert len(results) >= 1
        paths, msg = results[0]
        assert "请先" in msg or "生成文章" in msg
        assert paths == [None] * 4

    @patch.object(ImageRegenService, 'get_scenes_with_fallback')
    @patch("services.image_regen_service.image_cache")
    def test_scope_all_regenerates_all(self, mock_cache, mock_scenes):
        """scope=all 重新生成全部4张"""
        mock_scenes.return_value = ["场景A", "场景B", "场景C", "场景D"]
        mock_cache.clear = MagicMock()
        mock_cache.save = MagicMock()

        article = MagicMock()
        article.id = "test005"
        article.scenes = ["场景A", "场景B", "场景C", "场景D"]
        article.image_paths = []

        with patch.object(self.service, 'generator', MagicMock()) as mock_gen:
            mock_gen.generate.return_value = ["/new/image.png"]
            results = list(self.service.regenerate_article_images(article, scope="all"))

        assert mock_cache.clear.called
        assert len(results) > 0
        final_paths, final_msg = results[-1]
        assert "重新生成完成" in final_msg

    @patch.object(ImageRegenService, 'get_scenes_with_fallback')
    @patch("services.image_regen_service.image_cache")
    def test_no_targets_needed(self, mock_cache, mock_scenes):
        """所有配图均有效，无需重新生成"""
        mock_scenes.return_value = ["场景A", "场景B", "场景C", "场景D"]

        article = MagicMock()
        article.id = "test006"
        article.scenes = mock_scenes.return_value
        article.image_paths = []

        with patch.object(Path, 'exists', return_value=True):
            mock_cache.get.return_value = {
                "image_paths": ["/a.png", "/b.png", "/c.png", "/d.png"]
            }
            results = list(self.service.regenerate_article_images(article, scope="failed_only"))

        assert len(results) >= 1
        _, msg = results[-1]
        assert "无需重新生成" in msg


class TestFailureStatusDataclass:
    def test_creation(self):
        fs = FailureStatus(
            failure_type="partial",
            failed_indices=[1, 3],
            success_count=2,
            error_messages={1: "路径为空", 3: "文件不存在"},
        )
        assert fs.failure_type == "partial"
        assert len(fs.failed_indices) == 2
        assert fs.success_count == 2


class TestRegenProgressDataclass:
    def test_default_values(self):
        rp = RegenProgress(article_id="test", scope="all")
        assert rp.total == 4
        assert rp.completed == 0
        assert rp.success_count == 0
        assert rp.current_paths == [None, None, None, None]
        assert rp.status_message == ""


class TestSingletonInstance:
    def test_regen_service_exists(self):
        assert regen_service is not None
        assert isinstance(regen_service, ImageRegenService)
