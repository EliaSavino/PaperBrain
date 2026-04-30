from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .models import Paper

try:
    import ollama
except ImportError:
    ollama = None


logger = logging.getLogger(__name__)


FILTER_SYSTEM_PROMPT = """You are a literature relevance filter for an automated chemistry research group.
This is only a pre-filter. Do not summarize the paper.
Target relevance:
- self-driving labs
- Bayesian optimization
- active learning
- robotic chemistry
- flow chemistry automation
- reaction optimization
- chemical analytics
- autonomous experimentation

Avoid ingesting:
- generic ML papers
- generic chemistry papers
- generic robotics papers

Return ONLY valid JSON with this shape:
{
  "decision": "ingest" | "skip",
  "score": 0,
  "reason": "short explanation",
  "topics": ["..."],
  "priority": "low" | "medium" | "high"
}

Rubric:
0 = irrelevant
1 = weakly related
2 = adjacent but not useful
3 = worth ingesting
4 = directly relevant
5 = must ingest"""


FILTER_USER_PROMPT = """Evaluate this candidate paper for ingestion.

Title: {title}
Authors: {authors}
Abstract: {abstract}
DOI: {doi}
arXiv ID: {arxiv_id}
Source: {source}
URL: {url}
Matched query: {matched_query}

Default decision rule: ingest if score >= 3, skip if score < 3."""


@dataclass
class RelevanceDecision:
    decision: str
    score: float
    reason: str
    topics: list[str]
    priority: str


def parse_relevance_response(raw: str) -> RelevanceDecision:
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = cleaned.replace("```", "").strip()
    data = json.loads(cleaned)
    score = float(data.get("score", 0))
    decision = data.get("decision") or ("ingest" if score >= 3 else "skip")
    if decision not in {"ingest", "skip"}:
        decision = "ingest" if score >= 3 else "skip"
    priority = data.get("priority") or "low"
    if priority not in {"low", "medium", "high"}:
        priority = "low"
    topics = data.get("topics") or []
    return RelevanceDecision(
        decision=decision,
        score=score,
        reason=str(data.get("reason", "")).strip(),
        topics=topics if isinstance(topics, list) else [],
        priority=priority,
    )


def score_paper(paper: Paper, config: dict, matched_query: str | None = None) -> RelevanceDecision:
    if ollama is None:
        raise RuntimeError("ollama is not installed; cannot run literature scout relevance filter")

    prompt = FILTER_USER_PROMPT.format(
        title=paper.title,
        authors=", ".join(paper.authors),
        abstract=paper.abstract or "",
        doi=paper.doi or "",
        arxiv_id=paper.arxiv_id or "",
        source=paper.source,
        url=paper.canonical_url or paper.source_url or "",
        matched_query=matched_query or "",
    )
    response = ollama.chat(
        model=config["ollama"]["model"],
        messages=[
            {"role": "system", "content": FILTER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        options={
            "num_ctx": config["ollama"]["num_ctx"],
            "temperature": 0,
        },
    )
    decision = parse_relevance_response(response["message"]["content"])
    paper.relevance_score = decision.score
    paper.relevance_reason = decision.reason
    paper.status = "accepted" if decision.decision == "ingest" else "rejected"
    logger.info("Scout relevance for %s: %s %.1f", paper.title, decision.decision, decision.score)
    return decision

