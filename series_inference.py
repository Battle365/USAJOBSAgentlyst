"""Occupation-neutral, evidence-based federal series inference.

The public USAJOBS occupational-series code list supplies the canonical codes.
Aliases only bridge common resume job titles to those official series names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests

from models import EvidenceUnit, ResumeRecord


CATALOG_URL = "https://data.usajobs.gov/api/codelist/occupationalseries"
INFERENCE_VERSION = "series-v2-2026-09"
MIN_CONFIDENCE = 70
MAX_CONFIDENT_SERIES = 5

# Verified against the public USAJOBS code list. Used only if it is unavailable.
FALLBACK_CATALOG = {
    "0185": "Social Work", "0301": "Miscellaneous Administration And Program",
    "0340": "Program Management", "0341": "Administrative Officer",
    "0343": "Management And Program Analysis", "0501": "Financial Administration And Program",
    "0505": "Financial Management", "0510": "Accounting", "0560": "Budget Analysis",
    "0602": "Medical Officer", "0603": "Physician Assistant", "0610": "Nurse",
    "0620": "Practical Nurse", "0801": "General Engineering", "0810": "Civil Engineering",
    "0830": "Mechanical Engineering", "0850": "Electrical Engineering",
    "0905": "Attorney", "1102": "Contracting", "1515": "Operations Research",
    "1530": "Statistics", "1560": "Data Science Series",
    "2210": "Information Technology Management",
}

# No Program Analyst preference: every alias uses the same confidence rules.
ROLE_ALIASES = {
    "0185": ("social worker", "clinical social worker"),
    "0301": ("administrative specialist", "administrative program specialist"),
    "0340": ("program manager",),
    "0341": ("administrative officer",),
    "0343": ("program analyst", "management analyst"),
    "0501": ("financial analyst", "financial administrator"),
    "0505": ("financial manager",),
    "0510": ("accountant",),
    "0560": ("budget analyst",),
    "0602": ("physician", "medical doctor", "doctor of medicine"),
    "0603": ("physician assistant", "physician associate"),
    "0610": ("registered nurse", "nurse practitioner", "staff nurse"),
    "0620": ("licensed practical nurse", "practical nurse"),
    "0801": ("general engineer",),
    "0810": ("civil engineer",),
    "0830": ("mechanical engineer",),
    "0850": ("electrical engineer",),
    "0905": ("attorney", "lawyer"),
    "1102": ("contract specialist", "contracting officer"),
    "1515": ("operations research analyst",),
    "1530": ("statistician",),
    "1560": ("data scientist",),
    "2210": ("it specialist", "information technology specialist", "systems administrator", "cybersecurity specialist"),
}

# Distinct performed-work signals; alone they never produce a qualification MATCH.
DUTY_CUES = {
    "0185": ("case management", "psychosocial assessment", "clinical counseling", "social services"),
    "0343": ("program evaluation", "performance measurement", "program analysis", "process improvement"),
    "0505": ("financial reporting", "financial controls", "financial management"),
    "0510": ("general ledger", "financial statements", "account reconciliation"),
    "0560": ("budget formulation", "budget execution", "appropriations analysis"),
    "0602": ("diagnosis and treatment", "patient examination", "clinical decision making"),
    "0603": ("patient assessment", "diagnostic evaluation", "treatment plans"),
    "0610": ("patient assessment", "nursing care", "medication administration", "care planning"),
    "0801": ("engineering design", "technical specifications", "engineering analysis"),
    "2210": ("systems administration", "network security", "software deployment", "it infrastructure"),
}


@dataclass(frozen=True)
class SeriesCandidate:
    code: str
    name: str
    confidence: int
    evidence: tuple[str, ...]
    resume_evidence: tuple[EvidenceUnit, ...] = ()
    duty_signals: tuple[str, ...] = ()


_catalog_cache: tuple[datetime, dict[str, str]] | None = None


def active_series_catalog(session=None) -> tuple[dict[str, str], bool]:
    """Return active official code values, or a clearly identified local fallback."""
    global _catalog_cache
    now = datetime.now(UTC)
    if session is None and _catalog_cache and now - _catalog_cache[0] < timedelta(hours=24):
        return dict(_catalog_cache[1]), True
    client = session or requests.Session()
    if session is None:
        client.trust_env = False
    try:
        response = client.get(CATALOG_URL, timeout=20, headers={"Accept": "application/json"})
        response.raise_for_status()
        values = response.json()["CodeList"][0]["ValidValue"]
        catalog = {
            str(row["Code"]): str(row["Value"]).strip()
            for row in values
            if str(row.get("IsDisabled", "")).lower() == "no"
            and re.fullmatch(r"\d{4}", str(row.get("Code", "")))
            and row.get("Value")
        }
        if len(catalog) < 100:
            raise ValueError("Occupational-series code list was incomplete.")
        if session is None:
            _catalog_cache = (now, catalog)
        return catalog, True
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return dict(FALLBACK_CATALOG), False


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.I) is not None


def _role_evidence(text: str, title: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    title = title.lower()
    match = re.search(rf"(?<!\w){re.escape(title)}(?!\w)", normalized)
    if not match:
        return False
    suffix = normalized[match.end():].strip()
    if title == "physician" and suffix.startswith(("assistant", "associate")):
        return False
    prefix = normalized[: match.start()].strip(" ,-—|:")
    if not prefix or prefix in {"senior", "lead", "chief", "supervisory", "licensed", "registered"}:
        return True
    if re.search(r"\b(?:worked|served|employed) as (?:a|an)?\s*$", prefix):
        return True
    # A role followed by a date/employer near the beginning is a position line.
    return len(prefix) <= 18 and bool(re.search(r"\b(?:19|20)\d{2}\b", normalized))


def infer_series(resume: ResumeRecord, catalog: dict[str, str]) -> tuple[SeriesCandidate, ...]:
    experience = [unit for unit in resume.evidence if unit.section == "experience"]
    other = [unit for unit in resume.evidence if unit.section in {"education", "certifications", "licenses", "training"}]
    candidates: list[SeriesCandidate] = []
    for code, name in catalog.items():
        signals: list[str] = []
        supporting: list[EvidenceUnit] = []
        explicit_units = [unit for unit in experience if re.search(rf"\b(?:GS[- /]?)?{re.escape(code)}\b", unit.text, re.I)]
        if explicit_units:
            signals.append(f"explicit occupational series {code}")
            supporting.extend(explicit_units)
        aliases = (*ROLE_ALIASES.get(code, ()), name)
        role = next(((alias, unit) for alias in aliases for unit in experience if _role_evidence(unit.text, alias)), None)
        if role:
            signals.append(f"documented role: {role[0]}")
            supporting.append(role[1])
        cue_units = [(cue, next((unit for unit in experience if _contains(unit.text, cue)), None)) for cue in DUTY_CUES.get(code, ())]
        cues = [cue for cue, unit in cue_units if unit is not None]
        if len(cues) >= 2:
            signals.append("performed-work signals: " + ", ".join(cues[:3]))
            supporting.extend(unit for _, unit in cue_units if unit is not None)
        credential_units: list[EvidenceUnit] = []
        if code in {"0602", "0603", "0610", "0620", "0185"}:
            credential_units = [unit for unit in other if any(_contains(unit.text, phrase) for phrase in ("license", "licensed", "registered nurse", "board certified", "medical degree", "social work degree"))]
            if credential_units:
                signals.append("related education or credential stated")
                supporting.extend(credential_units)
        confidence = min(100, (100 if explicit_units else 0) or (80 if role else 0) or (55 if len(cues) >= 2 else 0))
        if confidence == 55 and len(cues) >= 3:
            confidence = 75
        if confidence == 55 and credential_units:
            confidence = 75
        if confidence >= MIN_CONFIDENCE:
            candidates.append(SeriesCandidate(code, name, confidence, tuple(signals), tuple(dict.fromkeys(supporting)), tuple(cues)))
    return tuple(sorted(candidates, key=lambda item: (-item.confidence, item.code))[:MAX_CONFIDENT_SERIES])
