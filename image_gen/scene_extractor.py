"""场景提取器 - 从文章中提取适合生成卡通配图的关键场景"""
from __future__ import annotations

import json
import re
from typing import Optional

from openai import OpenAI
from loguru import logger

from config.settings import settings


class SceneExtractor:
    """从文章内容中提取 4 个关键场景描述"""

    DEFAULT_SCENES = [
        "一位年轻人在办公室认真工作，桌上放着电脑和咖啡，充满干劲",
        "两人在咖啡馆讨论副业计划，桌上放着笔记本电脑和计划书",
        "一个人站在山顶俯瞰城市天际线，象征突破和成长",
        "一群人围坐在会议室分享经验，气氛热烈积极向上",
    ]

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.models.llm.api_base,
        )
        self.model = settings.models.llm.name

    def extract(self, content: str, n: int = 4) -> list[str]:
        """从文章中提取 n 个场景描述"""
        prompt = f"""从以下文章中提取 {n} 个适合生成卡通配图的关键场景。

文章内容：
{content[:2000]}

要求：
1. 每个场景描述 20-40 个字
2. 场景要具体、有画面感
3. 适合用 3D 卡通风格表现
4. 场景要覆盖文章的开头、中间、结尾不同阶段

请用 JSON 数组格式输出：
["场景描述1", "场景描述2", "场景描述3", "场景描述4"]"""

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=1024,
            )
            text = resp.choices[0].message.content.strip()

            # 解析 JSON
            match = re.search(r'\[.*?\]', text, re.DOTALL)
            if match:
                scenes = json.loads(match.group())
                if isinstance(scenes, list) and len(scenes) >= n:
                    return scenes[:n]
        except Exception as e:
            logger.warning(f"场景提取失败: {e}")

        logger.info("使用默认场景")
        return self.DEFAULT_SCENES[:n]
