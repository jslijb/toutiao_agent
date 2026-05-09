"""数据源相关数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SourceStatus(Enum):
    available = "available"
    disabled = "disabled"
    quota_exceeded = "quota_exceeded"
    error = "error"


@dataclass
class SourceHealth:
    name: str
    status: SourceStatus
    message: str = ""
    quota_remaining: Optional[int] = None
    balance_remaining: Optional[float] = None


@dataclass
class ImportResult:
    dataset_type: str
    total_read: int = 0
    filtered_quality: int = 0
    filtered_dedup: int = 0
    imported: int = 0
    elapsed_seconds: float = 0.0
    resumed_from: int = 0
    error: Optional[str] = None


@dataclass
class QuotaState:
    bing_used_count: int = 0
    bing_monthly_limit: int = 1000
    bing_reset_month: str = ""
    tianapi_used_beans: float = 0.0
    tianapi_total_beans: float = 0.0
    last_updated: str = ""


@dataclass
class RSSSourceConfig:
    name: str
    url: str
    enabled: bool = True
