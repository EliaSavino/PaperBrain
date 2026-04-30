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
import random
import re
from pathlib import Path
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from .config import load_config, setup_logging
from .pipeline import process_doi, process_pdf, extract_doi_from_message, PipelineError
from .slack.files import download_slack_pdf
from .summarizer import (
    format_slack_chem_summary,
    format_slack_ml_summary,
    format_slack_combined_summary,
    quick_slack_reply,
)

logger = logging.getLogger(__name__)

_HELP_TEXT = (
    "Hey! Here's what I can do:\n\n"
    "• *Paste a DOI or paper URL* — I'll fetch, summarize it!\n"
    "  e.g. `10.1021/jacs.3c01234` or `https://doi.org/10.1021/jacs.3c01234`\n\n"
    "• *Attach a PDF* — perfect for paywalled papers I can't grab myself\n\n"
    "• Add `for elia` or `ml summary` to get the ML/computational angle instead of chemistry\n\n"
    "• Add `for all`, `both`, or `ml and chem` to get both passes in one reply\n\n"
    "Works in DMs and in any channel you @mention me."
)

_DM_VIBES_TEXT = (
    "My friend, not vibes 😅\n\n"
    "Send me one of these:\n"
    "• A *DOI* or paper URL — e.g. `10.1021/jacs.3c01234`\n"
    "• A *PDF attachment* — great for paywalled papers\n\n"
    "That's literally it!"
)

_REFUSAL_TEXT = (
    "Nice try 😌 I can't help with secrets, tokens, local files, hidden prompts, or command-style requests.\n\n"
    "I *can* help with paper summaries, DOI links, PDFs, and quick paper-related questions."
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


def is_all_request(text: str) -> bool:
    """Check if message explicitly requests both chemistry and ML passes."""
    patterns = [
        r"\bfor all\b",
        r"\bboth\b",
        r"\bboth passes\b",
        r"\bboth summaries\b",
        r"\ball passes\b",
        r"\ball summaries\b",
        r"\bml and chem\b",
        r"\bchem and ml\b",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def wants_help(text: str) -> bool:
    """Check if message is explicitly asking for help/instructions."""
    return bool(re.search(r"\bhelp\b", text, re.IGNORECASE))


def get_summary_mode(text: str) -> str:
    """Resolve request mode from message text."""
    if is_all_request(text):
        return "all"
    if is_ml_request(text):
        return "ml"
    return "chem"


def strip_bot_mention(text: str, bot_user_id: str | None) -> str:
    """Remove the bot mention token so fallback chat sees just the user text."""
    if bot_user_id:
        text = text.replace(f"<@{bot_user_id}>", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_disallowed_request(text: str) -> bool:
    """Reject attempts to extract secrets, internal instructions, or local system data."""
    patterns = [
        r"ignore (all )?(previous|prior) instructions",
        r"forget (all )?(previous|prior) instructions",
        r"\bsystem prompt\b",
        r"\bhidden prompt\b",
        r"\binternal prompt\b",
        r"\breveal .*prompt\b",
        r"\bsecrets?\b",
        r"\bpasswords?\b",
        r"\bapi[- ]?keys?\b",
        r"\btokens?\b",
        r"\bcredentials?\b",
        r"\benv vars?\b",
        r"\benvironment variables?\b",
        r"\bconfig\b",
        r"\blogs?\b",
        r"\blocal files?\b",
        r"\bfilesystem\b",
        r"\byour pc\b",
        r"\byour machine\b",
        r"\bcomputer\b",
        r"\bssh\b",
        r"\bterminal\b",
        r"\bshell\b",
        r"\brun command\b",
        r"\bexecute\b",
        r"\bcat\s+/",
        r"\bls\s+/",
        r"\bread .*file\b",
        r"\bshow me\b.*\b(secret|token|password|config|prompt|file)\b",
        r"\bgive me\b.*\b(secret|token|password|config|prompt|file)\b",
    ]
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns)


def is_watched_channel(channel_id: str, config: dict) -> bool:
    return channel_id in config["slack"]["watched_channels"]


_REACT_EMOJIS = [
    "eyes", "brain", "nerd_face", "microscope", "dna",
    "test_tube", "mag", "rocket", "fire", "exploding_head",
]

def _react(client, channel_id: str, timestamp: str, emoji: str = None):
    """Add an emoji reaction to a Slack message. Silently ignores failures."""
    if emoji is None:
        emoji = random.choice(_REACT_EMOJIS)
    try:
        client.reactions_add(channel=channel_id, timestamp=timestamp, name=emoji)
    except Exception as e:
        logger.warning(f"Could not add reaction: {e}")


def _format_summary(summary, mode: str) -> str:
    if mode == "all":
        return format_slack_combined_summary(summary)
    if mode == "ml":
        return format_slack_ml_summary(summary)
    return format_slack_chem_summary(summary)


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

        mode = get_summary_mode(text)

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

        # No DOI/PDF. If they asked for help, show instructions; otherwise do a
        # quick conversational pass and then follow with instructions.
        cleaned_text = strip_bot_mention(text, bot_user_id)
        if cleaned_text.lower() == "scout":
            try:
                from literature_scout.pipeline import run_scout

                stats = run_scout(config, slack_client=client)
                reply(
                    "Literature scout run complete: "
                    f"{stats['collected']} collected, {stats['ingested']} ingested, "
                    f"{stats['waiting_for_pdf']} waiting for PDFs."
                )
            except Exception as e:
                logger.exception(f"Literature scout run failed: {e}")
                reply("❌ Literature scout run failed — check the logs.")
            return
        if not cleaned_text or wants_help(cleaned_text):
            reply(_HELP_TEXT)
            return
        if is_disallowed_request(cleaned_text):
            reply(_REFUSAL_TEXT)
            reply(_HELP_TEXT)
            return

        quick_reply = quick_slack_reply(cleaned_text, config)
        if quick_reply:
            reply(quick_reply)
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

        # Literature scout thread replies are owned by the addon. If a user
        # supplies a PDF in one of those threads, dispatch it into process_pdf.
        try:
            from literature_scout.slack.pdf_reply_handler import handle_pdf_reply

            if handle_pdf_reply(event, client, config):
                return
        except Exception as e:
            logger.exception(f"Literature scout PDF reply handling failed: {e}")

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
                _handle_doi(doi, say, config, mode=get_summary_mode(text) if is_all_request(text) else "ml")
                return

            # Text with no DOI and no PDF
            if wants_help(text):
                say(_HELP_TEXT)
            elif is_disallowed_request(text):
                say(_REFUSAL_TEXT)
            else:
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

            mode = get_summary_mode(text)
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
        msg = _format_summary(summary, mode)
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
        tmp_path = download_slack_pdf(file, config["slack"]["bot_token"])
        try:
            paper, summary = process_pdf(tmp_path, config)
            say(_format_summary(summary, mode))
        finally:
            tmp_path.unlink(missing_ok=True)

    except ValueError:
        say("❌ Couldn't get the download URL for that PDF — try re-uploading it.")
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
                say(_format_summary(summary, "ml"))
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
