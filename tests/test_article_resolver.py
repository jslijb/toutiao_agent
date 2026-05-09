"""任务10: 单元测试 — 文章统一查询"""
import pytest
from unittest.mock import patch, MagicMock

from quality.article_resolver import resolve_article, ResolvedArticle


class TestResolveArticleFromArticleStore:
    @patch("quality.article_resolver.get_article_store")
    @patch("quality.article_resolver.get_generated_store")
    def test_found_in_article_store(self, mock_gen, mock_art):
        mock_article = MagicMock()
        mock_article.id = "art001"
        mock_article.title = "数据源文章"
        mock_article.content = "文章内容"

        store = MagicMock()
        store._index = {"url1": mock_article}
        store._ensure_loaded = MagicMock()
        mock_art.return_value = store

        result = resolve_article("art001")
        assert result is not None
        assert result.source_type == "data_source"
        assert result.store_ref == "article_store"
        assert result.title == "数据源文章"

    @patch("quality.article_resolver.get_article_store")
    @patch("quality.article_resolver.get_generated_store")
    def test_article_store_priority(self, mock_gen, mock_art):
        mock_article = MagicMock()
        mock_article.id = "shared_id"
        mock_article.title = "ArticleStore版本"
        mock_article.content = "内容"

        store = MagicMock()
        store._index = {"url1": mock_article}
        store._ensure_loaded = MagicMock()
        mock_art.return_value = store

        result = resolve_article("shared_id")
        assert result is not None
        assert result.source_type == "data_source"


class TestResolveArticleFromGeneratedStore:
    @patch("quality.article_resolver.get_article_store")
    @patch("quality.article_resolver.get_generated_store")
    def test_found_in_generated_store(self, mock_gen, mock_art):
        store = MagicMock()
        store._index = {}
        store._ensure_loaded = MagicMock()
        mock_art.return_value = store

        gen_article = MagicMock()
        gen_article.id = "gen001"
        gen_article.title = "生成文章"
        gen_article.content = "生成内容"

        gen_store = MagicMock()
        gen_store.get.return_value = gen_article
        mock_gen.return_value = gen_store

        result = resolve_article("gen001")
        assert result is not None
        assert result.source_type == "generated"
        assert result.store_ref == "generated_store"
        assert result.title == "生成文章"


class TestResolveArticleNotFound:
    @patch("quality.article_resolver.get_article_store")
    @patch("quality.article_resolver.get_generated_store")
    def test_not_found_in_any_store(self, mock_gen, mock_art):
        store = MagicMock()
        store._index = {}
        store._ensure_loaded = MagicMock()
        mock_art.return_value = store

        gen_store = MagicMock()
        gen_store.get.return_value = None
        mock_gen.return_value = gen_store

        result = resolve_article("nonexistent")
        assert result is None


class TestResolveArticleExceptionHandling:
    @patch("quality.article_resolver.get_article_store")
    @patch("quality.article_resolver.get_generated_store")
    def test_article_store_exception_does_not_block(self, mock_gen, mock_art):
        mock_art.side_effect = Exception("ArticleStore崩溃")

        gen_article = MagicMock()
        gen_article.id = "gen002"
        gen_article.title = "兜底文章"
        gen_article.content = "兜底内容"

        gen_store = MagicMock()
        gen_store.get.return_value = gen_article
        mock_gen.return_value = gen_store

        result = resolve_article("gen002")
        assert result is not None
        assert result.source_type == "generated"

    @patch("quality.article_resolver.get_article_store")
    @patch("quality.article_resolver.get_generated_store")
    def test_both_stores_exception(self, mock_gen, mock_art):
        mock_art.side_effect = Exception("ArticleStore崩溃")
        mock_gen.side_effect = Exception("GeneratedStore崩溃")

        result = resolve_article("any_id")
        assert result is None
