"""User-facing triage of audited binary matcher decisions.

Processing errors never enter this layer. MATCH remains the core engine's
all-mandatory-requirements decision; POSSIBLE MATCH is only a duration-evidence
uncertainty with performed work cited and no other unsupported requirement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from models import DecisionRecord, EvidenceCitation, RequirementEvaluation


ClassificationLabel = Literal["MATCH", "POSSIBLE MATCH", "NOT A MATCH"]
ORDER = {"MATCH": 0, "POSSIBLE MATCH": 1, "NOT A MATCH": 2}


@dataclass(frozen=True)
class ClassifiedDecision:
    label: ClassificationLabel
    explanation: str
    evidence: tuple[EvidenceCitation, ...]
    gaps: tuple[str, ...]
    supported_count: int


def _citations(path: tuple[RequirementEvaluation, ...]) -> tuple[EvidenceCitation, ...]:
    seen: set[tuple[str, str, int | None]] = set()
    result: list[EvidenceCitation] = []
    for item in path:
        for citation in item.citations:
            key = (citation.excerpt, citation.section, citation.page)
            if key not in seen:
                seen.add(key)
                result.append(citation)
    return tuple(result)


def _is_duration_uncertainty(item: RequirementEvaluation) -> bool:
    return (
        not item.satisfied
        and item.requirement.kind == "experience"
        and bool(item.citations)
        and item.note == "resume shows related work, but its duration is not established"
    )


def classify(decision: DecisionRecord) -> ClassifiedDecision:
    if decision.final_outcome == "MATCH":
        path = next(path for path in decision.paths if path and all(item.satisfied for item in path))
        return ClassifiedDecision(
            "MATCH",
            "The submitted résumé shows evidence for every mandatory requirement in one identified qualification path. This is not an eligibility determination.",
            _citations(path), (), sum(item.satisfied for item in path),
        )

    possible_paths = [
        path for path in decision.paths
        if path and any(_is_duration_uncertainty(item) for item in path)
        and all(item.satisfied or _is_duration_uncertainty(item) for item in path)
    ]
    if possible_paths:
        path = max(possible_paths, key=lambda items: sum(item.satisfied for item in items))
        gaps = tuple(dict.fromkeys(item.requirement.text for item in path if not item.satisfied))
        return ClassifiedDecision(
            "POSSIBLE MATCH",
            "The résumé documents related work, but does not establish the required duration. Review the official announcement and résumé before relying on this result.",
            _citations(path), gaps, sum(item.satisfied for item in path),
        )

    path = max(decision.paths, key=lambda items: (sum(item.satisfied for item in items), -sum(not item.satisfied for item in items)), default=())
    gaps = tuple(dict.fromkeys(item.requirement.text for item in path if not item.satisfied))
    first_failure = next((item for item in path if not item.satisfied), None)
    explanation = (
        first_failure.note[:1].upper() + first_failure.note[1:] + "." if first_failure
        else "The submitted résumé does not establish a complete qualification path."
    )
    return ClassifiedDecision("NOT A MATCH", explanation, _citations(path), gaps, sum(item.satisfied for item in path))


def rank_decisions(decisions: list[tuple[dict, DecisionRecord]]) -> list[tuple[dict, DecisionRecord, ClassifiedDecision]]:
    classified = [(vacancy, decision, classify(decision)) for vacancy, decision in decisions]
    return sorted(
        classified,
        key=lambda entry: (
            ORDER[entry[2].label],
            -entry[2].supported_count,
            -len(entry[2].evidence),
            str(entry[0].get("close_date") or ""),
            str(entry[0].get("id") or ""),
        ),
    )
