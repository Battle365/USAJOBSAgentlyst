# USAJOBSAgent V4

A résumé-first Streamlit matcher for federal vacancy announcements. Paste résumé text or upload a PDF/DOCX, then click **Find Matching Jobs**. Behind the scenes, the app infers strongly supported occupational series from documented work experience, retrieves open announcements through the official unauthenticated Historic JOAs and Announcement Text endpoints, and compares each announcement with the résumé. Results are classified **MATCH**, **POSSIBLE MATCH**, or **NOT A MATCH**. Pasted announcement text remains available as a secondary fallback.

A MATCH is evidence screening, not a guarantee of eligibility, qualification, referral, interview, or selection. The application never signs in to USAJOBS, collects a USAJOBS password, prepares an application, or submits one. The only application-related action is a link to the official announcement.

## Run locally

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The V4 pipeline is **Résumé → Profile Extraction → Résumé-aware Discovery/Pre-ranking → Hiring Eligibility → Qualification Matching → Results**. Profile extraction links inferred occupational series and duty signals to résumé evidence. Discovery uses that profile only to prioritize which current announcements receive deeper analysis; it cannot decide qualifications or hiring eligibility. The matcher audits the actual announcement requirements against résumé evidence, and the results link to the official announcement.

No USAJOBS API key or email is required. Job discovery uses only the public Historic JOAs and Announcement Text data endpoints; it does not call the authenticated Search API or scrape webpages. A pasted announcement may include an optional official USAJOBS URL for the result link, but the app does not fetch that webpage.

Discovery is intentionally bounded and **not an exhaustive USAJOBS search**. It uses the public official occupational-series code list when available and a smaller verified fallback catalog otherwise. The same evidence rules apply across professions: documented roles, explicit series, or multiple performed-work signals can support an inferred series; isolated skill keywords do not. Up to five strongly supported series are searched. The deterministic résumé-aware pre-ranker selects at most 18 distinct open, grade-eligible announcements for qualification-text retrieval, using profile evidence and Historic JOA summary metadata for retrieval priority only. GS-11 is the default minimum for GS vacancies; non-GS pay plans may be displayed with an explicit notice because GS equivalence cannot be established from the Historic JOA data. If no series can be inferred confidently, use the secondary pasted-announcement option. Historic JOA dates and opening status are checked before qualification retrieval; a vacancy can change after retrieval, so review the official announcement before acting.

`MATCH` means the existing deterministic matcher found explicit evidence for every mandatory requirement in one identified path. `POSSIBLE MATCH` is narrower than a general near-match: it requires cited related work and no failed non-duration requirement, but the required experience duration remains unestablished. A known short duration, title-only similarity, or missing mandatory license/education/eligibility evidence remains `NOT A MATCH`. Failed retrieval or incomplete data is reported separately as not evaluated. Stronger classifications appear first; no public percentage is shown.

## Architecture

- `resume_ingest.py`: signature, size, archive/active-content, encryption, readability, and extraction checks; session-scoped resume/evidence records.
- `acquisition.py`: pasted-text normalization and official-host URL validation. Legacy URL retrieval remains in this module but is not used by the active app.
- `historic_poc.py`: reusable unauthenticated Historic JOA endpoint primitives and open-status filtering.
- `profile_extraction.py`: structured, session-only résumé profile with supported series, confidence, source evidence, duty signals, and existing discovery limits.
- `pre_ranking.py`: deterministic résumé-aware selection of summaries for the 18-announcement deep-analysis budget; it never determines eligibility or match outcomes.
- `job_discovery.py`: provider-neutral discovery orchestration that consumes the profile, pre-ranker, and Historic JOA adapter. It does not import or modify the matching engine.
- `series_inference.py`: official occupational-series catalog and occupation-neutral, confidence-bounded inference from résumé evidence.
- `usajobs.py`: isolated, disabled authenticated API provider code retained for a possible future approved integration; the active app does not import or invoke it.
- `matcher.py`: deterministic mandatory-requirement paths, conservative evidence mapping, binary qualification decisions, and not-evaluable separation.
- `match_presentation.py`: user-facing three-class triage and ranking without weakening the core MATCH rule.
- `models.py`: auditable resume, requirement, citation, decision, and processing-error records.
- `app.py`: one-button résumé-to-results flow, secondary manual announcement option, privacy, replacement, and deletion controls.
- `GAP_ANALYSIS.md`: required pre-implementation assessment of the original repository.

## Privacy and security

V4 deliberately implements session-only retention. It does not offer saved resumes or user accounts. Source bytes are not persisted by application code; extracted resume text, announcement text, and derived evidence live only in the Streamlit server session and are removed with the deletion control or session expiry. Full document text is not logged.

Uploads are limited to 8 MB PDF/DOCX files and checked for matching signatures, encrypted PDFs, unsafe DOCX archive characteristics, active content, known test-malware signatures, and insufficient extracted text. Pasted résumé text does not use or require ClamAV. When `APP_ENV=production`, PDF/DOCX uploads fail closed unless a reachable ClamAV `clamd` service is configured with `CLAMAV_HOST` and `CLAMAV_PORT`; pasted-text matching remains available. The client uses the ClamAV INSTREAM protocol, so source bytes do not need to be written to a temporary file.

### Production configuration

The checked-in `render.yaml` defines the application service but does not provision ClamAV. Configure these values in the production environment; do not put personal contact details or credentials in source control:

| Variable | Production use |
| --- | --- |
| `APP_ENV` | Set to `production` so PDF/DOCX uploads require a successful malware scan. |
| `PRIVACY_CONTACT` | Operator-controlled privacy/support email address or contact route displayed in the privacy panel; provide a real, monitored route before public release. |
| `CLAMAV_HOST` | Private hostname of a reachable `clamd` service; required for production PDF/DOCX uploads, not for pasted résumés. |
| `CLAMAV_PORT` | `clamd` TCP port (default `3310`); must match the service. |
| `SESSION_RETENTION_MINUTES` | Session-data retention period (default `60`); choose and disclose the production policy. |
| `PYTHON_VERSION` | Python runtime version selected by the deployment configuration. |

The hosting platform supplies `PORT` for the Streamlit start command. Neither `USAJOBS_API_KEY` nor `USAJOBS_EMAIL` is needed by the active V4 workflow.

The simplest planned upload-safe architecture is a separate, privately reachable `clamd` service on the same hosting private network as `usajobsagent`, with no public scanner endpoint. Configure `CLAMAV_HOST` to its internal hostname and `CLAMAV_PORT` to its listening port. Keep virus signatures updated, allow the app to reach the scanner, and monitor scanner health; an unavailable scanner intentionally blocks file uploads. Confirm private-network support and adequate memory for the chosen service plan before enabling uploads. This is a configuration plan only; no scanner infrastructure is included in this repository.

The application enforces the configured session lifetime and deletes the resume identifier, extracted text, evidence, vacancies, and decisions at expiry. The hosting layer remains responsible for HTTPS, encryption of infrastructure-managed memory/swap, rate limiting, monitoring, and an appropriate support route.

Document and announcement content is always treated as untrusted data. The matcher is deterministic code and does not execute embedded instructions or send resume content to an external model.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite covers pasted and uploaded résumés, manual announcement matching without credentials, conservative series inference, open/closed filtering, Historic JOA pagination, Announcement Text retrieval, discovery-to-matcher integration, all three display classifications and ordering, title-only rejection, duration, licenses/status, alternative and multi-grade paths, processing errors, prompt-like content, and deterministic reruns.
