"""RAG检索匹配记录器"""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import PROJECT_ROOT
from quality.models import RetrievalMatchRecord, ScoreDistribution


def _records_file() -> Path:
    return PROJECT_ROOT / "data" / "db" / "retrieval_match_records.json"


def _load_records() -> list[dict]:
    f = _records_file()
    if not f.exists():
        return []
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return []


def _save_records(records: list[dict]) -> None:
    f = _records_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(records, fp, ensure_ascii=False, indent=2)


def log_retrieval_match(
    query: str,
    retrieval_type: str,
    scores: list[float],
    injected_count: int,
    target_article_id: str = "",
) -> RetrievalMatchRecord:
    """记录一次检索匹配"""
    score_dist = ScoreDistribution()
    if scores:
        score_dist = ScoreDistribution(
            min_score=round(min(scores), 4),
            max_score=round(max(scores), 4),
            avg_score=round(statistics.mean(scores), 4),
            median_score=round(statistics.median(scores), 4),
        )

    record = RetrievalMatchRecord(
        query=query[:200],
        retrieval_type=retrieval_type,
        match_count=len(scores),
        injected_count=injected_count,
        score_distribution=asdict(score_dist),
        top_scores=[round(s, 4) for s in sorted(scores, reverse=True)[:10]],
        target_article_id=target_article_id,
    )

    records = _load_records()
    records.append(asdict(record))
    if len(records) > 1000:
        records = records[-1000:]
    _save_records(records)

    logger.debug(f"检索匹配记录: type={retrieval_type}, matches={len(scores)}, injected={injected_count}")
    return record


def query_records(
    retrieval_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """查询检索匹配记录"""
    records = _load_records()
    if retrieval_type:
        records = [r for r in records if r.get("retrieval_type") == retrieval_type]
    return records[-limit:]


def export_records_csv(output_path: str) -> str:
    """导出检索匹配记录为CSV"""
    import csv
    records = _load_records()
    if not records:
        return "无记录可导出"

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "query", "retrieval_type", "match_count", "injected_count",
            "score_distribution", "top_scores", "target_article_id", "retrieved_at",
        ])
        writer.writeheader()
        for r in records:
            r["score_distribution"] = json.dumps(r.get("score_distribution", {}), ensure_ascii=False)
            r["top_scores"] = json.dumps(r.get("top_scores", []))
            writer.writerow(r)

    return f"已导出 {len(records)} 条记录到 {output_path}"


def get_statistics() -> dict:
    """获取检索匹配统计"""
    records = _load_records()
    if not records:
        return {"total": 0}

    by_type = {}
    for r in records:
        t = r.get("retrieval_type", "unknown")
        if t not in by_type:
            by_type[t] = {"count": 0, "total_matches": 0, "total_injected": 0}
        by_type[t]["count"] += 1
        by_type[t]["total_matches"] += r.get("match_count", 0)
        by_type[t]["total_injected"] += r.get("injected_count", 0)

    return {"total": len(records), "by_type": by_type}
