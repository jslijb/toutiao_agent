"""文本清洗器"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from utils.text_utils import clean_html, remove_ads


class TextCleaner:
    """HTML 文本清洗器"""

    @staticmethod
    def clean(html_or_text: str, to_markdown: bool = False) -> str:
        """清洗文本/HTML，去除噪声"""
        if not html_or_text:
            return ""

        text = html_or_text.strip()

        # 如果是 HTML，先提取正文
        if "<" in text and ">" in text:
            # 用 markdownify 转 Markdown
            text = md(text, heading_style="ATX", strip=["script", "style", "nav", "footer", "header"])
            # 去除图片标记
            text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
            # 去除链接标记保留文字
            text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
            # 去除 Markdown 标题符号
            text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
            # 去除多余空白行
            text = re.sub(r"\n{3,}", "\n\n", text)

        # 去广告/噪声
        text = remove_ads(text)

        # 去除特殊字符
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

        # 标准化空白
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n ", "\n", text)

        return text.strip()

    @staticmethod
    def extract_main_content(html: str) -> str:
        """从 HTML 中提取正文（智能识别正文区域）"""
        soup = BeautifulSoup(html, "lxml")

        # 移除噪声标签
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
            tag.decompose()

        # 尝试常见正文容器
        for selector in ["article", '[class*="content"]', '[class*="article"]', '[class*="post"]', '[id*="content"]', '[id*="article"]']:
            main = soup.select_one(selector)
            if main:
                return TextCleaner.clean(str(main), to_markdown=True)

        # 降级：取 body
        body = soup.find("body")
        if body:
            return TextCleaner.clean(str(body), to_markdown=True)

        return TextCleaner.clean(html)
