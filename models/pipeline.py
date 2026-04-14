"""Pipeline 数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StageResult:
    """单个 Stage 的执行结果"""
    stage_name: str = ""
    status: StageStatus = StageStatus.PENDING
    message: str = ""
    started_at: str = ""
    finished_at: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class PipelineContext:
    """Pipeline 执行上下文，在各 Stage 之间传递数据"""
    articles: list = field(default_factory=list)       # 爬取的文章列表
    chunks: list = field(default_factory=list)          # 分块后的文本
    hot_topics: list = field(default_factory=list)      # 热点话题列表
    generated_article: Optional[dict] = None            # 生成的文章
    image_paths: list = field(default_factory=list)     # 生成的图片路径
    stage_results: list = field(default_factory=list)   # 各 Stage 执行结果
    error: Optional[str] = None

    def add_stage_result(self, result: StageResult):
        self.stage_results.append(result)

    def get_last_result(self) -> Optional[StageResult]:
        return self.stage_results[-1] if self.stage_results else None
