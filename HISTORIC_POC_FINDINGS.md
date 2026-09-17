# Unauthenticated Historic JOA proof of concept

Checked: 2026-09-16. This report records the proof-of-concept verification before integration. The validated endpoint primitives were subsequently wired into the app through `job_discovery.py`, without changing the matching engine.

## Evidence

- USAJOBS documents `GET /api/historicjoa` as unauthenticated bulk access to smaller fields for current and past announcements. It includes control number, title, agency, occupational series, open/close dates, early-expiration date, opening status, hiring paths, grade, and location. Source: https://developer.usajobs.gov/api-reference/get-api-historicjoa
- USAJOBS documents `GET /api/historicjoa/announcementtext` as unauthenticated bulk access to long text, including qualifications, education, conditions of employment, duties, and evaluations. Both endpoints use filters such as series, agency code, dates, and control number, plus continuation-token pagination. Source: https://developer.usajobs.gov/api-reference/get-api-announcementtext
- A credential-free live request for series 0343 with a close-date lower bound of 2026-09-16 returned HTTP 200 and 301 records. Of those, 248 had status `Accepting applications`, open date on or before 2026-09-16, close date on or after it, and no prior early-expiration date. The other records included closed, canceled, under-review, selected, or unknown-status announcements, demonstrating why the date filter alone is insufficient.
- A credential-free Announcement Text request for each of three open control numbers returned HTTP 200 and one matching record per control number. Qualification text lengths were 5,505, 3,362, and 661 characters. The three normalized records ran through the existing matching engine: three evaluated, zero processing errors, all `NOT A MATCH` for a synthetic program-analysis fixture.

## Feasibility verdict

**Feasible for bounded, credential-free discovery by occupational series or agency, with conservative filtering and explicit partial-result notices. Not yet reliable as unrestricted resume-driven discovery of all relevant current jobs.**

The Historic JOAs endpoint is a bulk feed, not the authenticated Search API. It has no documented keyword, location, remote-only, or semantic search parameter. A resume-to-series mapping can generate candidate series only for recognized evidence; unknown or cross-disciplinary resumes would need user confirmation or a broader, potentially costly feed scan. The PoC uses only a narrow 0343/2210/1560 mapping and does not claim comprehensive discovery.

An announcement is treated as viable only when status is exactly `Accepting applications`, dates are present and current, and an early-expiration date has not passed. Missing or unknown fields fail closed. Even then, status may change after retrieval; actionable links should be refreshed immediately before display or use. Historic JOA `teleworkEligible` is not proof of remote designation.

Announcement Text appears sufficient to supply many qualification paragraphs, but individual records can omit fields or reference outside standards. The existing conservative matcher must continue to classify incomplete/ambiguous records as not evaluable rather than inventing a qualification result. A production integration would need bounded pagination, rate/error handling, cache freshness, explicit candidate-series confirmation, source/version hashes, and evaluation-quality tests against representative real announcements.

## Proof-of-concept boundaries

- No authenticated Search API, API key, or credential is used.
- No USAJOBS webpage is scraped.
- No resume content is sent to USAJOBS; only series/date/control-number query parameters are sent.
- No app architecture or user-facing flow was changed.
- No commit, push, or deployment was performed.
