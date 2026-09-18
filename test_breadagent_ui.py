"""BreadAgent's V4 résumé entry stays plain-text only."""

from contextlib import nullcontext

import app
from resume_ingest import parse_pasted_resume


def test_resume_screen_shows_only_pasted_text(monkeypatch):
    class StreamlitStub:
        session_state = {}
        label = None

        def header(self, value):
            pass

        def write(self, value):
            pass

        def info(self, value):
            pass

        def form(self, key):
            return nullcontext()

        def text_area(self, label, **kwargs):
            self.label = label
            return ""

        def form_submit_button(self, label, **kwargs):
            return False

        def file_uploader(self, *args, **kwargs):
            raise AssertionError("File upload must not be exposed in the V4 UI")

    stub = StreamlitStub()
    monkeypatch.setattr(app, "st", stub)
    app._resume_screen()
    assert stub.label == "Paste your résumé text"


def test_pasted_resume_does_not_require_clamav_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CLAMAV_HOST", raising=False)
    resume = parse_pasted_resume(
        "Experience\nRegistered Nurse, January 2020 - Present\n"
        "Performed patient assessment, nursing care, medication administration, "
        "care planning, and clinical documentation in a hospital."
        "\nLicenses\nActive registered nurse license.",
        "test-owner",
    )
    assert resume.evidence
