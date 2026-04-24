"""
summarizer.py
Generates paper summaries using local Ollama (qwen3:8b).
Two modes: synthetic chemistry (public/lab) and ML/computational (private/elia).
"""

import logging
import ollama
from dataclasses import dataclass
from typing import Optional
from paper_fetcher import PaperMetadata

logger = logging.getLogger(__name__)

# How much text to feed the model — qwen3:8b with 16k ctx
# We reserve ~4k for the prompt and output, leaving ~12k for paper text
MAX_PAPER_CHARS = 40000   # ~10k tokens, safe for 16k ctx window


@dataclass
class PaperSummary:
    # Shared fields
    title: str
    authors_short: str       # "Smith et al." or "Smith & Jones"
    year: Optional[int]
    journal: str
    doi: str

    # Chemistry summary (public, for lab Slack)
    chem_one_liner: str      # one sentence, plain english
    chem_what: str           # what did they do
    chem_finding: str        # key finding
    chem_method: str         # synthetic/experimental method used
    chem_relevance: str      # why should a synthetic chemist care

    # ML summary (private, for Elia)
    ml_problem: str          # what ML/computational problem does this address
    ml_method: str           # what approach/model/algorithm
    ml_dataset: str          # data used, scale, source
    ml_result: str           # quantitative results if any
    ml_angle: str            # interesting angle for an ML researcher
    ml_limitations: str      # honest limitations

    # Obsidian metadata
    relevance_score: int     # 1-5, how relevant to RoboChem/flow chem/ML for chemistry
    tags: list[str]          # suggested Obsidian tags


def _truncate_paper_text(paper: PaperMetadata) -> str:
    """
    Prepare paper text for the LLM. Uses full text if available,
    falls back to abstract. Truncates to fit context window.
    """
    if paper.full_text:
        text = paper.full_text
        if len(text) > MAX_PAPER_CHARS:
            # Take first 70% + last 30% — intro+methods and conclusions
            split = int(MAX_PAPER_CHARS * 0.7)
            text = text[:split] + "\n\n[...middle truncated...]\n\n" + text[-(MAX_PAPER_CHARS - split):]
        return text
    elif paper.abstract:
        return f"Abstract:\n{paper.abstract}\n\n(Full text not available)"
    else:
        return "(No text content available — metadata only)"


def _authors_short(authors: list[str]) -> str:
    """Format author list as 'Smith et al.' or 'Smith & Jones'."""
    if not authors:
        return "Unknown authors"
    if len(authors) == 1:
        return authors[0].split()[-1]   # last name only
    if len(authors) == 2:
        return f"{authors[0].split()[-1]} & {authors[1].split()[-1]}"
    return f"{authors[0].split()[-1]} et al."


CHEM_SYSTEM_PROMPT = """You are a research assistant helping synthetic chemists quickly understand papers.
Your summaries are concise, jargon-aware for chemistry but avoid unnecessary complexity.
You focus on: what was made or discovered, how (experimental methods), and why it matters to a lab chemist.
You always respond in valid JSON only. No preamble, no markdown fences, just the JSON object."""

CHEM_USER_PROMPT = """Summarize this chemistry paper for a synthetic chemistry research group.

Paper metadata:
Title: {title}
Authors: {authors}
Journal: {journal}
Year: {year}
DOI: {doi}

Paper content:
{text}

Respond with this exact JSON structure:
{{
  "one_liner": "One sentence summary a chemist can read in 5 seconds",
  "what": "What did they do? (2-3 sentences, focus on synthesis/reaction/discovery)",
  "finding": "Key finding or result (2-3 sentences, be specific)",
  "method": "Experimental/synthetic method used (1-2 sentences)",
  "relevance": "Why should a synthetic chemist care? (1-2 sentences)",
  "relevance_score": 3,
  "tags": ["flow-chemistry", "catalysis", "example-tag"]
}}

relevance_score: 1=not relevant to synthetic chemistry, 3=somewhat relevant, 5=highly relevant.
tags: suggest 3-6 Obsidian tags relevant to the paper content (lowercase, hyphenated)."""


ML_SYSTEM_PROMPT = """You are a research assistant helping an ML researcher who works on automated chemistry platforms (RoboChem).
They are interested in: Bayesian optimization, active learning, reaction optimization, self-driving labs,
machine learning for chemistry, flow chemistry automation, and chemical space exploration.
Your summaries are technical and ML-focused. You always respond in valid JSON only."""

ML_USER_PROMPT = """Summarize this paper from an ML/computational perspective for an ML researcher working on automated chemistry.

Paper metadata:
Title: {title}
Authors: {authors}
Journal: {journal}
Year: {year}
DOI: {doi}

Paper content:
{text}

Respond with this exact JSON structure:
{{
  "problem": "What ML/computational problem does this address? (2-3 sentences)",
  "method": "What approach, model, or algorithm? Be specific about architecture/method choices",
  "dataset": "Data used: scale, source, type, any preprocessing",
  "result": "Key quantitative results (metrics, benchmarks, comparisons)",
  "angle": "Most interesting thing for an ML researcher working on automated chemistry (2-3 sentences)",
  "limitations": "Honest limitations or open questions (1-2 sentences)",
  "relevance_score": 3,
  "tags": ["bayesian-optimization", "active-learning", "example-tag"]
}}

relevance_score: 1=not relevant to ML-for-chemistry, 3=somewhat relevant, 5=directly relevant to RoboChem-style work."""


def _call_ollama(system: str, prompt: str, config: dict) -> Optional[str]:
    """Call local Ollama with given prompts. Returns raw response text."""
    try:
        response = ollama.chat(
            model=config["ollama"]["model"],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            options={
                "num_ctx": config["ollama"]["num_ctx"],
                "temperature": config["ollama"]["temperature"],
            }
        )
        return response["message"]["content"]
    except Exception as e:
        logger.error(f"Ollama call failed: {e}")
        return None


def _parse_json_response(raw: str) -> Optional[dict]:
    """Parse JSON from model response, handling minor formatting issues."""
    import json
    import re

    # Strip any accidental markdown fences qwen sometimes adds
    cleaned = re.sub(r'```json\s*', '', raw)
    cleaned = re.sub(r'```\s*', '', cleaned)
    cleaned = cleaned.strip()

    # qwen3 thinking mode sometimes emits <think>...</think> blocks — strip them
    cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed: {e}\nRaw response:\n{raw[:500]}")
        return None


def summarize_paper(paper: PaperMetadata, config: dict) -> Optional[PaperSummary]:
    """
    Generate both chemistry and ML summaries for a paper.
    Runs two Ollama calls sequentially.
    """
    logger.info(f"Summarizing: {paper.title}")
    text = _truncate_paper_text(paper)
    authors_short = _authors_short(paper.authors)

    format_args = {
        "title": paper.title,
        "authors": ", ".join(paper.authors[:5]) + (" et al." if len(paper.authors) > 5 else ""),
        "journal": paper.journal or "Unknown journal",
        "year": paper.year or "Unknown year",
        "doi": paper.doi,
        "text": text,
    }

    # Chemistry summary
    logger.info("Running chemistry summary pass...")
    chem_raw = _call_ollama(CHEM_SYSTEM_PROMPT, CHEM_USER_PROMPT.format(**format_args), config)
    if not chem_raw:
        return None
    chem = _parse_json_response(chem_raw)
    if not chem:
        return None

    # ML summary
    logger.info("Running ML summary pass...")
    ml_raw = _call_ollama(ML_SYSTEM_PROMPT, ML_USER_PROMPT.format(**format_args), config)
    if not ml_raw:
        return None
    ml = _parse_json_response(ml_raw)
    if not ml:
        return None

    # Merge tags from both passes, deduplicate
    all_tags = list(set(chem.get("tags", []) + ml.get("tags", [])))

    return PaperSummary(
        title=paper.title,
        authors_short=authors_short,
        year=paper.year,
        journal=paper.journal or "",
        doi=paper.doi,
        chem_one_liner=chem.get("one_liner", ""),
        chem_what=chem.get("what", ""),
        chem_finding=chem.get("finding", ""),
        chem_method=chem.get("method", ""),
        chem_relevance=chem.get("relevance", ""),
        ml_problem=ml.get("problem", ""),
        ml_method=ml.get("method", ""),
        ml_dataset=ml.get("dataset", ""),
        ml_result=ml.get("result", ""),
        ml_angle=ml.get("angle", ""),
        ml_limitations=ml.get("limitations", ""),
        relevance_score=ml.get("relevance_score", chem.get("relevance_score", 3)),
        tags=all_tags,
    )


def format_slack_chem_summary(summary: PaperSummary) -> str:
    """Format chemistry summary for public Slack reply."""
    score_emoji = {1: "⚪", 2: "🟡", 3: "🟠", 4: "🔴", 5: "🔥"}.get(summary.relevance_score, "⚪")

    return (
        f"*{summary.title}*\n"
        f"_{summary.authors_short}, {summary.journal} ({summary.year})_\n"
        f"DOI: `{summary.doi}`\n\n"
        f"*TL;DR:* {summary.chem_one_liner}\n\n"
        f"*What they did:* {summary.chem_what}\n\n"
        f"*Key finding:* {summary.chem_finding}\n\n"
        f"*Method:* {summary.chem_method}\n\n"
        f"*Why it matters:* {summary.chem_relevance}\n\n"
        f"{score_emoji} Relevance score: {summary.relevance_score}/5"
    )


def format_slack_ml_summary(summary: PaperSummary) -> str:
    """Format ML summary for Elia's private Slack DM."""
    score_emoji = {1: "⚪", 2: "🟡", 3: "🟠", 4: "🔴", 5: "🔥"}.get(summary.relevance_score, "⚪")

    return (
        f"*{summary.title}*\n"
        f"_{summary.authors_short}, {summary.journal} ({summary.year})_\n"
        f"DOI: `{summary.doi}`\n\n"
        f"*Problem:* {summary.ml_problem}\n\n"
        f"*Method:* {summary.ml_method}\n\n"
        f"*Data:* {summary.ml_dataset}\n\n"
        f"*Results:* {summary.ml_result}\n\n"
        f"*Interesting angle:* {summary.ml_angle}\n\n"
        f"*Limitations:* {summary.ml_limitations}\n\n"
        f"{score_emoji} RoboChem relevance: {summary.relevance_score}/5\n"
        f"📝 Saved to Obsidian"
    )
