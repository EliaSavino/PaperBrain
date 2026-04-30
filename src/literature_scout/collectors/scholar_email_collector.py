from __future__ import annotations

import mailbox
import re

from literature_scout.models import Paper


def collect(config: dict) -> list[Paper]:
    mbox_path = config.get("literature_scout", {}).get("scholar_email_mbox")
    if not mbox_path:
        return []
    return parse_mbox(mbox_path)


def parse_mbox(path: str) -> list[Paper]:
    papers: list[Paper] = []
    for message in mailbox.mbox(path):
        subject = str(message.get("subject", "")).strip()
        if not subject:
            continue
        body = _body(message)
        url = _first_url(body)
        papers.append(
            Paper(
                title=re.sub(r"^Google Scholar Alert\s*[:-]\s*", "", subject, flags=re.I),
                authors=[],
                abstract=None,
                doi=None,
                arxiv_id=None,
                source="scholar_email",
                source_url=None,
                canonical_url=url,
                published_date=None,
                pdf_url=url if url and url.lower().endswith(".pdf") else None,
                pdf_path=None,
                relevance_score=None,
                relevance_reason=None,
                summary=None,
                summary_type="metadata_only",
                status="candidate",
            )
        )
    return papers


def _body(message) -> str:
    if message.is_multipart():
        parts = []
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
        return "\n".join(parts)
    payload = message.get_payload(decode=True)
    if not payload:
        return ""
    return payload.decode(message.get_content_charset() or "utf-8", errors="replace")


def _first_url(text: str) -> str | None:
    match = re.search(r"https?://\S+", text)
    return match.group(0).rstrip(").,;") if match else None

