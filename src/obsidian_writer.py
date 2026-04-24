"""
obsidian_writer.py
Writes paper summaries to Obsidian vault as markdown notes with frontmatter.
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from paper_fetcher import PaperMetadata
from summarizer import PaperSummary

logger = logging.getLogger(__name__)


def _safe_filename(title: str, doi: str) -> str:
    """Generate a safe filename from paper title."""
    # Use title, sanitize, truncate
    safe = re.sub(r'[^\w\s-]', '', title)
    safe = re.sub(r'\s+', '-', safe.strip())
    safe = safe[:80]   # keep filenames reasonable
    return safe


def _format_authors_yaml(authors: list[str]) -> str:
    """Format authors list for YAML frontmatter."""
    if not authors:
        return "  - Unknown"
    return "\n".join(f"  - {a}" for a in authors)


def build_note_content(paper: PaperMetadata, summary: PaperSummary, config: dict) -> str:
    """Build full Obsidian markdown note content."""
    now = datetime.now().strftime("%Y-%m-%d")
    tags = summary.tags + [config["obsidian"]["inbox_tag"]]
    tags_yaml = "\n".join(f"  - {t}" for t in tags)

    # Relevance emoji
    score_emoji = {1: "⚪", 2: "🟡", 3: "🟠", 4: "🔴", 5: "🔥"}.get(summary.relevance_score, "⚪")

    note = f"""---
title: "{summary.title.replace('"', "'")}"
authors:
{_format_authors_yaml(paper.authors)}
journal: "{summary.journal}"
year: {summary.year or 'null'}
doi: "{summary.doi}"
date_added: "{now}"
open_access: {str(paper.is_open_access).lower()}
relevance_score: {summary.relevance_score}
tags:
{tags_yaml}
---

# {summary.title}

> **{summary.authors_short}** | {summary.journal} | {summary.year} | [{summary.doi}](https://doi.org/{summary.doi})

---

## 🧪 Chemistry Summary

**TL;DR:** {summary.chem_one_liner}

**What they did:** {summary.chem_what}

**Key finding:** {summary.chem_finding}

**Method:** {summary.chem_method}

**Why it matters:** {summary.chem_relevance}

---

## 🤖 ML / Computational Angle

**Problem:** {summary.ml_problem}

**Method:** {summary.ml_method}

**Data:** {summary.ml_dataset}

**Results:** {summary.ml_result}

**Interesting angle:** {summary.ml_angle}

**Limitations:** {summary.ml_limitations}

{score_emoji} **RoboChem relevance:** {summary.relevance_score}/5

---

## 📝 My Notes

<!-- Your notes go here -->

---

## 🔗 Related

<!-- Obsidian links to related papers/concepts go here -->
<!-- e.g. [[Bayesian Optimization for Flow Chemistry]] -->

"""
    return note


def write_note(paper: PaperMetadata, summary: PaperSummary, config: dict) -> Path:
    """
    Write note to Obsidian vault. Returns the path of the created file.
    """
    vault_path = Path(config["obsidian"]["vault_path"])
    papers_folder = vault_path / config["obsidian"]["papers_folder"]
    papers_folder.mkdir(parents=True, exist_ok=True)

    filename = _safe_filename(summary.title, summary.doi) + ".md"
    note_path = papers_folder / filename

    # Don't overwrite existing notes (user may have added their own notes)
    if note_path.exists():
        logger.warning(f"Note already exists, skipping: {note_path}")
        return note_path

    content = build_note_content(paper, summary, config)

    with open(note_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"Written Obsidian note: {note_path}")
    return note_path
