from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date

import requests

from literature_scout.models import Paper


logger = logging.getLogger(__name__)
ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def collect(config: dict) -> list[Paper]:
    scout_config = config.get("literature_scout", {})
    arxiv_config = scout_config.get("arxiv", {})
    queries = arxiv_config.get("queries") or scout_config.get("queries") or [
        '"self-driving lab" OR "Bayesian optimization" OR "active learning" chemistry'
    ]
    max_results = int(arxiv_config.get("max_results", scout_config.get("max_results", 25)))
    papers: list[Paper] = []
    for query in queries:
        papers.extend(fetch_arxiv(query, max_results=max_results))
    return papers


def fetch_arxiv(query: str, max_results: int = 25, session=requests) -> list[Paper]:
    response = session.get(
        ARXIV_API_URL,
        params={
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        timeout=30,
    )
    response.raise_for_status()
    return parse_arxiv_response(response.text)


def parse_arxiv_response(xml_text: str) -> list[Paper]:
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        source_url = _text(entry, f"{ATOM}id")
        arxiv_id = _arxiv_id_from_url(source_url)
        pdf_url = None
        canonical_url = source_url
        for link in entry.findall(f"{ATOM}link"):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href")
            if link.attrib.get("rel") == "alternate":
                canonical_url = link.attrib.get("href")
        doi = _text(entry, f"{ARXIV}doi")
        published = _parse_date(_text(entry, f"{ATOM}published"))
        papers.append(
            Paper(
                title=_clean_text(_text(entry, f"{ATOM}title") or "Unknown title"),
                authors=[
                    _clean_text(_text(author, f"{ATOM}name") or "")
                    for author in entry.findall(f"{ATOM}author")
                    if _text(author, f"{ATOM}name")
                ],
                abstract=_clean_text(_text(entry, f"{ATOM}summary") or "") or None,
                doi=doi,
                arxiv_id=arxiv_id,
                source="arxiv",
                source_url=source_url,
                canonical_url=canonical_url,
                published_date=published,
                pdf_url=pdf_url,
                pdf_path=None,
                relevance_score=None,
                relevance_reason=None,
                summary=None,
                summary_type="metadata_only",
                status="candidate",
            )
        )
    return papers


def _text(node, path: str) -> str | None:
    found = node.find(path)
    return found.text if found is not None else None


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _arxiv_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/").split("/")[-1]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None

