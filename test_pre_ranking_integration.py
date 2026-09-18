"""Acceptance checks for retrieval-only activation of the reviewed selector."""

from datetime import UTC, datetime, timedelta

import job_discovery
from acquisition import build_manual_vacancy
from historic_poc import Discovery, normalize_pair
from job_discovery import _balanced_candidates, discover_for_profile
from matcher import evaluate_vacancy
from models import EvidenceUnit, ResumeRecord
from pre_ranking import select_for_text_experimental
from profile_extraction import DiscoveryConstraints, ResumeProfile, SupportedSeries


def _resume():
    unit = EvidenceUnit("Performed network security and systems administration.", "experience", 1, duration_months=24)
    return ResumeRecord("synthetic", "owner", "resume.txt", "text/plain", 100, "", "synthetic-hash", unit.text, (unit,))


def _profile(duty_signals=("network security",), limit=18):
    series = SupportedSeries("2210", "Information Technology Management", 80,
                             ("documented role: information technology specialist",),
                             duty_signals, _resume().evidence)
    return ResumeProfile("synthetic-hash", (series,), DiscoveryConstraints(maximum_announcement_texts=limit), True)


def _row(control, title, *, opened_days_ago=1):
    today = datetime.now(UTC).date()
    return {
        "usajobsControlNumber": control, "jobcategories": [{"series": "2210"}],
        "positionTitle": title, "hiringAgencyName": "Test Agency",
        "positionOpenDate": (today - timedelta(days=opened_days_ago)).isoformat(),
        "positionCloseDate": (today + timedelta(days=30)).isoformat(),
        "positionOpeningStatus": "Accepting applications", "payScale": "GS",
        "minimumGrade": "11", "maximumGrade": "11", "whoMayApply": None,
        "hiringpaths": [{"hiringPath": "The public"}],
    }


class Provider:
    def __init__(self, rows):
        self.rows = rows
        self.requested = []

    def discover(self, series):
        assert series == "2210"
        return Discovery((series,), tuple(self.rows), True, 1)

    def announcement(self, control_number):
        self.requested.append(control_number)
        return {"usajobsControlNumber": int(control_number),
                "requirementsQualifications": "You must have one year of specialized experience performing network security and systems administration."}

    def normalize(self, summary, announcement):
        return normalize_pair(summary, announcement)


def test_live_discovery_uses_pre_ranked_ids_and_never_exceeds_18_distinct_texts():
    rows = [_row(910000 + index, "Office Specialist", opened_days_ago=1) for index in range(25)]
    rows.append(_row(919999, "Information Technology Specialist (Network Security)", opened_days_ago=10))
    rows.append(dict(rows[0]))  # Duplicate control numbers do not consume text requests.
    provider = Provider(rows)
    profile = _profile()
    expected = [str(item.summary["usajobsControlNumber"])
                for item in select_for_text_experimental(profile, {"2210": rows}, today=datetime.now(UTC).date())]
    balanced = [str(row["usajobsControlNumber"]) for row in _balanced_candidates({"2210": rows}, ("2210",))]
    result = discover_for_profile(profile, provider)
    assert provider.requested == expected
    assert provider.requested != balanced
    assert provider.requested[0] == "919999"
    assert len(provider.requested) == len(set(provider.requested)) == 18
    assert len(result.vacancies) == 18
    assert result.open_candidate_count == 26


def test_profile_signals_change_retrieval_not_matcher_decision_for_same_vacancy():
    rows = [_row(920001, "Information Technology Specialist (Network Security)"),
            _row(920002, "Information Technology Specialist (Systems Administration)")]
    network_provider, systems_provider = Provider(rows), Provider(rows)
    network = discover_for_profile(_profile(("network security",), 1), network_provider)
    systems = discover_for_profile(_profile(("systems administration",), 1), systems_provider)
    assert network_provider.requested != systems_provider.requested
    assert len(network.vacancies) == len(systems.vacancies) == 1
    fixed_vacancy = normalize_pair(rows[0], network_provider.announcement("920001"))
    first = evaluate_vacancy(_resume(), fixed_vacancy)
    second = evaluate_vacancy(_resume(), fixed_vacancy)
    assert first.final_outcome == second.final_outcome == "MATCH"
    assert first.paths == second.paths
    assert first.eligibility_source_paths == second.eligibility_source_paths == ("The public",)


def test_manual_announcement_does_not_call_discovery_or_pre_ranker(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("manual matching must bypass discovery")

    monkeypatch.setattr(job_discovery, "select_for_text_experimental", forbidden)
    monkeypatch.setattr(job_discovery, "discover_for_profile", forbidden)
    announcement = ("Qualifications\nYou must have one year of specialized experience performing "
                    "network security and systems administration.\n") * 4
    vacancy = build_manual_vacancy("IT Specialist", "Test Agency", announcement)
    assert evaluate_vacancy(_resume(), vacancy).final_outcome == "MATCH"
