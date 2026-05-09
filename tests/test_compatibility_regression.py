"""任务19: 兼容性回归验证 — 现有功能不受影响"""
import pytest

from quality.models import (
    LessonSummary,
    ExperienceSummary,
    QualityAnalysisReport,
    QualityLabelResult,
    CAUSE_CATEGORIES,
    CAUSE_CATEGORY_LABELS,
)
from quality.prompts import NEGATIVE_ANALYSIS_PROMPT, POSITIVE_ANALYSIS_PROMPT
from quality.cause_attributor import validate_cause_categories


class TestLabelerSignatureUnchanged:
    def test_submit_label_exists(self):
        from quality.labeler import submit_label
        import inspect
        sig = inspect.signature(submit_label)
        params = list(sig.parameters.keys())
        assert "article_id" in params
        assert "quality_category" in params
        assert "limit_flow" in params
        assert "cause_categories" in params
        assert "label_reason" in params


class TestPromptsUnchanged:
    def test_negative_prompt_has_placeholders(self):
        assert "{title}" in NEGATIVE_ANALYSIS_PROMPT
        assert "{content}" in NEGATIVE_ANALYSIS_PROMPT
        assert "{cause_categories}" in NEGATIVE_ANALYSIS_PROMPT
        assert "{label_reason}" in NEGATIVE_ANALYSIS_PROMPT

    def test_positive_prompt_has_placeholders(self):
        assert "{title}" in POSITIVE_ANALYSIS_PROMPT
        assert "{content}" in POSITIVE_ANALYSIS_PROMPT
        assert "{label_reason}" in POSITIVE_ANALYSIS_PROMPT


class TestIngestFunctionExists:
    def test_ingest_lesson_exists(self):
        from quality.ingester import ingest_lesson
        assert callable(ingest_lesson)

    def test_ingest_experience_exists(self):
        from quality.ingester import ingest_experience
        assert callable(ingest_experience)

    def test_list_knowledge_exists(self):
        from quality.ingester import list_knowledge
        assert callable(list_knowledge)


class TestCauseAttributorUnchanged:
    def test_validate_cause_categories_negative(self):
        result = validate_cause_categories(
            categories=["title_issue"],
            quality_category="negative",
        )
        assert "title_issue" in result

    def test_validate_cause_categories_positive(self):
        result = validate_cause_categories(
            categories=[],
            quality_category="positive",
        )
        assert isinstance(result, list)


class TestAutoAnalyzerExists:
    def test_auto_analyze_exists(self):
        from quality.auto_analyzer import auto_analyze
        assert callable(auto_analyze)

    def test_confirm_ingest_exists(self):
        from quality.auto_analyzer import confirm_ingest
        assert callable(confirm_ingest)

    def test_validate_input_exists(self):
        from quality.auto_analyzer import _validate_input
        assert callable(_validate_input)


class TestArticleResolverExists:
    def test_resolve_article_exists(self):
        from quality.article_resolver import resolve_article
        assert callable(resolve_article)

    def test_resolved_article_dataclass(self):
        from quality.article_resolver import ResolvedArticle
        ra = ResolvedArticle(
            article_id="test", title="t", content="c",
            source_type="data_source", store_ref="article_store"
        )
        assert ra.article_id == "test"
