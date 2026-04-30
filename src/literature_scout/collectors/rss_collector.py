from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime

import requests

from literature_scout.models import Paper


ATOM = "{http://www.w3.org/2005/Atom}"


def collect(config: dict) -> list[Paper]:
    feeds = config.get("literature_scout", {}).get("rss_feeds", [])
    papers: list[Paper] = []
    for url in feeds:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        papers.extend(parse_feed(response.text, source_url=url))
    return papers


def parse_feed(xml_text: str, source_url: str | None = None) -> list[Paper]:
    root = ET.fromstring(xml_text)
    if root.tag.endswith("feed"):
        return _parse_atom(root, source_url)
    return _parse_rss(root, source_url)


def _parse_atom(root, source_url: str | None) -> list[Paper]:
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        title = _text(entry, f"{ATOM}title") or "Unknown title"
        link = _link(entry) or source_url
        papers.append(_paper(title, _text(entry, f"{ATOM}summary"), link, source_url, _date(_text(entry, f"{ATOM}published"))))
    return papers


def _parse_rss(root, source_url: str | None) -> list[Paper]:
    papers = []
    for item in root.findall("./channel/item"):
        title = item.findtext("title") or "Unknown title"
        link = item.findtext("link") or source_url
        abstract = item.findtext("description")
        papers.append(_paper(title, abstract, link, source_url, _date(item.findtext("pubDate"))))
    return papers


def _paper(title: str, abstract: str | None, link: str | None, source_url: str | None, published: date | None) -> Paper:
    return Paper(
        title=" ".join(title.split()),
        authors=[],
        abstract=" ".join(abstract.split()) if abstract else None,
        doi=None,
        arxiv_id=None,
        source="rss",
        source_url=source_url,
        canonical_url=link,
        published_date=published,
        pdf_url=link if link and link.lower().endswith(".pdf") else None,
        pdf_path=None,
        relevance_score=None,
        relevance_reason=None,
        summary=None,
        summary_type="metadata_only",
        status="candidate",
    )


def _text(node, path: str) -> str | None:
    found = node.find(path)
    return found.text if found is not None else None


def _link(entry) -> str | None:
    for link in entry.findall(f"{ATOM}link"):
        if link.attrib.get("href"):
            return link.attrib["href"]
    return None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        if "T" in value:
            return date.fromisoformat(value[:10])
        return parsedate_to_datetime(value).date()
    except Exception:
        return None

