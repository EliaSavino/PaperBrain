from __future__ import annotations

import logging

from paperbrain.slack.files import download_slack_pdf
from literature_scout.storage import ScoutStorage
from paperbrain.pipeline import process_pdf


logger = logging.getLogger(__name__)


def handle_pdf_reply(event: dict, client, config: dict, storage: ScoutStorage | None = None, ingest_fn=process_pdf) -> bool:
    channel = event.get("channel")
    thread_ts = event.get("thread_ts")
    files = event.get("files") or []
    if not channel or not thread_ts or not files:
        return False

    storage = storage or ScoutStorage(_db_path(config))
    paper = storage.get_by_slack_thread(channel, thread_ts)
    if not paper:
        return False

    pdf_files = [file for file in files if file.get("mimetype") == "application/pdf"]
    if not pdf_files:
        return False

    for file in pdf_files:
        _download_and_ingest(file, client, config, ingest_fn)
    paper.status = "ingested"
    storage.upsert_paper(paper)
    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text="PDF received. I sent it through the existing PaperBrain ingestion pipeline.",
    )
    return True


def _download_and_ingest(file: dict, client, config: dict, ingest_fn):
    tmp_path = download_slack_pdf(file, config["slack"]["bot_token"])
    try:
        ingest_fn(tmp_path, config)
    finally:
        tmp_path.unlink(missing_ok=True)


def _db_path(config: dict) -> str:
    return config.get("literature_scout", {}).get("db_path", "data/literature_scout.sqlite")
