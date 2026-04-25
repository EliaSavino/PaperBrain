"""
slack_bot.py
PaperBrain Slack bot.

Behaviors:
- DM to bot: process any DOI or PDF attachment → ML summary reply + Obsidian note
- Channel message containing DOI: reply in thread with chem summary + Obsidian note
- "@paperbrain summarize for elia" in channel: reply with ML summary instead
- PDF attached in DM: process and summarize
- @PaperBot mention with PDF in any channel: download and summarize the PDF, reply in thread
- @PaperBot mention with DOI (no PDF): fetch and summarize, reply in thread
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

_HELP_TEXT = (
    "Hey! Here's what I can do:\n\n"
    "• *Paste a DOI or paper URL* — I'll fetch, summarize it!\n"
    "  e.g. `10.1021/jacs.3c01234` or `https://doi.org/10.1021/jacs.3c01234`\n\n"
    "• *Attach a PDF* — perfect for paywalled papers I can't grab myself\n\n"
    "• Add `for elia` or `ml summary` to get the ML/computational angle instead of chemistry\n\n"
    "Works in DMs and in any channel you @mention me."
)

_DM_VIBES_TEXT = (
    "My friend, not vibes 😅\n\n"
    "Send me one of these:\n"
    "• A *DOI* or paper URL — e.g. `10.1021/jacs.3c01234`\n"
    "• A *PDF attachment* — great for paywalled papers\n\n"
    "That's literally it!"
)


def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config" / "config.secret.work.yaml"
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


def _react(client, channel_id: str, timestamp: str, emoji: str = "thumbsup"):
    """Add an emoji reaction to a Slack message. Silently ignores failures."""
    try:
        client.reactions_add(channel=channel_id, timestamp=timestamp, name=emoji)
    except Exception as e:
        logger.warning(f"Could not add reaction: {e}")


def create_app(config: dict) -> App:
    app = App(token=config["slack"]["bot_token"])

    # Fetch the bot's own user ID so we can skip @mention messages in the
    # message handler (they're handled by the app_mention handler instead).
    bot_user_id = None
    try:
        auth_result = app.client.auth_test()
        bot_user_id = auth_result.get("user_id")
        logger.info(f"Bot user ID: {bot_user_id}")
    except Exception as e:
        logger.warning(f"Could not fetch bot user ID: {e}")

    # ─────────────────────────────────────────────────────
    # app_mention handler — @PaperBot with PDF or DOI in any channel
    # ─────────────────────────────────────────────────────
    @app.event("app_mention")
    def handle_mention(event, say, client, logger):
        logger.info(f"App mention: {event}")
        channel_id = event.get("channel")
        text = event.get("text", "") or ""
        files = event.get("files", [])
        msg_ts = event.get("ts")
        thread_ts = event.get("thread_ts") or msg_ts

        def reply(msg, **kw):
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=msg,
                **kw
            )

        mode = "ml" if is_ml_request(text) else "chem"

        # PDF attachment → react, then download and process
        pdf_files = [f for f in files if f.get("mimetype") == "application/pdf"]
        if pdf_files:
            _react(client, channel_id, msg_ts)
            for pdf_file in pdf_files:
                _handle_pdf_file(pdf_file, client, reply, config, mode=mode)
            return

        # DOI in text → react, then fetch and summarize
        doi = extract_doi_from_message(text)
        if doi:
            _react(client, channel_id, msg_ts)
            _handle_doi(doi, reply, config, mode=mode)
            return

        # No actionable content → helpful rundown
        reply(_HELP_TEXT)

    # ─────────────────────────────────────────────────────
    # DM handler — process DOI or PDF from Elia directly
    # ─────────────────────────────────────────────────────
    @app.event("message")
    def handle_message(event, say, client, logger):
        logger.info(f"Message: {event}")
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
            msg_ts = event.get("ts")

            # PDF attachment in DM
            if files:
                for file in files:
                    if file.get("mimetype") == "application/pdf":
                        _react(client, channel_id, msg_ts)
                        _handle_pdf_file(file, client, say, config, mode="ml")
                        return

            # DOI in DM text
            doi = extract_doi_from_message(text)
            if doi:
                _react(client, channel_id, msg_ts)
                _handle_doi(doi, say, config, mode="ml")
                return

            # Text with no DOI and no PDF
            say(_DM_VIBES_TEXT)
            return

        # ── Watched channel handling ──────────────────────
        if is_watched_channel(channel_id, config):
            # @mention messages are handled by the app_mention handler above;
            # skip them here to avoid processing the same message twice.
            if bot_user_id and f"<@{bot_user_id}>" in text:
                return

            doi = extract_doi_from_message(text)
            if not doi:
                return   # no DOI, ignore

            mode = "ml" if is_ml_request(text) else "chem"
            msg_ts = event.get("ts")
            thread_ts = event.get("thread_ts") or msg_ts

            _react(client, channel_id, msg_ts)
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
        # Warn clearly when we only had the abstract (paywalled paper)
        if not paper.full_text:
            msg = (
                "🔒 *DOI found but no open-access PDF* — summary is from the abstract only.\n"
                "_Got the PDF? Mention me and attach it for a fuller summary._\n\n"
            ) + msg
        say(msg)
    except PipelineError as e:
        err = str(e)
        logger.error(f"Pipeline error for DOI {doi}: {e}")
        if "Could not fetch paper" in err:
            say(
                f"🤔 That DOI didn't come back from CrossRef mate — is `{doi}` correct?\n"
                "Double-check for typos, or try pasting the full paper URL."
            )
        elif "Summarization failed" in err:
            say(f"❌ Found the paper but summarization crashed — check the logs.")
        else:
            say(f"❌ Something went wrong with `{doi}`: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error for DOI {doi}: {e}")
        say(f"❌ Something went badly wrong processing `{doi}` — check the logs.")


def _handle_pdf_file(file: dict, client, say, config: dict, mode: str = "ml"):
    """Download and process a PDF file from Slack."""
    try:
        url = file.get("url_private_download")
        if not url:
            say("❌ Couldn't get the download URL for that PDF — try re-uploading it.")
            return

        headers = {"Authorization": f"Bearer {config['slack']['bot_token']}"}
        import requests
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

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
        say("❌ Something went wrong reading that PDF — is it a valid, non-encrypted PDF?")


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
