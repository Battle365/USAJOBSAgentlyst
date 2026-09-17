from models import EvidenceUnit, ResumeRecord
from series_inference import FALLBACK_CATALOG, MAX_CONFIDENT_SERIES, infer_series


def _resume(*sections):
    units = tuple(EvidenceUnit(text, section) for section, text in sections)
    return ResumeRecord("f", "o", "r", "text/plain", 1, "", "hash", "\n".join(unit.text for unit in units), units)


def test_professional_roles_use_same_rule_across_families():
    examples = {
        "Registered Nurse, 2019-2025. Provided nursing care.": "0610",
        "Physician Assistant, 2019-2025. Conducted diagnostic evaluation.": "0603",
        "Physician, 2019-2025. Conducted patient examination.": "0602",
        "Social Worker, 2019-2025. Conducted psychosocial assessments.": "0185",
        "IT Specialist, 2019-2025. Managed networks.": "2210",
        "Administrative Officer, 2019-2025. Managed office operations.": "0341",
        "Financial Analyst, 2019-2025. Performed financial reporting.": "0501",
        "Civil Engineer, 2019-2025. Led engineering design.": "0810",
        "Program Analyst, 2019-2025. Performed program analysis.": "0343",
    }
    for text, expected in examples.items():
        inferred = infer_series(_resume(("experience", text)), FALLBACK_CATALOG)
        assert inferred and inferred[0].code == expected
        assert inferred[0].confidence == 80


def test_catalog_supports_unlisted_occupation_without_app_code_mapping():
    record = _resume(("experience", "Geologist, 2019-2025. Conducted geologic surveys."))
    inferred = infer_series(record, {"1350": "Geologist"})
    assert inferred[0].code == "1350"


def test_skills_or_collaboration_alone_do_not_infer_series():
    record = _resume(("skills", "Registered Nurse; Civil Engineer; Program Analyst"), ("experience", "Collaborated with registered nurses on a software project."))
    assert infer_series(record, FALLBACK_CATALOG) == ()


def test_multiple_confident_series_not_capped_at_two_but_still_bounded():
    titles = ["Registered Nurse", "Accountant", "Civil Engineer", "IT Specialist"]
    record = _resume(*(("experience", f"{title}, 2019-2025. Performed the role.") for title in titles))
    codes = [candidate.code for candidate in infer_series(record, FALLBACK_CATALOG)]
    assert len(codes) == 4
    assert len(codes) <= MAX_CONFIDENT_SERIES
    assert set(codes) == {"0610", "0510", "0810", "2210"}


def test_physician_assistant_does_not_infer_physician():
    record = _resume(("experience", "Physician Assistant, 2019-2025. Performed patient assessments."))
    assert [candidate.code for candidate in infer_series(record, FALLBACK_CATALOG)] == ["0603"]
