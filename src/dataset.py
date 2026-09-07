"""Loader for the evaluation set in ``eval/questions.yaml``."""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_QUESTIONS = os.path.join(ROOT, "eval", "questions.yaml")

KINDS = ("reference", "ambiguous", "unanswerable")


@dataclass
class Question:
    id: str
    question: str
    set: str
    kind: str
    ordered: bool = False
    reference_sql: Optional[str] = None
    missing_field: Optional[str] = None
    accept_missing: List[str] = field(default_factory=list)
    undefined_terms: List[str] = field(default_factory=list)
    note: Optional[str] = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"{self.id}: kind must be one of {KINDS}, got {self.kind!r}")
        if self.kind == "reference" and not self.reference_sql:
            raise ValueError(f"{self.id}: kind 'reference' requires reference_sql")
        if self.kind == "unanswerable" and not self.accept_missing:
            raise ValueError(f"{self.id}: kind 'unanswerable' requires accept_missing")


def load_questions(path: str = DEFAULT_QUESTIONS) -> List[Question]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required: pip install -r requirements.txt") from exc

    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or []

    questions = [Question(**item) for item in raw]
    ids = [q.id for q in questions]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate question ids in {path}: {sorted(duplicates)}")
    return questions


def index_by_id(questions: List[Question]) -> Dict[str, Question]:
    return {q.id: q for q in questions}


def select(questions: List[Question], sets: Optional[List[str]] = None,
           ids: Optional[List[str]] = None) -> List[Question]:
    chosen = questions
    if sets:
        wanted = {s.lower() for s in sets}
        chosen = [q for q in chosen if q.set.lower() in wanted]
    if ids:
        wanted_ids = {i.strip() for i in ids}
        chosen = [q for q in chosen if q.id in wanted_ids]
    return chosen
