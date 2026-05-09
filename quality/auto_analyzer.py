"""自动分析编排 — 黏贴即分析全流程"""
from __future__ import annotations

import json

from loguru import logger

from config.settings import settings
from quality.cause_attributor import validate_cause_categories
from quality.analyzer import analyze
from quality.ingester import ingest_lesson, ingest_experience
from quality.models import (
    AutoAnalysisResult,
    LessonSummary,
    ExperienceSummary,
    ARTICLE_SOURCE_PASTED,
)
from quality.prompts import QUALITY_CLASSIFY_PROMPT


def _validate_input(title: str, content: str) -> tuple[bool, str]:
    if not title.strip():
        return False, "请输入文章标题"
    if len(content.strip()) < 10:
        return False, "正文内容过短，请输入完整文章内容（至少10字符）"
    return True, ""


def _call_llm(prompt: str, max_tokens: int = 512, temperature: float = 0.1) -> str:
    from openai import OpenAI
    client = OpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.models.llm.api_base,
        timeout=60.0,
    )
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=settings.models.llm.name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt == 0:
                logger.warning(f"LLM调用失败(第1次)，重试: {e}")
                import time
                time.sleep(2)
            else:
                raise


def _classify_quality(title: str, content: str) -> dict:
    prompt = QUALITY_CLASSIFY_PROMPT.format(
        title=title,
        content=content[:2000],
    )
    raw = _call_llm(prompt, max_tokens=512, temperature=0.1)

    json_str = raw
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0]
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0]

    try:
        data = json.loads(json_str.strip())
        return {
            "quality_category": data.get("quality_category", ""),
            "cause_categories": data.get("cause_categories", []),
            "classify_reason": data.get("classify_reason", ""),
        }
    except json.JSONDecodeError:
        logger.warning(f"分类结果解析失败，原始文本: {raw[:200]}")
        return {
            "quality_category": "",
            "cause_categories": [],
            "classify_reason": raw[:200],
        }


def auto_analyze(title: str, content: str) -> AutoAnalysisResult:
    result = AutoAnalysisResult(
        source_title=title,
        article_source_type=ARTICLE_SOURCE_PASTED,
    )

    valid, msg = _validate_input(title, content)
    if not valid:
        result.analysis_status = "failed"
        result.detail = msg
        return result

    try:
        classify = _classify_quality(title, content)
    except Exception as e:
        logger.error(f"LLM质量分类调用失败: {e}")
        result.analysis_status = "failed"
        result.detail = "LLM分析失败，请检查网络或稍后重试"
        return result

    quality_category = classify.get("quality_category", "")
    if quality_category not in ("positive", "negative"):
        result.analysis_status = "partial"
        result.detail = f"分类结果异常: {classify.get('classify_reason', '')}"
        result.classify_reason = classify.get("classify_reason", "")
        return result

    result.quality_category = quality_category
    result.classify_reason = classify.get("classify_reason", "")

    if quality_category == "negative":
        cause_categories = classify.get("cause_categories", [])
        result.cause_categories = validate_cause_categories(
            categories=cause_categories,
            quality_category="negative",
        )

    try:
        report = analyze(
            title=title,
            content=content,
            quality_category=quality_category,
            cause_categories=result.cause_categories,
        )
    except Exception as e:
        logger.error(f"LLM原因分析调用失败: {e}")
        result.analysis_status = "failed"
        result.detail = "质量分类已完成，但原因分析失败，可重试"
        return result

    result.detail = report.detail
    result.lesson_text = report.lesson_text
    result.experience_text = report.experience_text
    result.analysis_status = "done"

    return result


def confirm_ingest(result: AutoAnalysisResult) -> str:
    if not result.quality_category:
        logger.warning("无有效分析结果，无法入库")
        return ""

    if result.quality_category == "negative" and result.lesson_text:
        lesson = LessonSummary(
            article_id="",
            cause_categories=result.cause_categories,
            lesson_text=result.lesson_text,
            source_title=result.source_title,
        )
        lid = ingest_lesson(lesson)
        if lid:
            result.ingested_id = lid
            logger.info(f"自动分析教训入库成功: {lid}")
            return lid
        return ""

    if result.quality_category == "positive" and result.experience_text:
        experience = ExperienceSummary(
            article_id="",
            experience_text=result.experience_text,
            source_title=result.source_title,
        )
        eid = ingest_experience(experience)
        if eid:
            result.ingested_id = eid
            logger.info(f"自动分析经验入库成功: {eid}")
            return eid
        return ""

    logger.warning("无可入库内容")
    return ""
