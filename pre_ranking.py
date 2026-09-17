"""Experimental metadata-only selection for Announcement Text retrieval.

This module is not called by the active V4 discovery path. Its priorities decide
only which summaries receive deeper analysis, never qualification outcomes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from math import ceil

from historic_poc import is_open
from profile_extraction import ResumeProfile, SupportedSeries


GENERIC_TITLE_WORDS = {
    "and", "assistant", "federal", "general", "lead", "officer", "position",
    "senior", "specialist", "supervisory", "the",
}
GENERIC_DUTY_WORDS = {"patient", "care", "nursing", "budget", "program", "management", "information", "technology"}


@dataclass(frozen=True)
class RankedCandidate:
    summary: dict
    anchor_series: str
    represented_series: tuple[str, ...]
    series_confidence: int
    title_tier: int
    duty_tier: int
    scope_flags: tuple[str, ...]
    review_flags: tuple[str, ...]
    public_path: bool
    reasons: tuple[str, ...]


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9]+", value.lower())) - GENERIC_TITLE_WORDS


def _title_fit(summary: dict, series: SupportedSeries) -> tuple[int, str]:
    title = str(summary.get("positionTitle") or "").lower()
    if not title.strip():
        return 0, ""
    roles = [signal.split(":", 1)[1].strip() for signal in series.occupational_signals if signal.startswith("documented role:")]
    phrases = roles + [series.name]
    if any(re.search(rf"(?<!\w){re.escape(phrase.lower())}(?!\w)", title) for phrase in phrases if phrase):
        return 2, "documented occupation appears in title"
    terms = set().union(*(_words(phrase) for phrase in phrases + list(series.duty_signals)))
    if _words(title) & terms:
        return 1, "title overlaps an occupational or duty signal"
    return 0, ""


def _duty_fit(summary: dict, series: SupportedSeries) -> tuple[int, str]:
    """Compare performed-duty cues with the title; no qualification inference."""
    title = _words(str(summary.get("positionTitle") or ""))
    normalized = set(title)
    if "sysadmin" in title:
        normalized.update(("systems", "administration"))
    if "infosec" in title:
        normalized.update(("information", "security"))
    best = 0
    for signal in series.duty_signals:
        words = {word for word in _words(signal) if len(word) >= 5}
        overlap = words & normalized
        distinctive_overlap = overlap - GENERIC_DUTY_WORDS
        best = max(best, 2 if len(words) >= 2 and len(overlap) == len(words) else 1 if distinctive_overlap else 0)
    return best, ("title reflects performed-duty signal" if best == 2 else "title shares a performed-duty term" if best else "")


def _scope_flags(summary: dict) -> tuple[str, ...]:
    title = str(summary.get("positionTitle") or "").lower()
    flags = []
    if re.search(r"\b(nurse practitioner|advanced practice|consultant|clinical research|research)\b", title):
        flags.append("specialized clinical/research role")
    if re.search(r"\b(supervisory|supervisor|chief|executive|director|cio|officer|lead)\b|\b(?:nurse|program|patient safety) manager\b", title):
        flags.append("senior or supervisory scope")
    if re.search(r"\b(?:o-[1-9]|military)\b|\bbillet\b", title):
        flags.append("military billet or designation")
    if re.search(r"\btitle\s*32\b", title):
        flags.append("special appointment scope")
    return tuple(flags)


def _review_flags(summary: dict) -> tuple[str, ...]:
    title = str(summary.get("positionTitle") or "").lower()
    pay = str(summary.get("payScale") or "").upper()
    flags = []
    if pay and pay != "GS":
        flags.append("non-GS pay plan; equivalence not established")
    if re.search(r"\btitle\s*32\b|\bnf-\d|\bbillet\b", title):
        flags.append("appointment or billet needs review")
    return tuple(flags)


def _public_path(summary: dict) -> bool:
    paths = []
    # Live Historic JOA summaries use lowercase ``hiringpaths``. Keep the
    # older camel-case shape readable for frozen or alternate provider data.
    hiring_paths = summary.get("hiringpaths")
    if hiring_paths is None:
        hiring_paths = summary.get("hiringPaths")
    if isinstance(hiring_paths, (list, tuple)):
        paths.extend(str(item.get("hiringPath") or "") for item in hiring_paths if isinstance(item, dict))
    if not any(value.strip() for value in paths):
        paths = [str(summary.get("whoMayApply") or "")]
    return any(phrase in value.lower() for value in paths for phrase in ("the public", "united states citizens", "u.s. citizens"))


def _completeness(summary: dict) -> int:
    return sum(bool(summary.get(key)) for key in ("positionTitle", "hiringAgencyName", "positionlocations", "minimumGrade")) + int(bool(summary.get("whoMayApply") or summary.get("hiringpaths") or summary.get("hiringPaths")))


def _opened(summary: dict) -> int:
    value = str(summary.get("positionOpenDate") or "").replace("-", "")[:8]
    return int(value) if value.isdigit() else 0


def _agency(summary: dict) -> str:
    return str(summary.get("hiringAgencyName") or summary.get("hiringDepartmentName") or "").strip().casefold()


def _locations(summary: dict) -> tuple[str, ...]:
    return tuple(sorted({
        ", ".join(str(item.get(key) or "").strip().casefold() for key in ("positionLocationCity", "positionLocationState") if item.get(key))
        for item in summary.get("positionlocations") or [] if isinstance(item, dict)
    } - {""}))


def _selection_key(item: RankedCandidate, selected: list[RankedCandidate]) -> tuple:
    used_agencies = {_agency(previous.summary) for previous in selected} - {""}
    used_locations = {location for previous in selected for location in _locations(previous.summary)}
    agency = _agency(item.summary)
    locations = _locations(item.summary)
    return (
        -item.series_confidence,
        -item.duty_tier,
        len(item.scope_flags),
        -item.title_tier,
        -int(item.public_path),
        -_opened(item.summary),
        -int(bool(agency) and agency not in used_agencies),
        -int(bool(locations) and not set(locations) & used_locations),
        str(item.summary["usajobsControlNumber"]),
    )


def select_for_text_experimental(
    profile: ResumeProfile,
    by_series: dict[str, list[dict]],
    *,
    today: date,
) -> tuple[RankedCandidate, ...]:
    """Select at most 18 eligible unique summaries, with series representation."""
    # Share the active eligibility gate without coupling module imports. A
    # future opt-in from job_discovery can call this function without a cycle.
    from job_discovery import MAX_TEXT_REQUESTS, grade_eligible

    limit = max(0, min(MAX_TEXT_REQUESTS, profile.constraints.maximum_announcement_texts))
    if not limit or not profile.series_candidates:
        return ()
    supported = {item.code: item for item in profile.series_candidates}
    unique: dict[str, tuple[dict, set[str]]] = {}
    for code in sorted(set(by_series) & set(supported)):
        for row in by_series[code]:
            control = str(row.get("usajobsControlNumber") or "")
            if not control.isdigit() or not is_open(row, today) or not grade_eligible(row, profile.constraints.minimum_gs_grade):
                continue
            if control not in unique:
                unique[control] = (row, {code})
            else:
                previous, represented = unique[control]
                represented.add(code)
                # Duplicate summaries can differ across series pages. Choose a
                # complete, deterministic copy without changing the identifier.
                if (-_completeness(row), json.dumps(row, sort_keys=True, default=str)) < (-_completeness(previous), json.dumps(previous, sort_keys=True, default=str)):
                    unique[control] = (row, represented)
    candidates: list[RankedCandidate] = []
    for control, (row, represented) in unique.items():
        anchor = min((supported[code] for code in represented), key=lambda item: (-item.confidence, item.code))
        title_tier, title_reason = _title_fit(row, anchor)
        duty_tier, duty_reason = _duty_fit(row, anchor)
        scope_flags = _scope_flags(row)
        review_flags = _review_flags(row)
        public = _public_path(row)
        reasons = [f"supported series {anchor.code} (confidence {anchor.confidence})"]
        if duty_reason:
            reasons.append(duty_reason)
        if title_reason:
            reasons.append(title_reason)
        if public:
            reasons.append("public hiring path listed")
        reasons.extend(scope_flags)
        reasons.extend(review_flags)
        candidates.append(RankedCandidate(row, anchor.code, tuple(sorted(represented)), anchor.confidence, title_tier, duty_tier, scope_flags, review_flags, public, tuple(reasons)))
    chosen: list[RankedCandidate] = []
    chosen_ids: set[str] = set()

    def add(item: RankedCandidate) -> None:
        chosen.append(item)
        chosen_ids.add(str(item.summary["usajobsControlNumber"]))

    # First pass guarantees one distinct announcement per supported series
    # when the pool and retrieval budget permit it.
    for series in profile.series_candidates:
        if len(chosen) >= limit:
            break
        options = [item for item in candidates if series.code in item.represented_series and str(item.summary["usajobsControlNumber"]) not in chosen_ids]
        if options:
            add(min(options, key=lambda item: _selection_key(item, chosen)))

    represented_count = sum(any(item.anchor_series == series.code for item in candidates) for series in profile.series_candidates)
    soft_cap = ceil(limit / max(1, represented_count)) + 1
    while len(chosen) < limit:
        remaining = [item for item in candidates if str(item.summary["usajobsControlNumber"]) not in chosen_ids]
        if not remaining:
            break
        under_cap = [item for item in remaining if sum(chosen_item.anchor_series == item.anchor_series for chosen_item in chosen) < soft_cap]
        add(min(under_cap or remaining, key=lambda item: _selection_key(item, chosen)))
    return tuple(chosen)
