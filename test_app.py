import pytest

from app import PROFILE_SECTIONS, RESUME_PROFILE, Preferences, announcement_url, application_url, normalize_job, parse_profile_entries, prepare_application, review_jobs, search_jobs, submit_application


def make_job(title: str, summary: str = "") -> dict:
    return normalize_job({
        "MatchedObjectDescriptor": {
            "PositionTitle": title,
            "PositionURI": f"https://www.usajobs.gov/job/{title.replace(' ', '-')}",
            "UserArea": {"Details": {"JobSummary": summary}},
        }
    })


def test_announcement_url_uses_api_uri_or_position_id_fallback():
    api_uri = "https://www.usajobs.gov:443/GetJob/ViewDetails/123456789"
    assert announcement_url({"PositionURI": api_uri}) == api_uri
    assert announcement_url({"PositionID": "123456789"}) == "https://www.usajobs.gov/job/123456789"
    assert announcement_url({"PositionURI": "https://data.usajobs.gov/api/search"}) == ""
    assert announcement_url({"PositionURI": "https://www.usajobs.gov/job/000000001"}) == "https://www.usajobs.gov/job/000000001"


def test_application_url_targets_apply_section_of_job_announcement():
    assert application_url({"url": "https://www.usajobs.gov/job/123456789"}) == "https://www.usajobs.gov/job/123456789#apply"
    search_url = "https://www.usajobs.gov/Search/Results?k=Program%20Analyst"
    assert application_url({"url": search_url}) == search_url


def test_demo_jobs_use_valid_public_usajobs_search_links():
    from app import DEMO_JOBS

    assert all(normalize_job(job)["url"].startswith("https://www.usajobs.gov/Search/Results?") for job in DEMO_JOBS)
    assert all(normalize_job(job)["salary"] != "Not listed" for job in DEMO_JOBS)


def test_live_api_position_uri_reaches_job_result():
    api_uri = "https://www.usajobs.gov:443/GetJob/ViewDetails/987654321"
    response_payload = {
        "SearchResult": {
            "SearchResultItems": [{
                "MatchedObjectDescriptor": {
                    "PositionTitle": "Program Analyst",
                    "PositionURI": api_uri,
                    "PositionLocationDisplay": "Biloxi, MS",
                    "JobGrade": [{"Code": "GS-9"}],
                }
            }]
        }
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return response_payload

    def request_get(*args, **kwargs):
        return Response()

    jobs, demo_mode = search_jobs(Preferences(5, [], False, []), "configured-key", "configured-email", request_get)
    assert demo_mode is False
    assert jobs[0]["url"] == api_uri


def test_usajobs_401_raises_clear_auth_error():
    class Response:
        status_code = 401

        def raise_for_status(self):
            raise Exception("401 Client Error: Unauthorized")

        def json(self):
            return {}

    def request_get(*args, **kwargs):
        return Response()

    with pytest.raises(ValueError, match="USAJOBS API key|registered email"):
        search_jobs(Preferences(5, [], False, []), "bad-key", "user@example.com", request_get)


def test_family_relevance_tiers_prioritize_title_relevance():
    jobs = [
        make_job("Program Analyst"),
        make_job("Senior Program Analyst"),
        make_job("Operations Specialist", "Leads information technology modernization."),
        make_job("Administrative Specialist", "Provides general office support."),
    ]
    reviewed = review_jobs(jobs, Preferences(5, [], False, []))
    scores_by_title = {job["title"]: job["score"] for job in reviewed}
    assert scores_by_title["Program Analyst"] == 40
    assert scores_by_title["Senior Program Analyst"] == 30
    assert scores_by_title["Operations Specialist"] == 18
    assert scores_by_title["Administrative Specialist"] == 8


def test_local_gulf_coast_job_outranks_higher_scoring_distant_job():
    local = make_job("Program Analyst")
    local["location"] = "Biloxi, Mississippi"
    distant = make_job("Program Analyst", "program analysis data AI Python dashboards")
    distant["location"] = "Harrisburg, Pennsylvania"
    reviewed = review_jobs([distant, local], Preferences(5, [], False, ["program analysis", "data", "AI", "Python", "dashboards"]))
    assert reviewed[0]["title"] == "Program Analyst"
    assert reviewed[0]["location_rank"] == 2
    assert reviewed[1]["location_rank"] == 0


def test_all_gulf_coast_targets_are_local():
    locations = [
        "Biloxi, MS",
        "Gulfport, Mississippi",
        "D'Iberville, MS",
        "Ocean Springs, MS",
        "Pascagoula, MS",
        "Keesler AFB, MS",
    ]
    for location in locations:
        job = make_job("Program Analyst")
        job["location"] = location
        reviewed = review_jobs([job], Preferences(5, [], False, []))
        assert reviewed[0]["location_rank"] == 2


def test_application_preparation_stops_before_existing_approval():
    job = make_job("Program Analyst", "Program analysis and stakeholder management.")
    packet = prepare_application(job, ["program analysis", "Python"])
    assert packet["job_id"] == job["id"]
    assert packet["status"] == "prepared for review"
    assert packet["profile_comparison"]["matched_skills_or_experience"] == ["program analysis"]
    assert packet["profile_comparison"]["not_found_in_announcement"] == ["Python"]
    assert packet["draft_responses"][0]["draft"].startswith("Draft prompt only")
    assert packet["required_documents"]
    assert "Request approval before any submission action" in packet["steps"]
    assert "No application submitted" in packet["actions_not_performed"]
    assert "No documents uploaded, altered, or modified" in packet["actions_not_performed"]
    assert "No USAJOBS account login or account changes" in packet["actions_not_performed"]


def test_submit_application_marks_manual_submission_stage():
    job = make_job("Program Analyst", "Program analysis and stakeholder management.")
    packet = prepare_application(job, ["program analysis"])
    submitted = submit_application(job, packet)
    assert submitted["status"] == "submitted for final review"
    assert submitted["submission_status"]["manual_approval_required"] is True
    assert "No external application request was sent" in submitted["submission_status"]["notes"]


def test_structured_profile_entries_feed_existing_match_scoring():
    profile = {
        "programming_languages": parse_profile_entries("Python, SQL\nPowerShell"),
        "education": ["Public Administration"],
    }
    keywords = [entry for entries in profile.values() for entry in entries]
    job = make_job("Data Analyst", "Uses Python and SQL for reporting.")
    reviewed = review_jobs([job], Preferences(5, [], False, keywords, profile))
    assert reviewed[0]["score"] == 54
    packet = prepare_application(job, keywords, profile)
    assert packet["profile_by_category"] == profile
    assert packet["profile_comparison"]["matched_skills_or_experience"] == ["Python", "SQL"]


def test_resume_profile_contains_all_editable_sections_and_transferable_evidence():
    assert {key for key, _, _ in PROFILE_SECTIONS} == set(RESUME_PROFILE)
    job = normalize_job({
        "MatchedObjectDescriptor": {
            "PositionTitle": "Operations Analyst",
            "PositionURI": "https://www.usajobs.gov/job/evidence-test",
            "UserArea": {"Details": {"JobSummary": "Supports quality assurance, dashboards, and stakeholder training.", "MajorDuties": "Performs data validation and stakeholder training."}},
        }
    })
    profile = {
        "technical_skills": ["data validation", "training"],
        "data_bi_skills": ["data analysis"],
    }
    reviewed = review_jobs([job], Preferences(5, [], False, ["data validation"], profile))
    analysis = reviewed[0]["match_analysis"]
    assert analysis["direct_specialized_matches"]
    assert analysis["qualification_status"] == "qualified evidence found"
    assert reviewed[0]["score"] > 8


def test_required_and_preferred_qualification_evidence_is_not_assumed():
    job = normalize_job({
        "MatchedObjectDescriptor": {
            "PositionTitle": "Program Analyst",
            "PositionURI": "https://www.usajobs.gov/job/qualification-evidence-test",
            "QualificationSummary": "Preferred: experience with reporting. Must have data analysis.",
        }
    })
    profile = {"data_bi_skills": ["data analysis"], "relevant_work_experience": ["reporting"]}
    reviewed = review_jobs([job], Preferences(5, [], False, [], profile))
    analysis = reviewed[0]["match_analysis"]
    assert analysis["matched_required"]
    assert analysis["matched_preferred"]


def test_normalize_job_preserves_qualification_fields():
    job = normalize_job({
        "MatchedObjectDescriptor": {
            "PositionTitle": "Program Analyst",
            "PositionURI": "https://www.usajobs.gov/job/qualification-test",
            "QualificationSummary": "Requires program analysis experience.",
            "SpecializedExperience": "Experience with performance measurement.",
            "UserArea": {"Details": {
                "MajorDuties": "Evaluate programs and prepare reports.",
                "Education": "Bachelor's degree in public administration.",
                "Certifications": "PMP preferred.",
                "RequiredDocuments": ["Resume", "Transcript"],
            }},
        }
    })
    assert "Evaluate programs" in job["duties"]
    assert "program analysis" in job["qualifications"]
    assert "performance measurement" in job["specialized_experience"]
    assert "public administration" in job["education_requirements"]
    assert "PMP" in job["certification_requirements"]
    assert "Resume" in job["required_documents"][0]


def test_education_profile_matches_education_field_only():
    job = normalize_job({
        "MatchedObjectDescriptor": {
            "PositionTitle": "Operations Specialist",
            "PositionURI": "https://www.usajobs.gov/job/education-test",
            "UserArea": {"Details": {"Education": "Degree in information systems."}},
        }
    })
    reviewed = review_jobs(
        [job],
        Preferences(5, [], False, ["information systems"], {"education": ["information systems"]}),
    )
    assert reviewed[0]["profile_matches"]["education"] == ["information systems"]


def test_review_keeps_only_0343_shape_and_scores_preferences():
    job = normalize_job({
        "MatchedObjectDescriptor": {
            "PositionTitle": "Management and Program Analyst",
            "PositionURI": "https://www.usajobs.gov/job/1",
            "OrganizationName": "Test Agency",
            "PositionLocationDisplay": "Remote job",
            "JobGrade": [{"Code": "GS-12"}],
            "PositionSchedule": [{"Name": "Full-time"}],
            "UserArea": {"Details": {"JobSummary": "Performance measurement and program analysis."}},
            "ApplicationCloseDate": "2026-09-30T00:00:00Z",
        }
    })
    reviewed = review_jobs([job], Preferences(11, ["remote"], False, ["performance"]))
    assert reviewed[0]["grade"] == "GS-12"
    assert reviewed[0]["score"] == 90
    assert "keyword match: performance" in reviewed[0]["reasons"]


def test_remote_only_penalizes_non_remote_job():
    job = normalize_job({
        "MatchedObjectDescriptor": {
            "PositionTitle": "Program Analyst",
            "PositionLocationDisplay": "Washington, DC",
            "JobGrade": [{"Code": "GS-11"}],
            "PositionURI": "https://www.usajobs.gov/job/2",
        }
    })
    reviewed = review_jobs([job], Preferences(11, [], True, []))
    assert reviewed[0]["score"] == 40
    assert "not listed as remote" in reviewed[0]["reasons"]
