# USAJOBSAgent V.3 Gap Analysis

Date: 2026-09-14

This analysis was completed before V.3 implementation, as required by the implementation brief. The inspected baseline consisted of `app.py`, `test_app.py`, `README.md`, deployment files, and a four-package Python dependency list.

## Executive finding

The baseline is a Streamlit job-review prototype, not a resume-first binary vacancy matcher. It searches a fixed set of job families, accepts a hand-entered skills profile, ranks jobs with a public percentage, and includes application-preparation and submit-labelled workflow controls. A V.3 implementation requires a substantial replacement of the domain model, ingestion pipeline, USAJOBS client, decision engine, interface, and tests.

## Gap matrix

| V.3 area | Baseline state | Gap / required change |
|---|---|---|
| Server credentials | Reads server configuration, but falls back to an end-user API-key field and demo mode | Remove every credential input and demo fallback; fail safely when operator configuration is absent; redact errors |
| Resume-first flow | Uses a prefilled/editable profile; no document upload | Require one readable PDF/DOCX; validate, extract, structure, hash, identify version, replace, and delete |
| File safety | No upload controls | Enforce size, extension, MIME/signature, encrypted/empty/image-only checks, archive safety, active-content rejection, and malware rejection hook/signature |
| Data minimization | Large hard-coded personal profile is embedded in source | Remove embedded applicant data; keep resume data session-only by default and never log content |
| Vacancy search | Fixed search terms, 25 results each, no reliable pagination completeness | Support user keywords/location and supported filters; paginate; deduplicate by announcement/control number; report partial/error states |
| Vacancy normalization | Retains only a subset of fields | Preserve identifiers, URL, dates, locations, remote designation, series/grades, eligibility, qualification/duty/education/conditions text, retrieval time, and source hash |
| Matching | Additive title/keyword scoring and transferable-term heuristics | Replace with deterministic mandatory-requirement extraction, independent qualification paths, resume evidence mapping, hard gates, and all-mandatory-elements rule |
| Decisions | Public score and “strong matches” threshold | Expose exactly `MATCH` or `NO MATCH` for every successfully evaluated vacancy; no percentage or third outcome |
| Explainability | Loose lists of keyword hits and gaps | Produce structured requirement/evidence records with resume section/page or excerpt references, source hashes, decision version, and factual user reason |
| Processing failures | API errors stop the page; incomplete data may still be ranked | Keep processing/data failures outside evaluated counts and expose retry guidance |
| Results UI | Ranked location tiers and fit metrics | Default to MATCH; offer MATCH/NO MATCH filter; required card fields; separate not-evaluated notice |
| Official action | Official links exist, but application preparation, apply fragments, and submit-labelled controls also exist | Remove all application-preparation/submission UI and code; retain only “View on USAJOBS” |
| Privacy controls | Session claims but no deletion workflow or privacy screen | Add concise processing/storage/retention notice and deletion that clears source bytes, extracted text, evidence, decisions, and identifiers |
| Accessibility | Basic Streamlit labels, but decision relies partly on layout and the flow is not defined | Use explicit textual labels, labelled controls, logical headings, keyboard-native widgets, and non-color-only outcomes |
| Security | Secrets may be exposed through user UI; no upload hardening or prompt-injection boundary | Server-only secrets, safe errors, strict untrusted-data handling, structured deterministic parsing, no document text in logs |
| Reliability/testing | Tests assert legacy scoring/demo/preparation behavior | Replace with unit/integration/regression fixtures for upload failures, pagination/dedup, MATCH, NO MATCH, alternate paths, duration, hard gates, injection text, deterministic reruns, and processing errors |

## Implementation implications

1. The existing score/profile/application-preparation concepts are incompatible with the mandate and should be removed rather than adapted.
2. V.3 will use session-only resume retention. Saved resumes/accounts and cross-user storage are deliberately not enabled.
3. Matching will be deterministic and rule-based for V.3. Ambiguous vacancy requirements will be marked not evaluable instead of guessed.
4. Malware scanning will use layered local validation with an optional operator-configured scanner; public production deployment must configure the scanner for full coverage.
5. USAJOBS search credentials remain operator-managed through `USAJOBS_API_KEY` and `USAJOBS_EMAIL`; neither value will enter the browser UI or client state.

## Baseline acceptance status

Only the prohibition on actually signing in or transmitting an application and the presence of official USAJOBS links were substantially present. The remaining V.3 acceptance criteria were absent, incomplete, or contradicted by the current interface. Implementation therefore proceeds in the mandated order from credential removal through the acceptance suite.
