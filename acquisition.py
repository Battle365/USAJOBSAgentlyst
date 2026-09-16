from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests


MAX_ANNOUNCEMENT_CHARS = 250_000
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
USAJOBS_HOSTS = {"www.usajobs.gov", "usajobs.gov"}


class AnnouncementInputError(ValueError):
    pass


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        elif tag in {"p", "div", "section", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        elif tag in {"p", "div", "section", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(part.strip() for part in self.parts if part.strip())).strip()


def validate_usajobs_url(url: str) -> str:
    candidate = url.strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in USAJOBS_HOSTS or not parsed.path:
        raise AnnouncementInputError("Enter an HTTPS URL on the official usajobs.gov website.")
    return candidate


def fetch_announcement_text(url: str, request_get=requests.get) -> str:
    current = validate_usajobs_url(url)
    try:
        for _ in range(4):
            response = request_get(current, timeout=20, allow_redirects=False, stream=True, headers={"User-Agent": "USAJOBSAgent-V3 local matcher"})
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location", "")
                current = validate_usajobs_url(urljoin(current, location))
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                raise AnnouncementInputError("The USAJOBS URL did not return an HTML announcement.")
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_DOWNLOAD_BYTES:
                    raise AnnouncementInputError("The announcement page is too large to process safely.")
            parser = _ReadableHTML()
            parser.feed(bytes(data).decode(response.encoding or "utf-8", errors="replace"))
            text = parser.text()
            if len(re.sub(r"\s+", "", text)) < 200:
                raise AnnouncementInputError("The URL did not provide enough readable announcement text. Paste the announcement text instead.")
            return text[:MAX_ANNOUNCEMENT_CHARS]
        raise AnnouncementInputError("The USAJOBS URL redirected too many times.")
    except AnnouncementInputError:
        raise
    except requests.RequestException as exc:
        raise AnnouncementInputError("The USAJOBS page could not be retrieved. Paste the announcement text instead.") from exc


def build_manual_vacancy(title: str, agency: str, announcement_text: str, official_url: str = "") -> dict:
    text = re.sub(r"\r\n?", "\n", announcement_text).strip()
    if len(re.sub(r"\s+", "", text)) < 200:
        raise AnnouncementInputError("Paste enough announcement text to include qualifications and specialized experience.")
    if len(text) > MAX_ANNOUNCEMENT_CHARS:
        raise AnnouncementInputError("The pasted announcement exceeds the supported size limit.")
    url = validate_usajobs_url(official_url) if official_url.strip() else ""
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    identifier = f"manual-{source_hash[:16]}"
    return {
        "id": identifier, "control_number": identifier, "position_id": "", "url": url,
        "title": title.strip() or "Pasted USAJOBS announcement",
        "agency": agency.strip() or "Agency not supplied", "organization": agency.strip() or "Agency not supplied",
        "locations": ["See announcement"], "location": "See announcement", "remote": False,
        "open_date": "", "close_date": "", "series": "", "grades": [], "pay_range": "",
        "appointment_type": "", "schedule": "", "promotion_potential": "",
        "eligibility": _section(text, "who may apply", "qualifications"),
        "qualifications": text,
        "specialized_experience": _section(text, "specialized experience", "education"),
        "duties": _section(text, "duties", "requirements"),
        "competencies": _section(text, "how you will be evaluated", "required documents"),
        "education": _section(text, "education", "additional information"),
        "conditions": _section(text, "conditions of employment", "qualifications"),
        "summary": "Manually supplied USAJOBS announcement.",
        "retrieved_at": datetime.now(UTC).isoformat(), "source_hash": source_hash,
    }


def acquire_announcement(title: str, agency: str, pasted_text: str, official_url: str, request_get=requests.get) -> dict:
    text = pasted_text.strip()
    if not text:
        if not official_url.strip():
            raise AnnouncementInputError("Paste announcement text or provide an official USAJOBS URL.")
        text = fetch_announcement_text(official_url, request_get=request_get)
    return build_manual_vacancy(title, agency, text, official_url)


def _section(text: str, start_heading: str, end_heading: str) -> str:
    headings = (
        "summary", "duties", "requirements", "conditions of employment", "qualifications",
        "specialized experience", "education", "how you will be evaluated", "required documents",
        "how to apply", "additional information", "who may apply",
    )
    next_heading = "|".join(re.escape(value) for value in headings if value != start_heading.lower())
    match = re.search(rf"(?is)(?:^|\n)\s*{re.escape(start_heading)}\s*:?\s*\n?(.*?)(?=\n\s*(?:{next_heading})\s*:?\s*(?:\n|$)|\Z)", text)
    return match.group(1).strip() if match else ""
