from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


DecisionLabel = Literal["MATCH", "NOT A MATCH"]


@dataclass(frozen=True)
class EvidenceUnit:
    text: str
    section: str
    page: int | None = None
    start_month: str | None = None
    end_month: str | None = None
    duration_months: int | None = None


@dataclass(frozen=True)
class ResumeRecord:
    file_id: str
    owner_id: str
    filename: str
    mime_type: str
    size: int
    uploaded_at: str
    content_hash: str
    extracted_text: str
    evidence: tuple[EvidenceUnit, ...]


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    kind: str
    text: str
    mandatory: bool = True
    duration_months: int | None = None


@dataclass(frozen=True)
class EvidenceCitation:
    requirement_id: str
    section: str
    page: int | None
    excerpt: str


@dataclass(frozen=True)
class RequirementEvaluation:
    requirement: Requirement
    satisfied: bool
    citations: tuple[EvidenceCitation, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class DecisionRecord:
    resume_hash: str
    vacancy_id: str
    vacancy_hash: str
    decision_version: str
    paths: tuple[tuple[RequirementEvaluation, ...], ...]
    final_outcome: DecisionLabel
    reason: str
    processed_at: str
    matched_grade: str | None = None
    eligibility_source_paths: tuple[str, ...] = ()
    eligibility_who_may_apply: str = ""
    eligibility_routes: tuple[RequirementEvaluation, ...] = ()
    error_state: None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProcessingError:
    vacancy_id: str
    title: str
    agency: str
    message: str
    retryable: bool = False


@dataclass
class EvaluationBatch:
    decisions: list[tuple[dict[str, Any], DecisionRecord]] = field(default_factory=list)
    errors: list[ProcessingError] = field(default_factory=list)
