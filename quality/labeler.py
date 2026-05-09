"""质量标注服务 - 编排标注全流程"""
from __future__ import annotations

from datetime import datetime

from loguru import logger

from quality.cause_attributor import validate_cause_categories, merge_with_suggestion
from quality.analyzer import analyze
from quality.ingester import ingest_lesson, ingest_experience
from quality.article_resolver import resolve_article
from quality.models import (
    LessonSummary, ExperienceSummary, QualityLabelResult,
    ARTICLE_SOURCE_DATA, ARTICLE_SOURCE_GENERATED,
)


def _writeback_label(resolved, label_data: dict) -> bool:
    """根据 source_type 写回对应 Store"""
    if resolved.source_type == ARTICLE_SOURCE_GENERATED:
        try:
            from models.generated_store import get_generated_store
            gen_store = get_generated_store()
            return gen_store.update(resolved.article_id, **label_data)
        except Exception as e:
            logger.error(f"GeneratedArticleStore写回失败: {e}")
            return False

    try:
        from models.article_store import get_article_store
        store = get_article_store()
        store._ensure_loaded()
        for article in store._index.values():
            if article.id == resolved.article_id:
                for key, value in label_data.items():
                    setattr(article, key, value)
                store._save_all()
                return True
        return False
    except Exception as e:
        logger.error(f"ArticleStore写回失败: {e}")
        return False


def submit_label(
    article_id: str,
    quality_category: str,
    limit_flow: bool = False,
    cause_categories: list[str] | None = None,
    label_reason: str = "",
) -> QualityLabelResult:
    """提交质量标注并触发全流程：校验→分析→入库

    Args:
        article_id: 文章ID
        quality_category: "positive" 或 "negative"
        limit_flow: 是否被限流
        cause_categories: 原因分类列表
        label_reason: 标注原因备注
    """
    result = QualityLabelResult()

    if quality_category not in ("positive", "negative"):
        result.message = "质量分类必须为 positive 或 negative"
        return result

    try:
        resolved = resolve_article(article_id)
        if not resolved:
            result.message = f"文章不存在: {article_id}"
            return result
    except Exception as e:
        result.message = f"获取文章失败: {e}"
        return result

    if resolved.source_type == ARTICLE_SOURCE_GENERATED:
        try:
            from models.generated_store import get_generated_store
            gen_store = get_generated_store()
            gen_article = gen_store.get(article_id)
            if gen_article and gen_article.quality_category:
                result.message = f"该文章已标注为{gen_article.quality_category}，如需修改请先清除原标注"
                return result
        except Exception:
            pass

    validated_causes = validate_cause_categories(
        categories=cause_categories or [],
        quality_category=quality_category,
        limit_flow=limit_flow,
    )

    try:
        report = analyze(
            title=resolved.title,
            content=resolved.content,
            quality_category=quality_category,
            cause_categories=validated_causes,
            label_reason=label_reason,
        )
    except Exception as e:
        logger.error(f"LLM分析失败: {e}")
        report = None

    lesson_ids = []
    experience_ids = []

    if quality_category == "negative" and report and report.lesson_text:
        lesson = LessonSummary(
            article_id=article_id,
            cause_categories=validated_causes,
            lesson_text=report.lesson_text,
            source_title=resolved.title,
        )
        lid = ingest_lesson(lesson)
        if lid:
            lesson_ids.append(lid)

    if quality_category == "positive" and report and report.experience_text:
        experience = ExperienceSummary(
            article_id=article_id,
            experience_text=report.experience_text,
            source_title=resolved.title,
        )
        eid = ingest_experience(experience)
        if eid:
            experience_ids.append(eid)

    if report and report.cause_suggestion:
        validated_causes = merge_with_suggestion(validated_causes, report.cause_suggestion)

    label_data = {
        "quality_category": quality_category,
        "limit_flow": limit_flow,
        "cause_categories": validated_causes,
        "label_reason": label_reason,
        "labeled_at": datetime.now().isoformat(),
        "analysis_status": "done" if report else "failed",
        "analysis_report": report.detail if report else "",
        "lesson_ids": lesson_ids,
        "experience_ids": experience_ids,
    }

    writeback_ok = _writeback_label(resolved, label_data)
    if not writeback_ok:
        logger.error(f"标注结果写回失败: {article_id}")

    result.success = True
    result.message = f"标注完成: {quality_category}, 教训{len(lesson_ids)}条, 经验{len(experience_ids)}条"
    result.lesson_ids = lesson_ids
    result.experience_ids = experience_ids
    if report:
        result.analysis_report = {
            "detail": report.detail,
            "lesson_text": report.lesson_text,
            "experience_text": report.experience_text,
        }

    logger.info(f"质量标注完成: {article_id} -> {quality_category}")
    return result
