"""Side-by-side tests; the active V4 selector is intentionally unchanged."""

import json
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path

from historic_poc import is_open, normalize_pair
from job_discovery import _balanced_candidates, grade_eligible
from matcher import evaluate_vacancy
from models import EvidenceUnit, ResumeRecord
from pre_ranking import _duty_fit, _public_path, select_for_text_experimental
from profile_extraction import DiscoveryConstraints, ResumeProfile, SupportedSeries


FIXTURE = Path(__file__).parent / "test_fixtures" / "historic_joa_selection.json"
HIRING_PATH_FIXTURE = Path(__file__).parent / "test_fixtures" / "historic_joa_hiring_paths.json"


def _fixture():
    frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))
    today = date.fromisoformat(frozen["as_of"])
    rows = []
    for values in frozen["rows"]:
        control, code, title, agency, city, state, opened = values[:7]
        path = values[7] if len(values) > 7 else "Federal employees"
        pay = values[8] if len(values) > 8 else "GS"
        grade = values[9] if len(values) > 9 else "11"
        status = values[10] if len(values) > 10 else "Accepting applications"
        expires = values[11] if len(values) > 11 else None
        rows.append({
            "usajobsControlNumber": control,
            "jobcategories": [{"series": code}],
            "positionTitle": title,
            "hiringAgencyName": agency,
            "positionlocations": [{"positionLocationCity": city, "positionLocationState": state}],
            "positionOpenDate": opened,
            "positionCloseDate": frozen["close_date"],
            "positionExpireDate": expires,
            "positionOpeningStatus": status,
            "payScale": pay,
            "minimumGrade": grade,
            "maximumGrade": grade,
            "whoMayApply": path,
            "hiringpaths": [{"hiringPath": path}],
        })
    return today, rows


def _profile():
    return ResumeProfile("fixture-resume", (
        SupportedSeries("0610", "Nurse", 100, ("explicit occupational series 0610", "documented role: registered nurse"), ("patient assessment", "nursing care"), (EvidenceUnit("Registered Nurse providing nursing care.", "experience", 1),)),
        SupportedSeries("0510", "Accounting", 80, ("documented role: accountant",), ("financial statements", "general ledger"), (EvidenceUnit("Accountant preparing financial statements.", "experience", 2),)),
        SupportedSeries("2210", "Information Technology Management", 75, ("performed-work signals: network security, systems administration", "documented role: it specialist"), ("network security", "systems administration"), (EvidenceUnit("IT Specialist managing network security.", "experience", 3),)),
    ), DiscoveryConstraints(), True)


def _by_series(rows, today, *, eligible_only=True):
    grouped = {code: [] for code in ("0610", "0510", "2210")}
    for row in rows:
        code = row["jobcategories"][0]["series"]
        if not eligible_only or (is_open(row, today) and grade_eligible(row)):
            grouped[code].append(row)
    return grouped


def _ids(rows):
    return [str(row["usajobsControlNumber"]) for row in rows]


def test_frozen_fixture_compares_exact_18_and_series_distribution():
    today, rows = _fixture()
    grouped = _by_series(rows, today)
    current = _balanced_candidates(grouped, tuple(item.code for item in _profile().series_candidates))
    experimental = select_for_text_experimental(_profile(), grouped, today=today)
    current_ids = _ids(current)
    experimental_ids = _ids(item.summary for item in experimental)
    assert current_ids == [
        "610001", "510001", "2210001", "610002", "510002", "2210002",
        "610003", "510003", "2210003", "610004", "510004", "2210004",
        "610005", "510005", "2210005", "610006", "510006", "2210006",
    ]
    assert experimental_ids == [
        "610007", "510007", "2210007", "610011", "610008", "610009",
        "610001", "610002", "610003", "510008", "510001", "510002",
        "510003", "510004", "510005", "2210009", "2210008", "2210001",
    ]
    assert len(set(current_ids)) == len(set(experimental_ids)) == 18
    assert len(set(current_ids) & set(experimental_ids)) == 9
    assert Counter(row["jobcategories"][0]["series"] for row in current) == {"0610": 6, "0510": 6, "2210": 6}
    assert Counter(item.anchor_series for item in experimental) == {"0610": 7, "0510": 7, "2210": 4}
    assert all(item.reasons and item.series_confidence > 0 for item in experimental)


def test_experimental_selector_is_deterministic_and_keeps_eligibility_gates():
    today, rows = _fixture()
    grouped = _by_series(rows, today, eligible_only=False)
    forward = select_for_text_experimental(_profile(), grouped, today=today)
    reversed_grouped = {code: list(reversed(values)) for code, values in reversed(list(grouped.items()))}
    backward = select_for_text_experimental(_profile(), reversed_grouped, today=today)
    assert _ids(item.summary for item in forward) == _ids(item.summary for item in backward)
    assert len(forward) <= 18
    assert not {"610010", "510010", "2210010"} & set(_ids(item.summary for item in forward))
    assert {"0610", "0510", "2210"} <= {item.anchor_series for item in forward}


def test_missing_metadata_and_telework_flag_do_not_disqualify_or_imply_remote():
    today, rows = _fixture()
    row = next(row for row in rows if row["usajobsControlNumber"] == 610001).copy()
    row["positionTitle"] = ""
    row["hiringAgencyName"] = ""
    row["positionlocations"] = []
    row["whoMayApply"] = ""
    row["hiringpaths"] = []
    selected = select_for_text_experimental(_profile(), {"0610": [row]}, today=today)
    row_with_telework = {**row, "teleworkEligible": "Y"}
    selected_telework = select_for_text_experimental(_profile(), {"0610": [row_with_telework]}, today=today)
    assert _ids(item.summary for item in selected) == _ids(item.summary for item in selected_telework) == ["610001"]
    assert selected[0].title_tier == 0 and not selected[0].public_path


def test_live_hiringpaths_shape_and_null_metadata_are_handled_neutrally():
    frozen = json.loads(HIRING_PATH_FIXTURE.read_text(encoding="utf-8"))
    for case in frozen["cases"]:
        assert _public_path(case["summary"]) is case["public"], case["name"]


def test_live_lowercase_public_path_adds_only_a_retrieval_reason():
    today, rows = _fixture()
    row = next(row for row in rows if row["usajobsControlNumber"] == 610007).copy()
    row["whoMayApply"] = None
    row["hiringpaths"] = [{"hiringPath": "Federal employees - Competitive service"}, {"hiringPath": "The public"}]
    selected = select_for_text_experimental(_profile(), {"0610": [row]}, today=today)
    assert len(selected) == 1
    assert selected[0].public_path
    assert "public hiring path listed" in selected[0].reasons


def test_non_gs_is_not_excluded_and_text_requests_never_exceed_budget():
    today, rows = _fixture()
    grouped = _by_series(rows, today)
    assert any(row["usajobsControlNumber"] == 610011 for row in grouped["0610"])
    selected = select_for_text_experimental(_profile(), grouped, today=today)
    class TextProvider:
        def __init__(self):
            self.requested = []

        def announcement(self, control):
            self.requested.append(control)
            return {"usajobsControlNumber": control}

    provider = TextProvider()
    for item in selected:
        provider.announcement(item.summary["usajobsControlNumber"])
    assert len(provider.requested) == len(set(provider.requested)) <= 18
    assert any(item.summary["usajobsControlNumber"] == 610011 for item in selected)


def test_duplicate_control_across_series_is_selected_once():
    today, rows = _fixture()
    grouped = _by_series(rows, today)
    duplicate = next(row for row in rows if row["usajobsControlNumber"] == 610007)
    grouped["0510"].append(duplicate)
    selected = select_for_text_experimental(_profile(), grouped, today=today)
    assert _ids(item.summary for item in selected).count("610007") == 1
    assert len(selected) == len({_id for _id in _ids(item.summary for item in selected)}) == 18


def test_priority_does_not_enter_qualification_decision():
    today, rows = _fixture()
    grouped = _by_series(rows, today)
    current = _balanced_candidates(grouped, tuple(item.code for item in _profile().series_candidates))
    experimental = select_for_text_experimental(_profile(), grouped, today=today)
    common = set(_ids(current)) & set(_ids(item.summary for item in experimental))
    resume = ResumeRecord("f", "o", "synthetic", "text/plain", 1, "", "hash", "Performed nursing care and patient assessment.", (EvidenceUnit("Performed nursing care and patient assessment.", "experience", 1, duration_months=24),))

    def outcomes(summaries):
        result = {}
        for row in summaries:
            requirement = "nursing care and patient assessment" if row["usajobsControlNumber"] % 2 else "budget formulation and financial analysis"
            text = {"usajobsControlNumber": row["usajobsControlNumber"], "requirementsQualifications": f"You must have one year of specialized experience performing {requirement}."}
            vacancy = normalize_pair({**row, "whoMayApply": "The public", "hiringpaths": [{"hiringPath": "The public"}]}, text)
            result[str(row["usajobsControlNumber"])] = evaluate_vacancy(resume, vacancy).final_outcome
        return result

    current_outcomes = outcomes(current)
    experimental_outcomes = outcomes(item.summary for item in experimental)
    assert common
    assert {control: current_outcomes[control] for control in common} == {control: experimental_outcomes[control] for control in common}
    assert set(current_outcomes.values()) == {"MATCH", "NOT A MATCH"}


def _single_series_profile(code):
    candidate = next((item for item in _profile().series_candidates if item.code == code), None)
    if code == "0560":
        candidate = SupportedSeries("0560", "Budget Analysis", 80,
                                    ("documented role: budget analyst",),
                                    ("budget formulation", "budget execution"),
                                    (EvidenceUnit("Performed budget formulation and budget execution.", "experience", 1),))
    assert candidate is not None
    return replace(_profile(), series_candidates=(candidate,),
                   constraints=DiscoveryConstraints(maximum_announcement_texts=1))


def _pick(code, titles):
    today, rows = _fixture()
    seed = next((row for row in rows if row["jobcategories"][0]["series"] == code), rows[0])
    options = [{**seed, "usajobsControlNumber": 900000 + index,
                "jobcategories": [{"series": code}],
                "positionTitle": title, "positionOpenDate": opened,
                "hiringAgencyName": agency, "hiringpaths": [{"hiringPath": "The public"}]}
               for index, (title, opened, agency) in enumerate(titles, 1)]
    return select_for_text_experimental(_single_series_profile(code), {code: options}, today=today)[0]


def test_general_nursing_duties_outrank_nurse_practitioner_title():
    selected = _pick("0610", [
        ("Advanced Practice Nurse Practitioner", "2026-09-16", "Agency A"),
        ("Registered Nurse (Patient Assessment)", "2026-09-15", "Agency A"),
    ])
    assert selected.summary["usajobsControlNumber"] == 900002
    assert selected.duty_tier > 0
    assert not selected.scope_flags


def test_generic_patient_word_is_not_a_performed_duty_match():
    series = next(item for item in _profile().series_candidates if item.code == "0610")
    assert _duty_fit({"positionTitle": "Registered Nurse - Patient Safety Manager"}, series)[0] == 0
    assert _duty_fit({"positionTitle": "Registered Nurse - Patient Assessment"}, series)[0] == 2


def test_hands_on_it_duties_outrank_cio_and_supervisory_titles():
    selected = _pick("2210", [
        ("Chief Information Officer (Director, Information Technology Management)", "2026-09-16", "Agency A"),
        ("Supervisory Information Technology Specialist", "2026-09-16", "Agency B"),
        ("Information Technology Specialist (Network Security)", "2026-09-15", "Agency A"),
    ])
    assert selected.summary["usajobsControlNumber"] == 900003
    assert selected.duty_tier == 2


def test_ordinary_budget_role_outranks_title32_and_billet_review_signals():
    selected = _pick("0560", [
        ("Budget Analyst (Title 32)", "2026-09-16", "Agency A"),
        ("Chief Budget Analyst (O-5 Billet)", "2026-09-16", "Agency B"),
        ("Budget Analyst", "2026-09-15", "Agency A"),
    ])
    assert selected.summary["usajobsControlNumber"] == 900003
    assert not selected.review_flags


def test_non_gs_pay_plan_is_flagged_but_not_ranked_below_older_gs_twin():
    today, rows = _fixture()
    seed = rows[0]
    newer = {**seed, "usajobsControlNumber": 900021, "positionTitle": "Registered Nurse",
             "positionOpenDate": "2026-09-16", "payScale": "VN"}
    older = {**seed, "usajobsControlNumber": 900022, "positionTitle": "Registered Nurse",
             "positionOpenDate": "2026-09-15", "payScale": "GS"}
    chosen = select_for_text_experimental(_single_series_profile("0610"), {"0610": [older, newer]}, today=today)
    assert chosen[0].summary["usajobsControlNumber"] == 900021
    assert chosen[0].review_flags


def test_structured_hiringpaths_override_conflicting_who_may_apply_for_priority():
    assert not _public_path({"whoMayApply": "The public", "hiringpaths": [{"hiringPath": "Federal employees - Competitive service"}]})
    assert _public_path({"whoMayApply": "Federal employees", "hiringpaths": [{"hiringPath": "The public"}]})
    assert not _public_path({"whoMayApply": None, "hiringpaths": None})


def test_relevance_then_recency_outrank_agency_location_novelty():
    selected = _pick("2210", [
        ("Information Technology Specialist (Network Security)", "2026-09-15", "Repeated Agency"),
        ("Chief Information Officer", "2026-09-16", "Novel Agency"),
    ])
    assert selected.summary["usajobsControlNumber"] == 900001
    today, rows = _fixture()
    seed = next(row for row in rows if row["jobcategories"][0]["series"] == "2210")
    profile = replace(_single_series_profile("2210"), constraints=DiscoveryConstraints(maximum_announcement_texts=2))
    first = {**seed, "usajobsControlNumber": 900010, "positionTitle": "Information Technology Specialist (Network Security)", "positionOpenDate": "2026-09-16", "hiringAgencyName": "Repeated Agency"}
    second = {**first, "usajobsControlNumber": 900011, "positionOpenDate": "2026-09-15"}
    novel = {**first, "usajobsControlNumber": 900012, "positionOpenDate": "2026-09-14", "hiringAgencyName": "Novel Agency"}
    chosen = select_for_text_experimental(profile, {"2210": [first, second, novel]}, today=today)
    assert _ids(item.summary for item in chosen) == ["900010", "900011"]
