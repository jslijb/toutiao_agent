"""任务7: 单元测试 — 数据模型"""
import pytest

from quality.models import (
    AutoAnalysisInput,
    AutoAnalysisResult,
    LessonSummary,
    ExperienceSummary,
    QualityAnalysisReport,
    QualityLabelResult,
    ResolvedArticle,
    CAUSE_CATEGORIES,
    CAUSE_CATEGORY_LABELS,
    ARTICLE_SOURCE_DATA,
    ARTICLE_SOURCE_GENERATED,
    ARTICLE_SOURCE_PASTED,
)
from quality.article_resolver import ResolvedArticle as ResolverArticle


class TestAutoAnalysisInput:
    def test_default_values(self):
        inp = AutoAnalysisInput()
        assert inp.title == ""
        assert inp.content == ""

    def test_with_values(self):
        inp = AutoAnalysisInput(title="测试标题", content="这是一篇测试文章内容")
        assert inp.title == "测试标题"
        assert inp.content == "这是一篇测试文章内容"


class TestAutoAnalysisResult:
    def test_default_values(self):
        r = AutoAnalysisResult()
        assert r.quality_category == ""
        assert r.cause_categories == []
        assert r.detail == ""
        assert r.lesson_text == ""
        assert r.experience_text == ""
        assert r.source_title == ""
        assert r.article_source_type == "pasted"
        assert r.analysis_status == ""
        assert r.classify_reason == ""
        assert r.ingested_id == ""

    def test_with_negative_result(self):
        r = AutoAnalysisResult(
            quality_category="negative",
            cause_categories=["title_issue", "content_hollow"],
            detail="标题夸大且内容空洞",
            lesson_text="避免使用夸大标题",
            source_title="震惊！你不知道的秘密",
            article_source_type="pasted",
            analysis_status="done",
            classify_reason="标题含夸大词汇",
        )
        assert r.quality_category == "negative"
        assert len(r.cause_categories) == 2
        assert "title_issue" in r.cause_categories

    def test_with_positive_result(self):
        r = AutoAnalysisResult(
            quality_category="positive",
            experience_text="用数据开头的文章可信度高",
            analysis_status="done",
        )
        assert r.quality_category == "positive"
        assert r.experience_text != ""


class TestResolvedArticle:
    def test_instantiation(self):
        ra = ResolverArticle(
            article_id="abc123",
            title="标题",
            content="正文",
            source_type="data_source",
            store_ref="article_store",
        )
        assert ra.article_id == "abc123"
        assert ra.source_type == "data_source"

    def test_generated_source(self):
        ra = ResolverArticle(
            article_id="gen456",
            title="生成标题",
            content="生成正文",
            source_type="generated",
            store_ref="generated_store",
        )
        assert ra.source_type == "generated"


class TestCauseCategories:
    def test_categories_count(self):
        assert len(CAUSE_CATEGORIES) == 7

    def test_categories_labels_count(self):
        assert len(CAUSE_CATEGORY_LABELS) == 7

    def test_all_categories_have_labels(self):
        for cat in CAUSE_CATEGORIES:
            assert cat in CAUSE_CATEGORY_LABELS

    def test_specific_categories(self):
        assert "title_issue" in CAUSE_CATEGORIES
        assert "content_hollow" in CAUSE_CATEGORIES
        assert "forbidden_words" in CAUSE_CATEGORIES
        assert "structure_chaos" in CAUSE_CATEGORIES
        assert "irrelevant_topic" in CAUSE_CATEGORIES
        assert "limit_flow_penalty" in CAUSE_CATEGORIES
        assert "other" in CAUSE_CATEGORIES


class TestArticleSourceConstants:
    def test_values(self):
        assert ARTICLE_SOURCE_DATA == "data_source"
        assert ARTICLE_SOURCE_GENERATED == "generated"
        assert ARTICLE_SOURCE_PASTED == "pasted"

    def test_uniqueness(self):
        sources = {ARTICLE_SOURCE_DATA, ARTICLE_SOURCE_GENERATED, ARTICLE_SOURCE_PASTED}
        assert len(sources) == 3


class TestExistingModelsUnchanged:
    def test_lesson_summary(self):
        ls = LessonSummary(lesson_text="测试教训", source_title="测试标题")
        assert ls.lesson_text == "测试教训"
        assert ls.deprecated is False

    def test_experience_summary(self):
        es = ExperienceSummary(experience_text="测试经验", source_title="测试标题")
        assert es.experience_text == "测试经验"
        assert es.deprecated is False

    def test_quality_analysis_report(self):
        r = QualityAnalysisReport(quality_category="negative")
        assert r.quality_category == "negative"
        assert r.cause_suggestion == []

    def test_quality_label_result(self):
        r = QualityLabelResult()
        assert r.success is False
        assert r.message == ""
        assert r.lesson_ids == []
        assert r.experience_ids == []
