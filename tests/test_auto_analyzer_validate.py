"""任务9: 单元测试 — 输入校验"""
import pytest

from quality.auto_analyzer import _validate_input


class TestValidateInput:
    def test_empty_title(self):
        ok, msg = _validate_input("", "这是一篇有足够内容的文章")
        assert ok is False
        assert "请输入文章标题" in msg

    def test_whitespace_only_title(self):
        ok, msg = _validate_input("   ", "这是一篇有足够内容的文章")
        assert ok is False
        assert "请输入文章标题" in msg

    def test_content_too_short(self):
        ok, msg = _validate_input("标题", "短内容")
        assert ok is False
        assert "正文内容过短" in msg

    def test_content_exactly_10_chars(self):
        ok, msg = _validate_input("标题", "a" * 10)
        assert ok is True
        assert msg == ""

    def test_content_9_chars(self):
        ok, msg = _validate_input("标题", "a" * 9)
        assert ok is False
        assert "正文内容过短" in msg

    def test_valid_input(self):
        ok, msg = _validate_input("正常标题", "这是一篇正常的文章内容，字数足够")
        assert ok is True
        assert msg == ""

    def test_content_whitespace_only(self):
        ok, msg = _validate_input("标题", "   ")
        assert ok is False
        assert "正文内容过短" in msg
