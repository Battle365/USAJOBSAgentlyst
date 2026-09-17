from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from functools import partial

import streamlit as st
from dotenv import load_dotenv

from acquisition import AnnouncementInputError, build_manual_vacancy
from job_discovery import discover_for_profile
from match_presentation import ClassifiedDecision, rank_decisions
from matcher import evaluate_vacancies
from models import DecisionRecord, ResumeRecord
from profile_extraction import extract_profile
from resume_ingest import MAX_RESUME_BYTES, ResumeValidationError, parse_pasted_resume, parse_resume, scan_with_clamav

load_dotenv()


def delete_session_data() -> None:
    """Remove the resume record and every associated decision from this session."""
    for key in ("resume", "profile", "vacancies", "batch", "discovery_result", "owner_id", "session_started"):
        st.session_state.pop(key, None)
    st.session_state["upload_nonce"] = secrets.token_hex(8)


def _init_state() -> None:
    st.session_state.setdefault("owner_id", secrets.token_urlsafe(24))
    st.session_state.setdefault("upload_nonce", secrets.token_hex(8))
    st.session_state.setdefault("screen", "resume")
    st.session_state.setdefault("session_started", datetime.now(UTC))
    retention = _retention_minutes()
    if datetime.now(UTC) - st.session_state.session_started > timedelta(minutes=max(5, retention)):
        delete_session_data()
        st.session_state.session_started = datetime.now(UTC)
        st.session_state.screen = "resume"


def _resume_screen() -> None:
    st.header("1. Add your résumé")
    st.write("Paste your résumé or upload a readable PDF/DOCX. We compare its work and education evidence with actual open USAJOBS announcements; we never apply for you.")
    st.info("Session-only privacy: the file is processed in memory for matching and is discarded when you delete it or the session ends. Full resume text is not shown or written to ordinary logs.")
    resume: ResumeRecord | None = st.session_state.get("resume")
    if resume:
        st.success(f"Résumé ready: {resume.filename} — text extraction succeeded")
        if st.button("Find Matching Jobs", type="primary", use_container_width=True):
            _run_discovery(resume)
        left, right = st.columns(2)
        if left.button("Replace resume", use_container_width=True):
            delete_session_data()
            st.rerun()
        if right.button("Remove resume and session data", use_container_width=True):
            delete_session_data()
            st.rerun()
        with st.expander("Have a specific announcement? Paste it instead"):
            _manual_announcement_form(resume)
        return
    paste_tab, upload_tab = st.tabs(["Paste résumé", "Upload PDF or DOCX"])
    with paste_tab:
        with st.form("paste-resume"):
            pasted = st.text_area("Résumé text", height=320, placeholder="Include positions, performed duties, dates, hours, education, and credentials when stated.")
            find_from_paste = st.form_submit_button("Find Matching Jobs", type="primary")
        if find_from_paste:
            try:
                record = parse_pasted_resume(pasted, st.session_state.owner_id)
                st.session_state.resume = record
                _run_discovery(record)
            except ResumeValidationError as exc:
                st.error(str(exc))
    with upload_tab:
        uploaded = st.file_uploader(
            "Résumé file", type=["pdf", "docx"], key=f"resume-upload-{st.session_state.upload_nonce}",
            help=f"PDF or DOCX, up to {MAX_RESUME_BYTES // 1024 // 1024} MB. Password-protected, image-only, unsafe, empty, or unreadable files are rejected.",
        )
        st.caption(f"Accepted: PDF and DOCX • Maximum size: {MAX_RESUME_BYTES // 1024 // 1024} MB • No saved-résumé feature is enabled")
        if uploaded is not None:
            try:
                with st.spinner("Validating and extracting résumé text…"):
                    clamav_host = os.getenv("CLAMAV_HOST", "").strip()
                    scanner = partial(scan_with_clamav, host=clamav_host, port=int(os.getenv("CLAMAV_PORT", "3310"))) if clamav_host else None
                    production = os.getenv("APP_ENV", "development").lower() == "production"
                    record = parse_resume(uploaded.getvalue(), uploaded.name, uploaded.type, st.session_state.owner_id, malware_scanner=scanner, require_malware_scan=production)
                st.session_state.resume = record
                st.rerun()
            except ResumeValidationError as exc:
                st.error(str(exc))


def _run_discovery(resume: ResumeRecord) -> None:
    with st.spinner("Finding open announcements and comparing qualifications with your résumé…"):
        profile = extract_profile(resume)
        st.session_state.profile = profile
        result = discover_for_profile(profile)
        st.session_state.discovery_result = result
        st.session_state.batch = evaluate_vacancies(resume, list(result.vacancies))
        st.session_state.batch.errors.extend(result.errors)
        st.session_state.vacancies = list(result.vacancies)
    st.session_state.screen = "results"
    st.rerun()


def _manual_announcement_form(resume: ResumeRecord) -> None:
    st.write("Paste the complete announcement, especially Who May Apply, Qualifications, Specialized Experience, Education, and Conditions of Employment. The optional official URL is only a result link; no webpage is scraped.")
    with st.form("announcement-input"):
        title = st.text_input("Vacancy title (optional)", placeholder="Title from the announcement")
        agency = st.text_input("Agency (optional)", placeholder="Department of Veterans Affairs")
        official_url = st.text_input("Official USAJOBS URL for result link (optional)", placeholder="https://www.usajobs.gov/job/...")
        announcement_text = st.text_area("Announcement text", height=360, placeholder="Paste the complete USAJOBS announcement here…")
        evaluate = st.form_submit_button("Evaluate announcement", type="primary")
    if evaluate:
        try:
            with st.spinner("Reading the announcement…"):
                vacancy = build_manual_vacancy(title, agency, announcement_text, official_url)
            with st.spinner("Comparing announcement requirements with resume evidence…"):
                st.session_state.vacancies = [vacancy]
                st.session_state.batch = evaluate_vacancies(resume, [vacancy])
                st.session_state.pop("discovery_result", None)
            st.session_state.screen = "results"
            st.rerun()
        except AnnouncementInputError as exc:
            st.error(str(exc))


def _render_decision(vacancy: dict, decision: DecisionRecord, classified: ClassifiedDecision) -> None:
    with st.container(border=True):
        st.subheader(vacancy["title"])
        st.write(vacancy["agency"])
        st.write(f"Series: {vacancy.get('series') or 'Not supplied'} · Grade: {', '.join(vacancy.get('grades') or []) or 'Not supplied'}")
        if vacancy.get("grade_note"):
            st.caption(vacancy["grade_note"])
        st.write(f"Location: {'Remote' if vacancy.get('remote') else vacancy.get('location', 'Location not listed')}")
        st.write(f"Opens: {vacancy.get('open_date') or 'Not supplied'} · Closes: {vacancy.get('close_date') or 'Not supplied'}")
        if classified.label == "MATCH":
            st.success("MATCH")
        elif classified.label == "POSSIBLE MATCH":
            st.warning("POSSIBLE MATCH")
        else:
            st.error("NOT A MATCH")
        if decision.matched_grade:
            st.caption(f"Supported grade path: {decision.matched_grade}")
        st.write(classified.explanation)
        if classified.evidence:
            st.write("Supporting résumé evidence:")
            for citation in classified.evidence[:2]:
                st.caption(f"{citation.section}, page {citation.page or '?'}: {citation.excerpt}")
        if classified.gaps:
            st.write("Important missing or unresolved requirements:")
            for gap in classified.gaps[:2]:
                st.caption(f"Submitted résumé does not establish: {gap}")
        if vacancy.get("url"):
            st.link_button("View Official Announcement", vacancy["url"], type="primary" if classified.label == "MATCH" else "secondary")
        with st.expander("Decision details"):
            st.caption(f"Decision version: {decision.decision_version} • Vacancy: {decision.vacancy_id}")
            for path_index, path in enumerate(decision.paths, start=1):
                st.write(f"Qualification path {path_index}")
                for evaluation in path:
                    marker = "Supported" if evaluation.satisfied else "Not supported"
                    st.write(f"{marker}: {evaluation.requirement.text}")
                    for citation in evaluation.citations:
                        source = f"page {citation.page}" if citation.page else citation.section
                        st.caption(f"Resume evidence ({source}): {citation.excerpt}")


def _results_screen() -> None:
    batch = st.session_state.get("batch")
    if not batch:
        st.session_state.screen = "resume"
        st.rerun()
    ranked = rank_decisions(batch.decisions)
    st.header("Matching jobs")
    discovery = st.session_state.get("discovery_result")
    if discovery:
        st.caption("Results cover a bounded set of current announcements based on occupational series supported by work experience in your résumé. GS-11 is the default minimum for GS vacancies; results are not an exhaustive USAJOBS search.")
        st.caption(f"Series considered: {', '.join(discovery.series) or 'none'} · Open candidates: {discovery.open_candidate_count} · Qualification texts checked: {len(discovery.vacancies)}")
        for notice in discovery.notices:
            if "partially" in notice.lower() or "could not" in notice.lower():
                st.warning(notice)
        if not discovery.series:
            st.info("No confident occupational series was found. You can still paste a specific announcement below.")
    st.write("MATCH requires evidence for every mandatory requirement. POSSIBLE MATCH means related work is shown but a required duration is unresolved. Neither label guarantees eligibility, referral, interview, or selection.")
    counts = {label: sum(item.label == label for _, _, item in ranked) for label in ("MATCH", "POSSIBLE MATCH", "NOT A MATCH")}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("MATCH", counts["MATCH"])
    c2.metric("POSSIBLE MATCH", counts["POSSIBLE MATCH"])
    c3.metric("NOT A MATCH", counts["NOT A MATCH"])
    c4.metric("Not evaluated", len(batch.errors))
    if not ranked:
        st.info("No announcement was evaluated in this run. Try again or use the pasted-announcement option below.")
    for vacancy, decision, classified in ranked:
        _render_decision(vacancy, decision, classified)
    if batch.errors:
        st.warning(f"{len(batch.errors)} announcement(s) were not evaluated because required data was missing or processing failed. They are excluded from all match classifications.")
        with st.expander("Not evaluated announcements"):
            for error in batch.errors:
                st.write(f"{error.title} — {error.agency}: {error.message}")
    left, right = st.columns(2)
    if left.button("Find Matching Jobs Again", use_container_width=True):
        _run_discovery(st.session_state.resume)
    if right.button("Change or delete résumé", use_container_width=True):
        delete_session_data()
        st.session_state.screen = "resume"
        st.rerun()
    with st.expander("Have a specific announcement? Paste it instead"):
        _manual_announcement_form(st.session_state.resume)


def _privacy_panel() -> None:
    with st.expander("Privacy and data controls"):
        retention = _retention_minutes()
        st.write(f"The app processes the uploaded file, extracted text, structured evidence, pasted announcement text, and decision records only to perform the comparison. V.3 stores this data only in the current server session; it is removed when you use the deletion control or after {retention} minutes. Standard logs must not contain resume or announcement text.")
        st.write("Job Discovery sends only inferred occupational-series codes, dates, and announcement control numbers to the official unauthenticated USAJOBS data endpoints; it does not transmit your résumé. No USAJOBS credentials are required. The app never asks for or stores a USAJOBS password and does not use resume content for model training.")
        contact = os.getenv("PRIVACY_CONTACT", "the operator listed by this deployment")
        st.write(f"For privacy questions, contact {contact}.")


def _retention_minutes() -> int:
    try:
        return max(5, int(os.getenv("SESSION_RETENTION_MINUTES", "60")))
    except ValueError:
        return 60


def main() -> None:
    st.set_page_config(page_title="USAJOBS Resume Matcher", page_icon="📄", layout="wide")
    _init_state()
    st.title("USAJOBS Resume Matcher")
    st.write("Compare evidence in one résumé with selected current federal vacancies or a pasted announcement. This tool never signs in, auto-applies, uploads application materials, or submits an application.")
    _privacy_panel()
    screen = st.session_state.screen if st.session_state.get("resume") else "resume"
    if screen == "results":
        _results_screen()
    else:
        _resume_screen()


if __name__ == "__main__":
    main()
