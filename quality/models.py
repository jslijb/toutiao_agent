"""质量分析相关数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
import uuid


CAUSE_CATEGORIES = [
    "title_issue",
    "content_hollow",
    "forbidden_words",
    "structure_chaos",
    "irrelevant_topic",
    "limit_flow_penalty",
    "other",
]

CAUSE_CATEGORY_LABELS = {
    "title_issue": "标题问题",
    "content_hollow": "内容空洞",
    "forbidden_words": "违规词汇",
    "structure_chaos": "结构混乱",
    "irrelevant_topic": "与热点无关",
    "limit_flow_penalty": "限流降权",
    "other": "其他",
}

ARTICLE_SOURCE_DATA = "data_source"
ARTICLE_SOURCE_GENERATED = "generated"
ARTICLE_SOURCE_PASTED = "pasted"


@dataclass
class LessonSummary:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    article_id: str = ""
    cause_categories: list[str] = field(default_factory=list)
    lesson_text: str = ""
    source_title: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    deprecated: bool = False


@dataclass
class ExperienceSummary:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    article_id: str = ""
    experience_text: str = ""
    source_title: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    deprecated: bool = False


@dataclass
class QualityAnalysisReport:
    article_id: str = ""
    quality_category: str = ""
    cause_suggestion: list[str] = field(default_factory=list)
    summary: str = ""
    detail: str = ""
    lesson_text: str = ""
    experience_text: str = ""


@dataclass
class ScoreDistribution:
    min_score: float = 0.0
    max_score: float = 0.0
    avg_score: float = 0.0
    median_score: float = 0.0


@dataclass
class RetrievalMatchRecord:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    query: str = ""
    retrieval_type: str = ""
    match_count: int = 0
    injected_count: int = 0
    score_distribution: dict = field(default_factory=dict)
    top_scores: list[float] = field(default_factory=list)
    target_article_id: str = ""
    retrieved_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class EnhancedRetrievalResult:
    context: str = ""
    avoidance_guide: str = ""
    experience_guide: str = ""
    rag_match_count: int = 0
    lesson_match_count: int = 0
    experience_match_count: int = 0


@dataclass
class AutoAnalysisInput:
    title: str = ""
    content: str = ""


@dataclass
class AutoAnalysisResult:
    quality_category: str = ""
    cause_categories: list[str] = field(default_factory=list)
    detail: str = ""
    lesson_text: str = ""
    experience_text: str = ""
    source_title: str = ""
    article_source_type: str = "pasted"
    analysis_status: str = ""
    classify_reason: str = ""
    ingested_id: str = ""


@dataclass
class QualityLabelResult:
    success: bool = False
    message: str = ""
    analysis_report: Optional[dict] = None
    lesson_ids: list[str] = field(default_factory=list)
    experience_ids: list[str] = field(default_factory=list)
