"""LLM原因分析器"""
from __future__ import annotations

import json

from loguru import logger

from config.settings import settings
from quality.models import QualityAnalysisReport
from quality.prompts import NEGATIVE_ANALYSIS_PROMPT, POSITIVE_ANALYSIS_PROMPT
from quality.cause_attributor import CAUSE_CATEGORY_LABELS


def analyze(
    title: str,
    content: str,
    quality_category: str,
    cause_categories: list[str] | None = None,
    label_reason: str = "",
) -> QualityAnalysisReport:
    """执行LLM原因分析"""
    report = QualityAnalysisReport(quality_category=quality_category)

    if quality_category == "negative":
        cause_labels = [CAUSE_CATEGORY_LABELS.get(c, c) for c in (cause_categories or [])]
        prompt = NEGATIVE_ANALYSIS_PROMPT.format(
            title=title,
            content=content[:2000],
            cause_categories=", ".join(cause_labels) if cause_labels else "未分类",
            label_reason=label_reason or "无",
        )
    elif quality_category == "positive":
        prompt = POSITIVE_ANALYSIS_PROMPT.format(
            title=title,
            content=content[:2000],
            label_reason=label_reason or "无",
        )
    else:
        report.detail = "无法分析：质量分类未确定"
        return report

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.models.llm.api_base,
        )
        resp = client.chat.completions.create(
            model=settings.models.llm.name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.3,
        )
        result_text = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"LLM分析调用失败: {e}")
        report.detail = f"LLM调用失败: {e}"
        return report

    json_str = result_text
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0]
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0]

    try:
        data = json.loads(json_str.strip())
        report.cause_suggestion = data.get("cause_suggestion", [])
        report.detail = data.get("detail", "")
        report.lesson_text = data.get("lesson_text", "")
        report.experience_text = data.get("experience_text", "")
        report.summary = report.lesson_text or report.experience_text or report.detail[:200]
    except json.JSONDecodeError:
        logger.warning(f"LLM分析结果解析失败，使用原始文本: {result_text[:200]}")
        report.detail = result_text
        report.summary = result_text[:200]

    return report
