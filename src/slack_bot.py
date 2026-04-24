"""
slack_bot.py
PaperBrain Slack bot.

Behaviors:
- DM to bot: process any DOI or PDF attachment → ML summary reply + Obsidian note
- Channel message containing DOI: reply in thread with chem summary + Obsidian note
- "@paperbrain summarize for elia" in channel: reply with ML summary instead
- PDF attached in DM: process and summarize
"""

import logging
import re
import os
import sys
import tempfile
import yaml
from pathlib import Path
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from pipeline import process_doi, process_pdf, extract_doi_from_message, PipelineError
from summarizer import format_slack_chem_summary, format_slack_ml_summary

logger = logging.getLogger(__name__)


def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    log_file = config["logging"]["log_file"]
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, config["logging"]["level"]),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ]
    )


def is_elia(user_id: str, config: dict) -> bool:
    return user_id == config["slack"]["elia_user_id"]


def is_ml_request(text: str) -> bool:
    """Check if message explicitly requests ML summary."""
    patterns = [
        r"summarize for elia",
        r"ml summary",
        r"machine learning summary",
        r"computational summary",
        r"for elia",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def is_watched_channel(channel_id: str, config: dict) -> bool:
    return channel_id in config["slack"]["watched_channels"]


def create_app(config: dict) -> App:
    app = App(token=config["slack"]["bot_token"])

    # ─────────────────────────────────────────────────────
    # DM handler — process DOI or PDF from Elia directly
    # ─────────────────────────────────────────────────────
    @app.event("message")
    def handle_message(event, say, client, logger):
        channel_type = event.get("channel_type")
        channel_id = event.get("channel")
        user_id = event.get("user")
        text = event.get("text", "") or ""
        files = event.get("files", [])
        subtype = event.get("subtype")

        # Ignore bot messages and edited messages
        if subtype in ("bot_message", "message_changed") or not user_id:
            return

        # ── DM handling ──────────────────────────────────
        if channel_type == "im":
            # PDF attachment in DM
            if files:
                for file in files:
                    if file.get("mimetype") == "application/pdf":
                        say("📄 Got your PDF, processing... this takes a minute ⏳")
                        _handle_pdf_file(file, client, say, config, mode="ml")
                        return

            # DOI in DM text
            doi = extract_doi_from_message(text)
            if doi:
                say(f"🔍 Found DOI `{doi}`, fetching and summarizing... ⏳")
                _handle_doi(doi, say, config, mode="ml")
                return

            # Unknown DM
            say(
                "Hi! Send me a DOI, a paper URL, or attach a PDF and I'll summarize it for you.\n"
                "Example: `10.1021/jacs.3c01234`"
            )
            return

        # ── Watched channel handling ──────────────────────
        if is_watched_channel(channel_id, config):
            doi = extract_doi_from_message(text)
            if not doi:
                return   # no DOI, ignore

            # Determine summary mode
            mode = "ml" if is_ml_request(text) else "chem"

            # Reply in thread
            thread_ts = event.get("thread_ts") or event.get("ts")
            _handle_doi(
                doi, 
                lambda msg, **kw: client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=msg,
                    **kw
                ),
                config,
                mode=mode,
            )

    return app


def _handle_doi(doi: str, say, config: dict, mode: str = "chem"):
    """Process a DOI and send summary via say()."""
    try:
        paper, summary = process_doi(doi, config)
        if mode == "ml":
            msg = format_slack_ml_summary(summary)
        else:
            msg = format_slack_chem_summary(summary)
        say(msg)
    except PipelineError as e:
        logger.error(f"Pipeline error for DOI {doi}: {e}")
        say(f"❌ Couldn't process DOI `{doi}`: {e}\nIs the DOI correct?")
    except Exception as e:
        logger.exception(f"Unexpected error for DOI {doi}: {e}")
        say(f"❌ Something went wrong processing `{doi}`. Check the logs.")


def _handle_pdf_file(file: dict, client, say, config: dict, mode: str = "ml"):
    """Download and process a PDF file from Slack."""
    try:
        # Download PDF from Slack
        url = file.get("url_private_download")
        if not url:
            say("❌ Couldn't get download URL for that PDF.")
            return

        headers = {"Authorization": f"Bearer {config['slack']['bot_token']}"}
        import requests
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        # Save to temp file and process
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = Path(tmp.name)

        try:
            paper, summary = process_pdf(tmp_path, config)
            if mode == "ml":
                msg = format_slack_ml_summary(summary)
            else:
                msg = format_slack_chem_summary(summary)
            say(msg)
        finally:
            tmp_path.unlink(missing_ok=True)

    except PipelineError as e:
        logger.error(f"Pipeline error for PDF: {e}")
        say(f"❌ Couldn't process that PDF: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error processing PDF: {e}")
        say("❌ Something went wrong with that PDF. Check the logs.")


# ─────────────────────────────────────────────────────────
# iCloud folder watcher (runs in separate thread)
# ─────────────────────────────────────────────────────────
def start_folder_watcher(config: dict, slack_client):
    """
    Watch the iCloud inbox folder for new PDFs.
    Processes them and posts summary to Elia's DM.
    """
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    import threading

    watch_folder = Path(config["inbox"]["pdf_watch_folder"])
    watch_folder.mkdir(parents=True, exist_ok=True)
    elia_user_id = config["slack"]["elia_user_id"]

    class PDFHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            path = Path(event.src_path)
            if path.suffix.lower() != ".pdf":
                return

            logger.info(f"New PDF in inbox: {path}")

            def say(msg, **kwargs):
                slack_client.chat_postMessage(
                    channel=elia_user_id,
                    text=msg,
                    **kwargs
                )

            say(f"📄 Picked up `{path.name}` from inbox, processing... ⏳")
            try:
                paper, summary = process_pdf(path, config)
                say(format_slack_ml_summary(summary))
            except PipelineError as e:
                say(f"❌ Couldn't process `{path.name}`: {e}")
            except Exception as e:
                logger.exception(f"Error processing inbox PDF {path}: {e}")
                say(f"❌ Something went wrong with `{path.name}`. Check logs.")

    handler = PDFHandler()
    observer = Observer()
    observer.schedule(handler, str(watch_folder), recursive=False)
    observer.start()
    logger.info(f"Watching iCloud inbox: {watch_folder}")
    return observer


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────
def main():
    config = load_config()
    setup_logging(config)
    logger.info("PaperBrain starting up...")

    app = create_app(config)

    # Start iCloud folder watcher in background
    observer = start_folder_watcher(config, app.client)

    try:
        # Socket Mode — no public URL needed, works behind Tailscale/firewall
        handler = SocketModeHandler(app, config["slack"]["app_token"])
        logger.info("PaperBrain online ✅")
        handler.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
