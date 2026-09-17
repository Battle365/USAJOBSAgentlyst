from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Iterable

from models import DecisionRecord, EvidenceCitation, EvidenceUnit, EvaluationBatch, ProcessingError, Requirement, RequirementEvaluation, ResumeRecord


DECISION_VERSION = "v3-rules-1"
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "have", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
    "applicant", "applicants", "candidate", "candidates", "experience", "required", "requirement", "must", "qualifying", "qualification", "work", "year", "years",
}
MANDATORY_MARKERS = ("must", "required", "requirement", "specialized experience", "minimum qualification", "in order to qualify")


class NotEvaluable(ValueError):
    pass


def _public_route(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in ("open to the public", "the public", "u.s. citizens", "us citizens"))


def _live_eligibility_routes(vacancy: dict) -> tuple[Requirement, ...]:
    routes = tuple(str(value).strip() for value in vacancy.get("hiring_paths", ()) if str(value).strip())
    if not routes:
        fallback = str(vacancy.get("eligibility") or "").strip()
        routes = (fallback,) if fallback else ()
    if not routes:
        raise NotEvaluable("The announcement does not identify an applicant eligibility path.")
    return tuple(Requirement(f"eligibility-{index}", "eligibility", route)
                 for index, route in enumerate(dict.fromkeys(routes), 1))


def _with_live_routes(paths: tuple[tuple[Requirement, ...], ...], vacancy: dict) -> tuple[tuple[Requirement, ...], ...]:
    if vacancy.get("eligibility_source") != "historic_joa":
        return paths
    routes = _live_eligibility_routes(vacancy)
    return tuple(path + (route,) for path in paths for route in routes)


def _sentences(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", value).strip(" •\t-") for value in re.split(r"\n+|(?<=[.;!?])\s+", text) if value.strip()]


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z][a-z0-9+#.-]{2,}", text.lower()) if token not in STOPWORDS}


def _required_months(text: str) -> int | None:
    lower = text.lower()
    if "one year" in lower or "1 year" in lower or "52 weeks" in lower:
        return 12
    match = re.search(r"(\d+(?:\.\d+)?)\s+years?", lower)
    if match:
        return round(float(match.group(1)) * 12)
    match = re.search(r"(\d+)\s+months?", lower)
    return int(match.group(1)) if match else None


def _make_requirements(text: str, prefix: str, default_kind: str) -> list[Requirement]:
    requirements: list[Requirement] = []
    sentences = _sentences(text)
    selected = [sentence for sentence in sentences if any(marker in sentence.lower() for marker in MANDATORY_MARKERS)]
    if default_kind == "education" and text.strip():
        selected = selected or [sentence for sentence in sentences if any(term in sentence.lower() for term in ("degree", "semester", "quarter", "coursework", "credits"))]
    if default_kind in {"license", "eligibility"} and text.strip():
        selected = selected or sentences
    for index, sentence in enumerate(selected):
        lower = sentence.lower()
        if any(phrase in lower for phrase in ("not required", "no specialized experience requirement", "preferred", "desired but not required")):
            continue
        if len(_tokens(sentence)) <= 2 and not re.search(r"\b(must|required|\d+|one|two|three)\b", lower):
            continue
        if default_kind == "education" and any(phrase in lower for phrase in ("may not be substituted", "cannot be substituted", "education is not required")):
            continue
        kind = default_kind
        if any(word in lower for word in ("degree", "coursework", "semester", "education", "transcript")):
            kind = "education"
        if any(word in lower for word in ("license", "licensure", "certification", "certified")):
            kind = "license"
        requirements.append(Requirement(f"{prefix}-{index + 1}", kind, sentence, True, _required_months(sentence)))
    return requirements


def extract_requirement_paths(vacancy: dict) -> tuple[tuple[Requirement, ...], ...]:
    qualification_text = "\n".join(filter(None, (vacancy.get("specialized_experience", ""), vacancy.get("qualifications", ""))))
    education_text = str(vacancy.get("education", ""))
    eligibility_text = str(vacancy.get("eligibility", ""))
    conditions_text = str(vacancy.get("conditions", ""))
    if not qualification_text.strip() and not education_text.strip():
        raise NotEvaluable("The announcement does not include enough qualification information to evaluate.")
    all_text = f"{qualification_text} {education_text}".lower()
    if ("qualification standard" in all_text or "opm standard" in all_text) and len(_tokens(all_text)) < 10:
        raise NotEvaluable("The announcement refers to an external qualification standard without including enough detail.")

    experience = _make_requirements(qualification_text, "experience", "experience")
    education = _make_requirements(education_text, "education", "education")
    conditions = _make_requirements(conditions_text, "condition", "condition")
    eligibility: list[Requirement] = []
    eligibility_lower = eligibility_text.lower()
    public_path = _public_route(eligibility_lower)
    if vacancy.get("eligibility_source") != "historic_joa" and eligibility_text.strip() and not public_path:
        eligibility = [Requirement("eligibility-1", "eligibility", eligibility_text)]

    substitution_denied = any(phrase in all_text for phrase in ("may not be substituted", "cannot be substituted", "no substitution"))
    alternative = bool(experience and education and re.search(r"\b(or|substitut|combination)\b", all_text) and not substitution_denied)
    base = tuple(conditions + eligibility)
    if alternative:
        paths = []
        if experience:
            paths.append(tuple(experience) + base)
        if education:
            paths.append(tuple(education) + base)
        if "combination" in all_text:
            paths.append(tuple(experience + education) + base)
        return _with_live_routes(tuple(paths), vacancy)
    offered_grades = [str(grade).upper() for grade in vacancy.get("grades", [])]
    grade_groups: list[tuple[Requirement, ...]] = []
    if len(offered_grades) > 1 and experience:
        common = [item for item in experience if not re.search(r"\bGS[- ]?\d{1,2}\b", item.text, re.I)]
        for grade in offered_grades:
            number = re.sub(r"\D", "", grade)
            specific = [item for item in experience if re.search(rf"\bGS[- ]?{re.escape(number)}\b", item.text, re.I)]
            if specific:
                grade_groups.append(tuple(common + specific + education) + base)
        if grade_groups:
            return _with_live_routes(tuple(grade_groups), vacancy)
    combined = tuple(experience + education) + base
    if not combined:
        raise NotEvaluable("No explicit qualification path could be identified.")
    return _with_live_routes((combined,), vacancy)


def _citation(requirement: Requirement, unit: EvidenceUnit) -> EvidenceCitation:
    return EvidenceCitation(requirement.requirement_id, unit.section, unit.page, unit.text[:240])


def _support(requirement: Requirement, evidence: Iterable[EvidenceUnit]) -> RequirementEvaluation:
    required = _tokens(requirement.text)
    # Generic announcement boilerplate must not create accidental matches.
    required -= {"position", "announcement", "grade", "level", "equivalent", "federal", "demonstrate", "ability", "knowledge"}
    matches: list[tuple[float, EvidenceUnit]] = []
    for unit in evidence:
        if requirement.kind == "experience":
            if unit.section in {"skills", "education", "certifications", "licenses", "training", "clearances"}:
                continue
            if unit.section == "unspecified" and not re.search(r"\b(performed|provided|managed|developed|conducted|led|administered|designed|analyzed|evaluated|delivered|treated|diagnosed|worked)\b", unit.text, re.I):
                continue
        present = _tokens(unit.text)
        overlap = required & present
        score = len(overlap) / max(1, min(len(required), 6))
        if len(overlap) >= 2 and score >= 0.34:
            matches.append((score, unit))
    matches.sort(key=lambda item: (-item[0], item[1].page or 0, item[1].text))

    if requirement.kind == "eligibility":
        matches = [(score, unit) for score, unit in matches if any(term in unit.text.lower() for term in ("veteran", "schedule a", "competitive service", "excepted service", "displaced federal", "military spouse"))]
    if requirement.kind == "education":
        matches = [(score, unit) for score, unit in matches if unit.section == "education" or any(term in unit.text.lower() for term in ("degree", "bachelor", "master", "ph.d", "credits", "coursework"))]
    if requirement.kind == "license":
        matches = [(score, unit) for score, unit in matches if unit.section in {"certifications", "licenses", "training"}]

    if requirement.duration_months:
        qualified = [(score, unit) for score, unit in matches if unit.duration_months is not None and unit.duration_months >= requirement.duration_months]
        if not qualified:
            unknown_duration = [(score, unit) for score, unit in matches if unit.duration_months is None]
            if unknown_duration:
                citations = tuple(_citation(requirement, unit) for _, unit in unknown_duration[:3])
                return RequirementEvaluation(requirement, False, citations, "resume shows related work, but its duration is not established")
            return RequirementEvaluation(requirement, False, (), f"resume does not show {requirement.duration_months} months of the required experience")
        matches = qualified
    if not matches:
        return RequirementEvaluation(requirement, False, (), "resume does not show sufficient evidence for this mandatory requirement")
    citations = tuple(_citation(requirement, unit) for _, unit in matches[:3])
    return RequirementEvaluation(requirement, True, citations, "supported by explicit resume evidence")


def _support_live_eligibility(requirement: Requirement, evidence: Iterable[EvidenceUnit]) -> RequirementEvaluation:
    if _public_route(requirement.text):
        return RequirementEvaluation(requirement, True, (), "public hiring path")
    route = requirement.text.lower()
    if "federal employee" in route:
        qualified = tuple(unit for unit in evidence
                          if re.search(r"\bfederal employees?\b", unit.text, re.I)
                          and ("competitive service" not in route or "competitive service" in unit.text.lower()))
        if qualified:
            return RequirementEvaluation(requirement, True,
                                         tuple(_citation(requirement, unit) for unit in qualified[:3]),
                                         "supported by explicit resume status evidence")
    else:
        supported = _support(requirement, evidence)
        if supported.satisfied:
            return supported
    return RequirementEvaluation(requirement, False, (),
                                 "resume does not show evidence for this hiring eligibility path")


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def evaluate_vacancy(resume: ResumeRecord, vacancy: dict, *, today: date | None = None) -> DecisionRecord:
    if not vacancy.get("id"):
        raise NotEvaluable("The announcement is missing its authoritative identifier.")
    close_date = _parse_date(str(vacancy.get("close_date", "")))
    if close_date and close_date < (today or datetime.now(UTC).date()):
        raise NotEvaluable("The announcement is closed and is no longer actionable.")
    paths = extract_requirement_paths(vacancy)
    live = vacancy.get("eligibility_source") == "historic_joa"
    evaluated_paths = tuple(tuple(
        _support_live_eligibility(requirement, resume.evidence)
        if live and requirement.kind == "eligibility"
        else _support(requirement, resume.evidence)
        for requirement in path
    ) for path in paths)
    passing = [path for path in evaluated_paths if path and all(item.satisfied for item in path)]
    outcome = "MATCH" if passing else "NOT A MATCH"
    if outcome == "MATCH":
        reason = "MATCH — the submitted resume shows evidence for every mandatory requirement in a permitted qualification path."
    else:
        failed = next((item for path in evaluated_paths for item in path if not item.satisfied), None)
        detail = failed.note if failed else "resume evidence does not establish a complete qualification path"
        reason = f"NOT A MATCH — {detail}."
    matched_grade = None
    if passing:
        passing_text = " ".join(item.requirement.text for item in passing[0])
        for grade in vacancy.get("grades", []):
            number = re.sub(r"\D", "", str(grade))
            if number and re.search(rf"\bGS[- ]?{re.escape(number)}\b", passing_text, re.I):
                matched_grade = str(grade)
                break
        if matched_grade is None and len(vacancy.get("grades", [])) == 1:
            matched_grade = vacancy["grades"][0]
    eligibility_routes = tuple(dict((item.requirement.requirement_id, item)
                                    for path in evaluated_paths for item in path
                                    if live and item.requirement.kind == "eligibility").values())
    return DecisionRecord(
        resume_hash=resume.content_hash, vacancy_id=vacancy["id"], vacancy_hash=vacancy["source_hash"],
        decision_version=DECISION_VERSION, paths=evaluated_paths, final_outcome=outcome,
        reason=reason, processed_at=datetime.now(UTC).isoformat(), matched_grade=matched_grade,
        eligibility_source_paths=tuple(vacancy.get("hiring_paths", ())) if live else (),
        eligibility_who_may_apply=str(vacancy.get("eligibility") or "") if live else "",
        eligibility_routes=eligibility_routes,
    )


def evaluate_vacancies(resume: ResumeRecord, vacancies: list[dict]) -> EvaluationBatch:
    batch = EvaluationBatch()
    for vacancy in vacancies:
        try:
            batch.decisions.append((vacancy, evaluate_vacancy(resume, vacancy)))
        except NotEvaluable as exc:
            batch.errors.append(ProcessingError(str(vacancy.get("id", "unknown")), str(vacancy.get("title", "Untitled position")), str(vacancy.get("agency", "Federal agency")), str(exc)))
        except Exception:
            batch.errors.append(ProcessingError(str(vacancy.get("id", "unknown")), str(vacancy.get("title", "Untitled position")), str(vacancy.get("agency", "Federal agency")), "The vacancy could not be processed.", True))
    return batch
