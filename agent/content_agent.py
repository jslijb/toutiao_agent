"""qwen-agent 文章生成 Agent（多步生成编排）"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from openai import OpenAI
from loguru import logger

from config.settings import settings
from models.article import GeneratedArticle
from agent.prompts import (
    SYSTEM_PROMPT, OUTLINE_PROMPT, ARTICLE_PROMPT,
    SCENE_EXTRACT_PROMPT, REFINE_PROMPT,
)
from agent.tools import RagRetrieveTool, HotTopicTool
from agent.title_gen import TitleGenerator
from utils.text_utils import count_words, check_sensitive, truncate_text


class ContentAgent:
    """文章生成 Agent：热点获取 → 主题筛选 → RAG 检索 → 大纲 → 扩写 → 润色 → 标题优化"""

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.models.llm.api_base,
        )
        self.model = settings.models.llm.name
        self.rag_tool = RagRetrieveTool()
        self.hot_tool = HotTopicTool()
        self.title_gen = TitleGenerator(self.client)

    def _chat(self, system: str = "", user: str = "", temperature: float = 0.7, max_tokens: int = 4096) -> str:
        """调用 LLM"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()

    def generate_article(
        self,
        hot_topic: str = "",
        custom_topic: str = "",
    ) -> GeneratedArticle:
        """
        生成一篇完整的微头条文章

        Args:
            hot_topic: 热点话题（可选，为空则自动获取）
            custom_topic: 自定义主题（优先于 hot_topic）

        Returns:
            GeneratedArticle 对象
        """
        article = GeneratedArticle()

        # Step 1: 确定话题
        topic = custom_topic or hot_topic
        if not topic:
            topics = self.hot_tool.fetch_hot_topics(max_topics=5)
            if topics:
                topic = topics[0].get("title", "")
            if not topic:
                topic = "职场技能提升"

        article.hot_topic = topic
        logger.info(f"[Agent] 话题确定: {topic}")

        # Step 2: RAG 检索
        rag_context = self.rag_tool.retrieve_context(topic, top_k=settings.generation.rag_top_k)
        logger.info(f"[Agent] RAG 检索完成，获取 {len(rag_context)} 字参考素材")

        # Step 3: 生成大纲
        logger.info("[Agent] 生成文章大纲...")
        outline = self._generate_outline(topic, rag_context)
        if not outline:
            logger.warning("[Agent] 大纲生成失败，使用默认结构")
            outline = self._default_outline(topic)

        # Step 4: 逐段扩写生成正文
        logger.info("[Agent] 生成文章正文...")
        content = self._generate_content(topic, outline, rag_context)

        # Step 5: 全文润色
        logger.info("[Agent] 润色文章...")
        content = self._refine_article(content)

        # Step 6: 字数校验
        content = self._adjust_word_count(content)

        # Step 7: 敏感词检测
        sensitive = check_sensitive(content)
        if sensitive:
            logger.warning(f"[Agent] 检测到敏感词: {sensitive}")

        article.content = content
        article.word_count = count_words(content)

        # Step 8: 标题优化
        logger.info("[Agent] 生成爆款标题...")
        summary = truncate_text(content, 300)
        titles = self.title_gen.generate_titles(summary, topic)
        best_title, title_score = self.title_gen.select_best_title(titles)
        article.title = best_title
        logger.info(f"[Agent] 最终标题: {best_title} (评分: {title_score})")

        # Step 9: 提取配图场景
        logger.info("[Agent] 提取配图场景...")
        article.scenes = self._extract_scenes(content)

        return article

    def _generate_outline(self, topic: str, rag_context: str) -> dict:
        """生成文章大纲"""
        prompt = OUTLINE_PROMPT.format(
            hot_topic=topic,
            rag_context=rag_context[:3000],
        )
        try:
            result = self._chat(user=prompt, temperature=0.7, max_tokens=2048)
            # 尝试解析 JSON
            match = re.search(r'\{.*\}', result, re.DOTALL)
            if match:
                return json.loads(match.group())
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"[Agent] 大纲 JSON 解析失败: {e}")

        return None

    def _default_outline(self, topic: str) -> dict:
        """默认文章结构"""
        return {
            "theme": topic,
            "hook": f"说个关于{topic}的大实话，可能有点扎心，但真的很重要。",
            "points": [
                {"title": "现状分析", "target_words": 250},
                {"title": "核心观点", "target_words": 300},
                {"title": "实操方法", "target_words": 300},
            ],
            "ending": "你觉得呢？评论区聊聊你的看法，觉得有用的话点个收藏，慢慢看。",
        }

    def _generate_content(self, topic: str, outline: dict, rag_context: str) -> str:
        """根据大纲生成正文"""
        outline_points = json.dumps(outline.get("points", []), ensure_ascii=False, indent=2)
        hook = outline.get("hook", "")

        prompt = ARTICLE_PROMPT.format(
            theme=outline.get("theme", topic),
            hook=hook,
            outline_points=outline_points,
            rag_context=rag_context[:3000],
            target_words=settings.generation.target_word_count,
        )

        return self._chat(system=SYSTEM_PROMPT, user=prompt, temperature=0.7, max_tokens=settings.models.llm.max_tokens)

    def _refine_article(self, content: str) -> str:
        """润色文章"""
        prompt = REFINE_PROMPT.format(
            article=content,
            target_words=settings.generation.target_word_count,
        )
        try:
            return self._chat(system=SYSTEM_PROMPT, user=prompt, temperature=0.6, max_tokens=settings.models.llm.max_tokens)
        except Exception as e:
            logger.warning(f"[Agent] 润色失败: {e}")
            return content

    def _adjust_word_count(self, content: str) -> str:
        """调整字数到目标范围"""
        current = count_words(content)
        target = settings.generation.target_word_count
        min_w = settings.generation.min_word_count
        max_w = settings.generation.max_word_count

        if min_w <= current <= max_w:
            return content

        if current < min_w:
            logger.info(f"[Agent] 字数不足 ({current}), 补充内容...")
            prompt = f"请将以下文章扩充到约 {target} 字，保持风格一致，补充更多细节和案例：\n\n{content}"
            try:
                return self._chat(system=SYSTEM_PROMPT, user=prompt, temperature=0.7, max_tokens=settings.models.llm.max_tokens)
            except Exception:
                return content
        else:
            logger.info(f"[Agent] 字数过多 ({current}), 精简内容...")
            # 简单截断到最大字数附近
            chars = list(content)
            return "".join(chars[:int(len(chars) * max_w / current)])

    def _extract_scenes(self, content: str) -> list[str]:
        """提取4个配图场景描述"""
        prompt = SCENE_EXTRACT_PROMPT.format(article_content=content[:2000])
        try:
            result = self._chat(user=prompt, temperature=0.8, max_tokens=1024)
            match = re.search(r'\[.*?\]', result, re.DOTALL)
            if match:
                scenes = json.loads(match.group())
                if isinstance(scenes, list) and len(scenes) >= 4:
                    return scenes[:4]
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"[Agent] 场景提取失败: {e}")

        # 默认场景
        return [
            "一位年轻人在办公室认真工作，桌上放着电脑和笔记本",
            "两人在咖啡馆讨论副业计划，充满热情",
            "一个人站在山顶俯瞰城市，象征成长和突破",
            "一群人围坐在一起分享经验，互相学习",
        ]
