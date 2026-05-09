"""任务8: 单元测试 — Prompt模板"""
import pytest

from quality.prompts import (
    NEGATIVE_ANALYSIS_PROMPT,
    POSITIVE_ANALYSIS_PROMPT,
    QUALITY_CLASSIFY_PROMPT,
)

_ORIGINAL_NEGATIVE = """你是一位文章质量分析师。以下文章已被用户标注为"劣质"（质量差），请分析原因并提炼教训摘要。"""
_ORIGINAL_POSITIVE = """你是一位文章质量分析师。以下文章已被用户标注为"优质"（质量好），请分析成功因素并提炼经验摘要。"""


class TestQualityClassifyPrompt:
    def test_format_no_error(self):
        result = QUALITY_CLASSIFY_PROMPT.format(
            title="测试标题", content="测试正文内容"
        )
        assert isinstance(result, str)
        assert len(result) > 100

    def test_contains_title(self):
        result = QUALITY_CLASSIFY_PROMPT.format(
            title="我的文章标题", content="正文"
        )
        assert "我的文章标题" in result

    def test_contains_content(self):
        result = QUALITY_CLASSIFY_PROMPT.format(
            title="标题", content="这是文章正文内容"
        )
        assert "这是文章正文内容" in result

    def test_contains_classification_criteria(self):
        result = QUALITY_CLASSIFY_PROMPT.format(title="t", content="c")
        assert "positive" in result
        assert "negative" in result

    def test_contains_cause_categories(self):
        result = QUALITY_CLASSIFY_PROMPT.format(title="t", content="c")
        assert "title_issue" in result
        assert "content_hollow" in result
        assert "forbidden_words" in result

    def test_requires_json_output(self):
        result = QUALITY_CLASSIFY_PROMPT.format(title="t", content="c")
        assert "quality_category" in result
        assert "cause_categories" in result
        assert "classify_reason" in result


class TestExistingPromptsUnchanged:
    def test_negative_prompt_unchanged(self):
        assert NEGATIVE_ANALYSIS_PROMPT.startswith(_ORIGINAL_NEGATIVE)

    def test_positive_prompt_unchanged(self):
        assert POSITIVE_ANALYSIS_PROMPT.startswith(_ORIGINAL_POSITIVE)

    def test_negative_prompt_format(self):
        result = NEGATIVE_ANALYSIS_PROMPT.format(
            title="t", content="c", cause_categories="标题问题", label_reason="备注"
        )
        assert "t" in result
        assert "c" in result

    def test_positive_prompt_format(self):
        result = POSITIVE_ANALYSIS_PROMPT.format(
            title="t", content="c", label_reason="备注"
        )
        assert "t" in result
        assert "c" in result
