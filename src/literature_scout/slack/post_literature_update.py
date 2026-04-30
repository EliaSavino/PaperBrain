from __future__ import annotations

from literature_scout.models import Paper


def build_missing_pdf_message(paper: Paper) -> str:
    authors = ", ".join(paper.authors[:5]) + (" et al." if len(paper.authors) > 5 else "")
    return (
        "New paper found:\n\n"
        f"Title: {paper.title}\n"
        f"Authors: {authors or 'Unknown authors'}\n"
        f"Source: {paper.source}\n"
        f"Why relevant: {paper.relevance_reason or 'Matched the literature scout profile.'}\n\n"
        "I could not retrieve an open PDF.\n"
        "Reply in this thread with the PDF and I will summarise it."
    )


def post_missing_pdf_request(client, channel: str, paper: Paper) -> str:
    result = client.chat_postMessage(channel=channel, text=build_missing_pdf_message(paper))
    return result.get("ts")

