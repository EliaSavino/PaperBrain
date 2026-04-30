import tempfile
from pathlib import Path

import requests


def download_slack_pdf(file: dict, bot_token: str) -> Path:
    url = file.get("url_private_download")
    if not url:
        raise ValueError("Slack file is missing url_private_download")

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {bot_token}"},
        timeout=30,
    )
    response.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(response.content)
        return Path(tmp.name)

