from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

import requests

from literature_scout.models import Paper


logger = logging.getLogger(__name__)


def resolve_pdf_url(paper: Paper, config: dict) -> str | None:
    if paper.pdf_url:
        return paper.pdf_url
    if paper.arxiv_id:
        return f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
    if paper.doi:
        return resolve_unpaywall_pdf(paper.doi, config)
    if paper.canonical_url and paper.canonical_url.lower().endswith(".pdf"):
        return paper.canonical_url
    return None


def resolve_unpaywall_pdf(doi: str, config: dict) -> str | None:
    email = (
        config.get("literature_scout", {}).get("unpaywall_email")
        or config.get("unpaywall", {}).get("email")
    )
    if not email:
        return None
    url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}?email={quote(email)}"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    data = response.json()
    if not data.get("is_oa"):
        return None
    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf") or best.get("url")


def download_pdf(url: str, destination_dir: str | Path, filename: str | None = None) -> Path | None:
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    name = filename or _filename_from_url(url)
    path = destination / name
    response = requests.get(
        url,
        headers={"Accept": "application/pdf,*/*;q=0.9", "User-Agent": "PaperBrain literature scout"},
        timeout=45,
    )
    response.raise_for_status()
    content = response.content
    content_type = response.headers.get("content-type", "").lower()
    if not content.startswith(b"%PDF") and "pdf" not in content_type and not url.lower().endswith(".pdf"):
        logger.warning("Resolved URL did not look like a PDF: %s", url)
        return None
    path.write_bytes(content)
    return path


def _filename_from_url(url: str) -> str:
    stem = url.rstrip("/").split("/")[-1] or "paper.pdf"
    if not stem.lower().endswith(".pdf"):
        stem = f"{stem}.pdf"
    return stem.replace("?", "_")

