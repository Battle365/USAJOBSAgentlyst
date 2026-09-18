"""Credential-free USAJOBS Historic JOA provider primitives.

No resume content is transmitted to USAJOBS.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urljoin, urlparse

import requests

from models import ResumeRecord
from series_inference import FALLBACK_CATALOG, infer_series


BASE = "https://data.usajobs.gov"
HISTORIC_PATH = "/api/historicjoa"
TEXT_PATH = "/api/historicjoa/announcementtext"
MAX_PAGES = 3


class DiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Discovery:
    series: tuple[str, ...]
    candidates: tuple[dict, ...]
    complete: bool
    pages: int
    warning: str = ""


def derive_series(resume: ResumeRecord) -> tuple[str, ...]:
    """Compatibility wrapper for the proof-of-concept tests."""
    return tuple(candidate.code for candidate in infer_series(resume, FALLBACK_CATALOG))


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def is_open(record: dict, today: date) -> bool:
    """Fail closed on unknown status/dates; early expiration overrides close date."""
    if str(record.get("positionOpeningStatus") or "").strip().lower() != "accepting applications":
        return False
    opened = _date(record.get("positionOpenDate"))
    closed = _date(record.get("positionCloseDate"))
    expired = _date(record.get("positionExpireDate")) if record.get("positionExpireDate") else None
    return bool(opened and closed and opened <= today <= closed and (expired is None or today <= expired))


def _get_json(session, path: str, params: dict) -> dict:
    response = session.get(urljoin(BASE, path), params=params, timeout=20, headers={"Accept": "application/json"})
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise DiscoveryError("USAJOBS returned an unexpected Historic JOA response.")
    return body


def _next_path(body: dict, expected_path: str) -> tuple[str, dict] | None:
    next_ref = (body.get("paging") or {}).get("next")
    if not next_ref:
        return None
    parsed = urlparse(urljoin(BASE, str(next_ref)))
    if parsed.scheme != "https" or parsed.hostname != "data.usajobs.gov" or parsed.path.lower() != expected_path.lower():
        raise DiscoveryError("USAJOBS returned an unsafe continuation URL.")
    return parsed.path + ("?" + parsed.query if parsed.query else ""), {}


def discover(series: str, *, today: date | None = None, session=None, max_pages: int = MAX_PAGES) -> Discovery:
    if not re.fullmatch(r"\d{4}", series):
        raise ValueError("Occupational series must be four digits.")
    day = today or datetime.now(UTC).date()
    client = session or requests.Session()
    path = HISTORIC_PATH
    params = {"PositionSeries": series, "StartPositionCloseDate": day.isoformat(), "EndPositionOpenDate": day.isoformat()}
    candidates: dict[str, dict] = {}
    complete = False
    pages = 0
    while pages < max_pages:
        body = _get_json(client, path, params)
        pages += 1
        for record in body["data"]:
            if not isinstance(record, dict) or not is_open(record, day):
                continue
            control = str(record.get("usajobsControlNumber") or "")
            if control.isdigit():
                candidates[control] = record
        next_page = _next_path(body, HISTORIC_PATH)
        if next_page is None:
            complete = True
            break
        path, params = next_page
    warning = "" if complete else "Historic JOA retrieval was capped; these are partial discovery results."
    return Discovery((series,), tuple(candidates.values()), complete, pages, warning)


def fetch_text(control_number: str, *, session=None) -> dict:
    if not control_number.isdigit():
        raise ValueError("Control number must be numeric.")
    client = session or requests.Session()
    body = _get_json(client, TEXT_PATH, {"USAJOBSControlNumbers": control_number})
    matched = [row for row in body["data"] if isinstance(row, dict) and str(row.get("usajobsControlNumber")) == control_number]
    if len(matched) != 1:
        raise DiscoveryError("Announcement Text did not return exactly one matching control number.")
    return matched[0]


def normalize_pair(summary: dict, full_text: dict) -> dict:
    control = str(summary.get("usajobsControlNumber") or "")
    if not control.isdigit() or str(full_text.get("usajobsControlNumber")) != control:
        raise DiscoveryError("Historic JOA and Announcement Text identifiers disagree.")
    qualifications = str(full_text.get("requirementsQualifications") or "").strip()
    if not qualifications:
        raise DiscoveryError("Announcement Text lacks qualification requirements.")
    location_parts = []
    for item in summary.get("positionlocations") or []:
        if isinstance(item, dict):
            location_parts.append(", ".join(str(item.get(key) or "").strip() for key in ("positionLocationCity", "positionLocationState") if item.get(key)))
    payload_hash = hashlib.sha256(json.dumps({"summary": summary, "text": full_text}, sort_keys=True, default=str).encode()).hexdigest()
    series = sorted({str(item.get("series")) for item in summary.get("jobcategories") or [] if isinstance(item, dict) and item.get("series")})
    minimum_grade = str(summary.get("minimumGrade") or "").strip()
    maximum_grade = str(summary.get("maximumGrade") or "").strip()
    pay_scale = str(summary.get("payScale") or "").strip()
    grades: list[str] = []
    grade_note = ""
    if pay_scale == "GS" and minimum_grade.isdigit():
        low = int(minimum_grade)
        high = int(maximum_grade) if maximum_grade.isdigit() else low
        grades = [f"GS-{grade:02d}" for grade in range(max(11, low), min(15, high) + 1)]
        if low < 11 <= high:
            grade_note = "Only the GS-11+ portion of this multi-grade announcement is considered."
    elif pay_scale:
        grades = [f"{pay_scale}-{minimum_grade}"] if minimum_grade else [pay_scale]
        if maximum_grade and maximum_grade != minimum_grade:
            grades.append(f"{pay_scale}-{maximum_grade}")
        grade_note = "Non-GS pay plan; GS-11 equivalence has not been established."
    return {
        "id": control,
        "source_hash": payload_hash,
        "url": f"https://www.usajobs.gov/job/{control}",
        "title": str(summary.get("positionTitle") or "Untitled position"),
        "agency": str(summary.get("hiringAgencyName") or summary.get("hiringDepartmentName") or "Federal agency"),
        "location": "; ".join(location_parts) or "Location not listed",
        "remote": False,  # Historic JOA telework flag does not establish remote status.
        "open_date": str(summary.get("positionOpenDate") or ""),
        "close_date": str(summary.get("positionCloseDate") or ""),
        "series": ", ".join(series),
        "grades": grades,
        "grade_note": grade_note,
        "eligibility": str(summary.get("whoMayApply") or ""),
        "eligibility_source": "historic_joa",
        "hiring_paths": tuple(
            str(item.get("hiringPath") or "").strip()
            for item in (summary.get("hiringpaths") or [])
            if isinstance(item, dict) and str(item.get("hiringPath") or "").strip()
        ),
        "qualifications": qualifications,
        "specialized_experience": "",  # Included in qualification prose when present.
        "education": str(full_text.get("requirementsEducation") or ""),
        "conditions": str(full_text.get("requirementsConditionsOfEmployment") or ""),
        "duties": str(full_text.get("duties") or full_text.get("majorDutiesList") or ""),
        "summary": str(full_text.get("summary") or ""),
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
