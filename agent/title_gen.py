"""爆款标题生成器"""
from __future__ import annotations

import json
import re
from typing import Optional

from openai import OpenAI
from loguru import logger

from config.settings import settings
from agent.prompts import TITLE_PROMPT, TITLE_SCORE_PROMPT
from utils.text_utils import count_words


class TitleGenerator:
    """爆款标题生成器：多次生成 + 评分选优"""

    def __init__(self, client: Optional[OpenAI] = None):
        self.client = client or OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.models.llm.api_base,
        )
        self.model = settings.models.llm.name
        self.n_candidates = settings.generation.title_candidates

    def generate_titles(self, content_summary: str, hot_topic: str = "") -> list[str]:
        """生成多个候选标题"""
        prompt = TITLE_PROMPT.format(
            content_summary=content_summary[:500],
            hot_topic=hot_topic,
            n=self.n_candidates,
        )

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=1024,
            )
            text = resp.choices[0].message.content.strip()
            # 提取 JSON 数组
            titles = self._parse_titles(text)
            if titles:
                return titles
        except Exception as e:
            logger.error(f"标题生成失败: {e}")

        return [f"关于{hot_topic or '职场'}，说点大实话"] if hot_topic else ["职场人必须知道的真相"]

    def select_best_title(self, titles: list[str]) -> tuple[str, float]:
        """评分选出最佳标题"""
        if len(titles) <= 1:
            return titles[0], 5.0 if titles else ("", 0.0)

        prompt = TITLE_SCORE_PROMPT.format(titles=json.dumps(titles, ensure_ascii=False, indent=2))

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
            )
            text = resp.choices[0].message.content.strip()
            result = self._parse_score(text)
            if result:
                return result["best_title"], result["best_score"]
        except Exception as e:
            logger.error(f"标题评分失败: {e}")

        return titles[0], 5.0

    def _parse_titles(self, text: str) -> list[str]:
        """从 LLM 输出中解析标题列表"""
        # 尝试提取 JSON 数组
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            try:
                titles = json.loads(match.group())
                if isinstance(titles, list) and all(isinstance(t, str) for t in titles):
                    return [t.strip() for t in titles if t.strip()]
            except json.JSONDecodeError:
                pass

        # 降级：按行分割，过滤空行
        lines = [l.strip().strip('"\'').strip() for l in text.split("\n") if l.strip()]
        # 去除序号前缀
        cleaned = []
        for line in lines:
            line = re.sub(r"^\d+[\.\、\)]\s*", "", line)
            if 5 < len(line) < 50:
                cleaned.append(line)
        return cleaned[:self.n_candidates]

    def _parse_score(self, text: str) -> Optional[dict]:
        """解析评分结果 JSON"""
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return None
