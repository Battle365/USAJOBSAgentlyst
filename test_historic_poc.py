from datetime import date

import pytest

from historic_poc import DiscoveryError, derive_series, discover, fetch_text, is_open, normalize_pair
from models import EvidenceUnit, ResumeRecord


DAY = date(2026, 9, 16)


def _record(**changes):
    return {
        "usajobsControlNumber": 123456789,
        "positionOpenDate": "2026-09-01",
        "positionCloseDate": "2026-09-30",
        "positionExpireDate": None,
        "positionOpeningStatus": "Accepting applications",
        "positionTitle": "Program Analyst",
        "hiringAgencyName": "Test Agency",
        "whoMayApply": "The public",
        **changes,
    }


def test_open_filter_rejects_closed_cancelled_future_and_early_expired():
    assert is_open(_record(), DAY)
    assert not is_open(_record(positionOpeningStatus="Job closed"), DAY)
    assert not is_open(_record(positionOpeningStatus="Job canceled"), DAY)
    assert not is_open(_record(positionOpeningStatus=None), DAY)
    assert not is_open(_record(positionOpenDate="2026-09-17"), DAY)
    assert not is_open(_record(positionCloseDate="2026-09-15"), DAY)
    assert not is_open(_record(positionExpireDate="2026-09-15"), DAY)


def test_series_derivation_is_bounded():
    resume = ResumeRecord("f", "o", "r", "text/plain", 5, "", "hash", "Program Analyst performing program analysis", (EvidenceUnit("Program Analyst performing program analysis", "experience"),))
    assert derive_series(resume) == ("0343",)
    unknown = ResumeRecord("f", "o", "r", "text/plain", 5, "", "hash", "General office work", ())
    assert derive_series(unknown) == ()


def test_skill_list_alone_does_not_trigger_series():
    record = ResumeRecord("f", "o", "r", "text/plain", 5, "", "hash", "Data science and program analysis", (EvidenceUnit("Data science and program analysis", "skills"),))
    assert derive_series(record) == ()


def test_explicit_series_has_priority_when_resume_mentions_three_families():
    units = (
        EvidenceUnit("Program Analyst and data scientist collaboration", "experience"),
        EvidenceUnit("GS-2210 Information Technology Specialist", "experience"),
    )
    record = ResumeRecord("f", "o", "r", "text/plain", 5, "", "hash", "\n".join(unit.text for unit in units), units)
    assert derive_series(record)[0] == "2210"
    assert len(derive_series(record)) == 2


class Response:
    def __init__(self, data):
        self.data = data
    def raise_for_status(self):
        return None
    def json(self):
        return self.data


class Client:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(next(self.pages))


def test_discovery_filters_and_tracks_pagination_completeness():
    client = Client([
        {"data": [_record(), _record(positionOpeningStatus="Job closed")], "paging": {"next": "/api/historicjoa?continuationtoken=abc"}},
        {"data": [_record(usajobsControlNumber=987654321)], "paging": {}},
    ])
    result = discover("0343", today=DAY, session=client)
    assert result.complete and result.pages == 2
    assert {row["usajobsControlNumber"] for row in result.candidates} == {123456789, 987654321}
    assert "Authorization-Key" not in client.calls[0][1].get("headers", {})


def test_discovery_marks_capped_results_partial():
    client = Client([{"data": [_record()], "paging": {"next": "/api/historicjoa?continuationtoken=abc"}}])
    result = discover("0343", today=DAY, session=client, max_pages=1)
    assert not result.complete and "partial" in result.warning


def test_announcement_text_requires_matching_identifier_and_qualification():
    client = Client([{"data": [{"usajobsControlNumber": 123456789, "requirementsQualifications": "Must have one year of program analysis."}]}])
    text = fetch_text("123456789", session=client)
    assert text["requirementsQualifications"]
    normalized = normalize_pair(_record(), text)
    assert normalized["id"] == "123456789"
    assert normalized["url"] == "https://www.usajobs.gov/job/123456789"
    with pytest.raises(DiscoveryError, match="identifiers disagree"):
        normalize_pair(_record(), {"usajobsControlNumber": 999, "requirementsQualifications": "Required"})
    with pytest.raises(DiscoveryError, match="lacks qualification"):
        normalize_pair(_record(), {"usajobsControlNumber": 123456789})


def test_unsafe_continuation_is_rejected():
    client = Client([{"data": [_record()], "paging": {"next": "https://example.com/steal"}}])
    with pytest.raises(DiscoveryError, match="unsafe"):
        discover("0343", today=DAY, session=client)
