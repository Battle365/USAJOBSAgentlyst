"""Structured, evidence-linked résumé profile for bounded job discovery.

This profile suggests where to search. It does not establish qualifications;
the independent matching engine evaluates each actual announcement.
"""

from __future__ import annotations

from dataclasses import dataclass

from models import EvidenceUnit, ResumeRecord
from series_inference import MAX_CONFIDENT_SERIES, active_series_catalog, infer_series


PROFILE_VERSION = "profile-v1"


@dataclass(frozen=True)
class DiscoveryConstraints:
    minimum_gs_grade: int = 11
    maximum_series: int = MAX_CONFIDENT_SERIES
    maximum_announcement_texts: int = 18


@dataclass(frozen=True)
class SupportedSeries:
    code: str
    name: str
    confidence: int
    occupational_signals: tuple[str, ...]
    duty_signals: tuple[str, ...]
    resume_evidence: tuple[EvidenceUnit, ...]


@dataclass(frozen=True)
class ResumeProfile:
    resume_hash: str
    series_candidates: tuple[SupportedSeries, ...]
    constraints: DiscoveryConstraints
    authoritative_catalog: bool
    version: str = PROFILE_VERSION


def extract_profile(
    resume: ResumeRecord,
    catalog: dict[str, str] | None = None,
    *,
    authoritative_catalog: bool | None = None,
    constraints: DiscoveryConstraints | None = None,
) -> ResumeProfile:
    """Infer only confidently supported series and retain their exact sources."""
    if catalog is None:
        catalog, official = active_series_catalog()
    else:
        official = True if authoritative_catalog is None else authoritative_catalog
    limits = constraints or DiscoveryConstraints()
    inferred = infer_series(resume, catalog)[:limits.maximum_series]
    candidates = tuple(
        SupportedSeries(
            code=item.code,
            name=item.name,
            confidence=item.confidence,
            occupational_signals=item.evidence,
            duty_signals=item.duty_signals,
            resume_evidence=item.resume_evidence,
        )
        for item in inferred
    )
    return ResumeProfile(resume.content_hash, candidates, limits, official)
