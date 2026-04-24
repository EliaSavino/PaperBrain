"""
pipeline.py
Orchestrates the full paper processing pipeline.
Input: DOI string or PDF path → Output: PaperSummary + Obsidian note written
"""

import logging
from pathlib import Path
from typing import Optional, Tuple
from paper_fetcher import PaperMetadata, fetch_paper, fetch_paper_from_pdf, extract_doi_from_text
from summarizer import PaperSummary, summarize_paper
from obsidian_writer import write_note

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    pass


def process_doi(doi: str, config: dict) -> Tuple[PaperMetadata, PaperSummary]:
    """
    Full pipeline for a DOI.
    Returns (metadata, summary) tuple.
    Raises PipelineError if anything critical fails.
    """
    logger.info(f"Pipeline starting for DOI: {doi}")

    # Fetch
    paper = fetch_paper(doi)
    if not paper:
        raise PipelineError(f"Could not fetch paper for DOI: {doi}")

    # Summarize
    summary = summarize_paper(paper, config)
    if not summary:
        raise PipelineError(f"Summarization failed for DOI: {doi}")

    # Write to Obsidian
    note_path = write_note(paper, summary, config)
    logger.info(f"Pipeline complete. Note at: {note_path}")

    return paper, summary


def process_pdf(pdf_path: Path, config: dict) -> Tuple[PaperMetadata, PaperSummary]:
    """
    Full pipeline for a local PDF file.
    Returns (metadata, summary) tuple.
    """
    logger.info(f"Pipeline starting for PDF: {pdf_path}")

    paper = fetch_paper_from_pdf(pdf_path)
    if not paper:
        raise PipelineError(f"Could not process PDF: {pdf_path}")

    summary = summarize_paper(paper, config)
    if not summary:
        raise PipelineError(f"Summarization failed for PDF: {pdf_path}")

    note_path = write_note(paper, summary, config)
    logger.info(f"Pipeline complete. Note at: {note_path}")

    # Move processed PDF to a 'processed' subfolder so inbox stays clean
    processed_dir = pdf_path.parent / "processed"
    processed_dir.mkdir(exist_ok=True)
    pdf_path.rename(processed_dir / pdf_path.name)
    logger.info(f"Moved PDF to processed: {processed_dir / pdf_path.name}")

    return paper, summary


def extract_doi_from_message(text: str) -> Optional[str]:
    """Extract DOI from a Slack message or arbitrary text."""
    return extract_doi_from_text(text)
