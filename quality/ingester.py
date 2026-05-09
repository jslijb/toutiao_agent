"""经验教训入库器"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

from config.settings import PROJECT_ROOT, settings
from quality.models import LessonSummary, ExperienceSummary
from rag.embedder import create_embedder
from rag.vectorstore import FAISSVectorStore


def _knowledge_file() -> Path:
    return PROJECT_ROOT / "data" / "db" / "quality_knowledge.json"


def _load_knowledge() -> dict:
    f = _knowledge_file()
    if not f.exists():
        return {"lessons": [], "experiences": []}
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {"lessons": [], "experiences": []}


def _save_knowledge(data: dict) -> None:
    f = _knowledge_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def ingest_lesson(lesson: LessonSummary) -> str:
    """入库反面教训摘要到FAISS"""
    try:
        embedder = create_embedder()
        vs = FAISSVectorStore()
        if vs.total == 0:
            vs.load()

        vector = embedder.embed_query(lesson.lesson_text)
        meta = {
            "text": lesson.lesson_text,
            "title": f"[教训] {lesson.source_title}",
            "source": "quality_lesson",
            "url": "",
            "quality_label": "negative_lesson",
            "lesson_id": lesson.id,
            "cause_categories": json.dumps(lesson.cause_categories, ensure_ascii=False),
        }
        vs.add_vectors(
            vectors=vector.reshape(1, -1),
            metas=[meta],
            urls=[f"lesson://{lesson.id}"],
        )
        vs.save()

        knowledge = _load_knowledge()
        knowledge["lessons"].append(asdict(lesson))
        _save_knowledge(knowledge)

        logger.info(f"教训入库成功: {lesson.id} - {lesson.lesson_text[:50]}")
        return lesson.id
    except Exception as e:
        logger.error(f"教训入库失败: {e}")
        _save_pending("lesson", asdict(lesson))
        return ""


def ingest_experience(experience: ExperienceSummary) -> str:
    """入库正面经验摘要到FAISS"""
    try:
        embedder = create_embedder()
        vs = FAISSVectorStore()
        if vs.total == 0:
            vs.load()

        vector = embedder.embed_query(experience.experience_text)
        meta = {
            "text": experience.experience_text,
            "title": f"[经验] {experience.source_title}",
            "source": "quality_experience",
            "url": "",
            "quality_label": "positive_experience",
            "experience_id": experience.id,
        }
        vs.add_vectors(
            vectors=vector.reshape(1, -1),
            metas=[meta],
            urls=[f"experience://{experience.id}"],
        )
        vs.save()

        knowledge = _load_knowledge()
        knowledge["experiences"].append(asdict(experience))
        _save_knowledge(knowledge)

        logger.info(f"经验入库成功: {experience.id} - {experience.experience_text[:50]}")
        return experience.id
    except Exception as e:
        logger.error(f"经验入库失败: {e}")
        _save_pending("experience", asdict(experience))
        return ""


def list_knowledge() -> dict:
    """列出所有教训和经验"""
    return _load_knowledge()


def delete_knowledge(item_type: str, item_id: str) -> bool:
    """删除（标记deprecated）教训或经验"""
    knowledge = _load_knowledge()
    key = "lessons" if item_type == "lesson" else "experiences"
    for item in knowledge.get(key, []):
        if item.get("id") == item_id:
            item["deprecated"] = True
            _save_knowledge(knowledge)
            return True
    return False


def _save_pending(item_type: str, data: dict) -> None:
    """保存入库失败的条目"""
    pending_file = PROJECT_ROOT / "data" / "db" / "pending_ingest.json"
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending = []
    if pending_file.exists():
        try:
            with open(pending_file, "r", encoding="utf-8") as f:
                pending = json.load(f)
        except Exception:
            pass
    pending.append({"type": item_type, "data": data})
    with open(pending_file, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)
    logger.info(f"入库失败条目已暂存: {item_type}/{data.get('id', '?')}")
