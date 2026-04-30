from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal


SummaryType = Literal["pdf", "abstract_only", "metadata_only"]
PaperStatus = Literal[
    "candidate",
    "rejected",
    "accepted",
    "pdf_found",
    "pdf_missing",
    "ingested",
    "waiting_for_user_pdf",
]


@dataclass
class Paper:
    title: str
    authors: list[str]
    abstract: str | None
    doi: str | None
    arxiv_id: str | None
    source: str
    source_url: str | None
    canonical_url: str | None
    published_date: date | None
    pdf_url: str | None
    pdf_path: str | None
    relevance_score: float | None
    relevance_reason: str | None
    summary: str | None
    summary_type: SummaryType
    status: PaperStatus

    def title_hash(self) -> str:
        normalized = normalize_title(self.title)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def dedupe_key(self) -> str:
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id.lower()}"
        return f"title:{self.title_hash()}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["published_date"] = self.published_date.isoformat() if self.published_date else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Paper":
        loaded = dict(data)
        if loaded.get("published_date") and not isinstance(loaded["published_date"], date):
            loaded["published_date"] = date.fromisoformat(loaded["published_date"])
        return cls(**loaded)


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", title.lower())).strip()


def deduplicate_papers(papers: list[Paper]) -> list[Paper]:
    seen: set[str] = set()
    unique: list[Paper] = []
    for paper in papers:
        keys = {
            f"doi:{paper.doi.lower()}" if paper.doi else None,
            f"arxiv:{paper.arxiv_id.lower()}" if paper.arxiv_id else None,
            f"title:{paper.title_hash()}",
        }
        if any(key in seen for key in keys if key):
            continue
        seen.update(key for key in keys if key)
        unique.append(paper)
    return unique

