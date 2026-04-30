from __future__ import annotations

import logging
from pathlib import Path

from literature_scout.collectors import arxiv_collector, rss_collector, scholar_email_collector
from literature_scout.models import Paper, deduplicate_papers
from literature_scout.resolvers.pdf_resolver import download_pdf, resolve_pdf_url
from literature_scout.scoring import score_paper
from literature_scout.slack.post_literature_update import post_missing_pdf_request
from literature_scout.storage import ScoutStorage
from paperbrain.pipeline import process_pdf


logger = logging.getLogger(__name__)


def run_scout(config: dict, slack_client=None, storage: ScoutStorage | None = None, ingest_fn=process_pdf) -> dict:
    scout_config = config.get("literature_scout", {})
    storage = storage or ScoutStorage(scout_config.get("db_path", "data/literature_scout.sqlite"))
    candidates = collect_candidates(config)
    unique = deduplicate_papers(candidates)
    stats = {"collected": len(candidates), "unique": len(unique), "rejected": 0, "ingested": 0, "waiting_for_pdf": 0}

    for paper in unique:
        if storage.has_seen(paper):
            continue
        storage.upsert_paper(paper)
        matched_query = _matched_query(config, paper)
        decision = score_paper(paper, config, matched_query=matched_query)
        storage.upsert_paper(paper)
        if decision.decision == "skip":
            stats["rejected"] += 1
            continue

        pdf_url = resolve_pdf_url(paper, config)
        if pdf_url:
            paper.pdf_url = pdf_url
            paper.status = "pdf_found"
            storage.upsert_paper(paper)
            pdf_path = download_pdf(pdf_url, _download_dir(config), filename=_pdf_filename(paper))
            if pdf_path:
                paper.pdf_path = str(pdf_path)
                ingest_fn(Path(pdf_path), config)
                paper.status = "ingested"
                storage.upsert_paper(paper)
                stats["ingested"] += 1
                continue

        paper.status = "waiting_for_user_pdf"
        storage.upsert_paper(paper)
        if slack_client and scout_config.get("slack_channel"):
            thread_ts = post_missing_pdf_request(slack_client, scout_config["slack_channel"], paper)
            if thread_ts:
                storage.link_slack_thread(paper, scout_config["slack_channel"], thread_ts)
        stats["waiting_for_pdf"] += 1

    return stats


def collect_candidates(config: dict) -> list[Paper]:
    collectors = [arxiv_collector.collect]
    scout_config = config.get("literature_scout", {})
    if scout_config.get("scholar_email_mbox"):
        collectors.append(scholar_email_collector.collect)
    if scout_config.get("rss_feeds"):
        collectors.append(rss_collector.collect)

    papers: list[Paper] = []
    for collector in collectors:
        try:
            papers.extend(collector(config))
        except Exception as exc:
            logger.warning("Literature collector failed: %s", exc)
    return papers


def _download_dir(config: dict) -> Path:
    return Path(config.get("literature_scout", {}).get("pdf_download_dir", "data/literature_scout_pdfs"))


def _pdf_filename(paper: Paper) -> str:
    if paper.doi:
        return paper.doi.replace("/", "_").replace(":", "_") + ".pdf"
    if paper.arxiv_id:
        return paper.arxiv_id.replace("/", "_") + ".pdf"
    return paper.title_hash()[:16] + ".pdf"


def _matched_query(config: dict, paper: Paper) -> str:
    queries = config.get("literature_scout", {}).get("arxiv", {}).get("queries")
    if not queries:
        queries = config.get("literature_scout", {}).get("queries", [])
    return "; ".join(queries)
