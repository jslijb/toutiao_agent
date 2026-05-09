"""低质量原因归因服务"""
from __future__ import annotations

from loguru import logger

from quality.models import CAUSE_CATEGORIES, CAUSE_CATEGORY_LABELS


def validate_cause_categories(
    categories: list[str],
    quality_category: str,
    limit_flow: bool = False,
) -> list[str]:
    """校验原因分类，劣质文章必填，限流自动追加标签"""
    valid = [c for c in categories if c in CAUSE_CATEGORIES]
    invalid = [c for c in categories if c not in CAUSE_CATEGORIES]
    if invalid:
        logger.warning(f"无效的原因分类已过滤: {invalid}")

    if limit_flow and "limit_flow_penalty" not in valid:
        valid.append("limit_flow_penalty")

    if quality_category == "negative" and not valid:
        logger.warning("劣质文章未填写原因分类，自动添加'other'")
        valid.append("other")

    return valid


def get_available_categories() -> list[dict]:
    """获取可用原因分类列表"""
    return [
        {"value": c, "label": CAUSE_CATEGORY_LABELS[c]}
        for c in CAUSE_CATEGORIES
    ]


def get_statistics(articles: list) -> dict:
    """统计各原因分类出现次数"""
    stats = {c: 0 for c in CAUSE_CATEGORIES}
    for article in articles:
        cats = getattr(article, "cause_categories", [])
        for c in cats:
            if c in stats:
                stats[c] += 1
    return stats


def merge_with_suggestion(
    user_categories: list[str],
    llm_suggestion: list[str],
) -> list[str]:
    """合并用户选择和LLM建议的原因分类"""
    merged = list(user_categories)
    for c in llm_suggestion:
        if c in CAUSE_CATEGORIES and c not in merged:
            merged.append(c)
    return merged
