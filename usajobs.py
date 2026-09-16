from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlparse

import requests


API_URL = "https://data.usajobs.gov/api/search"
RESULTS_PER_PAGE = 100
MAX_PAGES = 25


class USAJobsError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, partial: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.partial = partial


@dataclass(frozen=True)
class SearchFilters:
    keywords: str = ""
    location: str = ""
    remote_only: bool = False
    agency: str = ""
    pay_grade_low: int | None = None
    job_series: str = ""


def official_url(descriptor: dict[str, Any]) -> str:
    candidate = str(descriptor.get("PositionURI", "")).strip()
    parsed = urlparse(candidate)
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname.lower() in {"www.usajobs.gov", "usajobs.gov"} and parsed.path:
        return candidate
    position_id = str(descriptor.get("PositionID", "")).strip()
    return f"https://www.usajobs.gov/job/{position_id}" if position_id.isdigit() else ""


def _text(container: dict[str, Any], *keys: str) -> str:
    values: list[str] = []
    for key in keys:
        value = container.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    values.extend(str(v) for v in item.values() if v)
                elif item:
                    values.append(str(item))
        elif isinstance(value, dict):
            values.extend(str(v) for v in value.values() if v)
        elif value:
            values.append(str(value))
    return "\n".join(values)


def normalize_vacancy(item: dict[str, Any], retrieved_at: str | None = None) -> dict[str, Any]:
    descriptor = item.get("MatchedObjectDescriptor", item)
    details = descriptor.get("UserArea", {}).get("Details", {}) or {}
    position_id = str(descriptor.get("PositionID") or descriptor.get("PositionURI") or "").strip()
    control_number = str(details.get("JobAnnouncementNumber") or descriptor.get("PositionID") or position_id).strip()
    locations = descriptor.get("PositionLocation") or []
    location_names = [str(location.get("LocationName", "")).strip() for location in locations if isinstance(location, dict)]
    display_location = str(descriptor.get("PositionLocationDisplay") or ", ".join(filter(None, location_names)) or "Location not listed")
    remote_text = _text(details, "RemoteIndicator", "TeleworkEligible", "RemoteJob")
    remote = "remote" in f"{display_location} {remote_text}".lower() and "not remote" not in f"{display_location} {remote_text}".lower()
    grades = [str(value.get("Code", "")) for value in descriptor.get("JobGrade", []) if isinstance(value, dict) and value.get("Code")]
    qualifications = _text(descriptor, "QualificationSummary", "Requirements", "SpecializedExperience") or _text(details, "Qualifications", "Requirements")
    specialized = _text(descriptor, "SpecializedExperience") or _text(details, "SpecializedExperience")
    duties = _text(details, "MajorDuties", "Duties", "Responsibilities")
    education = _text(details, "Education", "EducationRequirements")
    conditions = _text(details, "ConditionsOfEmployment", "Requirements")
    eligibility = _text(descriptor, "WhoMayApply") or _text(details, "WhoMayApply", "HiringPath")
    competencies = _text(details, "Evaluations", "Competencies", "AssessmentQuestion")
    payload = json.dumps(descriptor, sort_keys=True, separators=(",", ":"), default=str)
    return {
        "id": control_number or position_id,
        "control_number": control_number,
        "position_id": position_id,
        "url": official_url(descriptor),
        "title": str(descriptor.get("PositionTitle") or "Untitled position"),
        "agency": str(descriptor.get("OrganizationName") or descriptor.get("DepartmentName") or "Federal agency"),
        "organization": str(descriptor.get("DepartmentName") or descriptor.get("OrganizationName") or "Federal agency"),
        "locations": location_names or [display_location],
        "location": display_location,
        "remote": remote,
        "open_date": str(descriptor.get("PublicationStartDate") or ""),
        "close_date": str(descriptor.get("ApplicationCloseDate") or ""),
        "series": _text(descriptor, "JobCategory"),
        "grades": grades,
        "pay_range": _text(descriptor, "PositionRemuneration"),
        "appointment_type": _text(descriptor, "PositionOfferingType"),
        "schedule": _text(descriptor, "PositionSchedule"),
        "promotion_potential": _text(details, "PromotionPotential"),
        "eligibility": eligibility,
        "qualifications": qualifications,
        "specialized_experience": specialized,
        "duties": duties,
        "competencies": competencies,
        "education": education,
        "conditions": conditions,
        "summary": str(details.get("JobSummary") or ""),
        "retrieved_at": retrieved_at or datetime.now(UTC).isoformat(),
        "source_hash": hashlib.sha256(payload.encode()).hexdigest(),
    }


class USAJobsClient:
    def __init__(self, api_key: str, email: str, request_get: Callable[..., Any] = requests.get):
        if not api_key or not email:
            raise USAJobsError("USAJOBS search is not configured. Contact the site operator.")
        self._headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": api_key}
        self._get = request_get

    def search(self, filters: SearchFilters) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"ResultsPerPage": RESULTS_PER_PAGE, "Fields": "Full"}
        if filters.keywords.strip():
            params["Keyword"] = filters.keywords.strip()
        if filters.location.strip():
            params["LocationName"] = filters.location.strip()
        if filters.agency.strip():
            params["Organization"] = filters.agency.strip()
        if filters.pay_grade_low:
            params["PayGradeLow"] = filters.pay_grade_low
        if filters.job_series.strip():
            params["JobCategoryCode"] = filters.job_series.strip()
        if filters.remote_only:
            params["RemoteIndicator"] = "True"

        items: dict[str, dict[str, Any]] = {}
        page = 1
        while page <= MAX_PAGES:
            try:
                response = self._get(API_URL, headers=self._headers, params={**params, "Page": page}, timeout=20)
                status = getattr(response, "status_code", 200)
                if status in (401, 403):
                    raise USAJobsError("USAJOBS search is unavailable because server credentials are invalid.")
                if status == 429:
                    raise USAJobsError("USAJOBS is temporarily rate-limiting searches. Please retry shortly.", retryable=True, partial=bool(items))
                response.raise_for_status()
                body = response.json()
            except USAJobsError:
                raise
            except requests.Timeout as exc:
                raise USAJobsError("USAJOBS timed out. Please retry.", retryable=True, partial=bool(items)) from exc
            except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                raise USAJobsError("USAJOBS could not be reached or returned an invalid response.", retryable=True, partial=bool(items)) from exc
            result = body.get("SearchResult") or {}
            page_items = result.get("SearchResultItems") or []
            if not isinstance(page_items, list):
                raise USAJobsError("USAJOBS returned an invalid response.", retryable=True, partial=bool(items))
            for raw in page_items:
                vacancy = normalize_vacancy(raw)
                if vacancy["id"]:
                    items[vacancy["id"]] = vacancy
            count = int(result.get("SearchResultCount") or len(page_items))
            total = int(result.get("SearchResultCountAll") or count)
            if not page_items or page * RESULTS_PER_PAGE >= total:
                return list(items.values())
            page += 1
        raise USAJobsError("USAJOBS returned more pages than this search can safely retrieve. Narrow the filters.", partial=True)
