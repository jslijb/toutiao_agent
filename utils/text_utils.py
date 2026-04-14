"""文本工具函数"""
from __future__ import annotations

import re
from typing import Optional


def count_words(text: str) -> int:
    """统计中文字数（中文按字计，英文按词计）"""
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_words = len(re.findall(r"[a-zA-Z]+", text))
    return chinese_chars + english_words


def clean_html(html: str) -> str:
    """去除 HTML 标签"""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&[a-zA-Z]+;", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def remove_ads(text: str) -> str:
    """去除常见广告/噪声文本"""
    noise_patterns = [
        r"【.*?广告.*?】",
        r"关注.*?公众号.*?",
        r"扫码.*?关注.*?",
        r"点击.*?阅读原文.*?",
        r"分享到.*?",
        r"版权声明.*",
        r"免责声明.*",
        r"转载请注明.*?",
        r"本文.*?不代表.*?观点",
    ]
    for pattern in noise_patterns:
        text = re.sub(pattern, "", text, flags=re.DOTALL)
    return text.strip()


def extract正文_from_html(html: str) -> str:
    """从 HTML 提取正文（清洗 + 去广告）"""
    text = clean_html(html)
    text = remove_ads(text)
    return text


# 常见敏感词列表（可扩展）
DEFAULT_SENSITIVE_WORDS = [
    "赌博", "色情", "暴力", "毒品", "枪支", "洗钱",
    "诈骗", "传销", "非法集资", "假币",
]


def check_sensitive(text: str, extra_words: list[str] | None = None) -> list[str]:
    """检测文本中的敏感词，返回命中的敏感词列表"""
    words = set(DEFAULT_SENSITIVE_WORDS)
    if extra_words:
        words.update(extra_words)
    found = [w for w in words if w in text]
    return found


def is_sensitive(text: str, extra_words: list[str] | None = None) -> bool:
    """判断文本是否包含敏感词"""
    return len(check_sensitive(text, extra_words)) > 0


def truncate_text(text: str, max_length: int = 500) -> str:
    """截断文本到指定长度"""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
