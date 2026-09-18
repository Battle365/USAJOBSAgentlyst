"""Provider-neutral job discovery orchestration; matching remains in matcher.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import requests

from historic_poc import DiscoveryError, discover, fetch_text, is_open, normalize_pair
from models import ProcessingError
from pre_ranking import select_for_text_experimental
from profile_extraction import ResumeProfile
from series_inference import active_series_catalog


MIN_GS_GRADE = 11
MAX_TEXT_REQUESTS = 18


class JobDiscoveryProvider(Protocol):
    def discover(self, series: str) -> object: ...
    def announcement(self, control_number: str) -> dict: ...
    def normalize(self, summary: dict, announcement: dict) -> dict: ...


class HistoricJoaProvider:
    """Official unauthenticated USAJOBS Historic JOA + Announcement Text adapter."""

    def __init__(self, session=None) -> None:
        self.session = session or requests.Session()
        if session is None:
            # Match the validated direct-access PoC. This also prevents a local
            # proxy setting from redirecting public USAJOBS requests elsewhere.
            self.session.trust_env = False

    def discover(self, series: str):
        return discover(series, session=self.session)

    def series_catalog(self) -> tuple[dict[str, str], bool]:
        return active_series_catalog()

    def announcement(self, control_number: str) -> dict:
        return fetch_text(control_number, session=self.session)

    def normalize(self, summary: dict, announcement: dict) -> dict:
        return normalize_pair(summary, announcement)


@dataclass(frozen=True)
class DiscoveryResult:
    series: tuple[str, ...]
    vacancies: tuple[dict, ...]
    errors: tuple[ProcessingError, ...]
    open_candidate_count: int
    complete: bool
    notices: tuple[str, ...]


def grade_eligible(summary: dict, minimum_gs_grade: int = MIN_GS_GRADE) -> bool:
    """Apply the GS floor; non-GS pay plans are retained for separate review."""
    pay_plan = str(summary.get("payScale") or "").upper().strip()
    if not pay_plan:
        return False
    if pay_plan != "GS":
        return True
    try:
        highest = int(str(summary.get("maximumGrade") or summary.get("minimumGrade") or ""))
    except ValueError:
        return False
    return highest >= minimum_gs_grade


def _recent_first(summary: dict) -> tuple[int, str]:
    opened = str(summary.get("positionOpenDate") or "").replace("-", "")[:8]
    return (-int(opened) if opened.isdigit() else 0, str(summary.get("usajobsControlNumber") or ""))


def _balanced_candidates(by_series: dict[str, list[dict]], codes: tuple[str, ...], limit: int = MAX_TEXT_REQUESTS) -> list[dict]:
    """Round-robin across supported series so one large series cannot dominate."""
    ordered = {code: sorted(by_series.get(code, []), key=_recent_first) for code in codes}
    selected: list[dict] = []
    seen: set[str] = set()
    while len(selected) < limit and any(ordered.values()):
        for code in codes:
            rows = ordered[code]
            if not rows:
                continue
            row = rows.pop(0)
            control = str(row.get("usajobsControlNumber") or "")
            if control not in seen:
                seen.add(control)
                selected.append(row)
                if len(selected) >= limit:
                    break
    return selected


def discover_for_profile(profile: ResumeProfile, provider: JobDiscoveryProvider | None = None) -> DiscoveryResult:
    adapter = provider or HistoricJoaProvider()
    series = tuple(candidate.code for candidate in profile.series_candidates)
    if not series:
        return DiscoveryResult((), (), (), 0, True, ("No occupational series could be inferred confidently from work experience. Use the manual announcement workflow.",))
    by_series: dict[str, list[dict]] = {}
    candidate_ids: set[str] = set()
    notices: list[str] = []
    errors: list[ProcessingError] = []
    complete = True
    non_gs_seen = False
    if not profile.authoritative_catalog:
        notices.append("The live occupational-series code list was unavailable; discovery used a smaller verified fallback catalog.")
    today = datetime.now(UTC).date()
    for code in series:
        try:
            page = adapter.discover(code)
        except (DiscoveryError, requests.RequestException, ValueError):
            complete = False
            notices.append(f"Series {code} could not be retrieved. Retry discovery or use a pasted announcement.")
            continue
        if not page.complete:
            complete = False
            notices.append(f"Series {code} was only partially retrieved; these results are not a complete search.")
        by_series[code] = []
        for row in page.candidates:
            control = str(row.get("usajobsControlNumber") or "")
            if control.isdigit() and is_open(row, today) and grade_eligible(row, profile.constraints.minimum_gs_grade):
                by_series[code].append(row)
                candidate_ids.add(control)
                non_gs_seen |= str(row.get("payScale") or "").upper().strip() != "GS"
    selected = [item.summary for item in select_for_text_experimental(profile, by_series, today=today)]
    if len(candidate_ids) > len(selected):
        notices.append(f"Qualification text was checked for {len(selected)} résumé-prioritized candidates out of {len(candidate_ids)} open grade-eligible announcements; results are not exhaustive.")
    if non_gs_seen:
        notices.append("The GS-11+ default applies to GS positions. Non-GS pay plans are shown separately because an authoritative grade equivalence is not established.")
    vacancies: list[dict] = []
    for row in selected:
        control = str(row["usajobsControlNumber"])
        if not is_open(row, today):
            continue
        try:
            detail = adapter.announcement(control)
            vacancy = adapter.normalize(row, detail)
            if not vacancy.get("qualifications"):
                raise DiscoveryError("Qualification text is missing.")
            vacancies.append(vacancy)
        except (DiscoveryError, requests.RequestException, KeyError, ValueError):
            errors.append(ProcessingError(control, str(row.get("positionTitle") or "Untitled position"), str(row.get("hiringAgencyName") or "Federal agency"), "Qualification text could not be retrieved or validated.", True))
    notices.append("Discovered announcements are based only on occupational series inferred from the résumé; they are not an exhaustive USAJOBS search.")
    return DiscoveryResult(series, tuple(vacancies), tuple(errors), len(candidate_ids), complete, tuple(notices))
