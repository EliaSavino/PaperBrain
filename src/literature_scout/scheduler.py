from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run the PaperBrain literature scout.")
    parser.add_argument("--config", default=None, help="Path to config YAML")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    from slack_bolt import App
    from paperbrain.slack_bot import load_config, setup_logging
    from .pipeline import run_scout

    config = load_config(args.config)
    setup_logging(config)
    interval_hours = float(config.get("literature_scout", {}).get("interval_hours", 24))
    slack_client = None
    if config.get("literature_scout", {}).get("slack_channel"):
        slack_client = App(token=config["slack"]["bot_token"]).client

    while True:
        stats = run_scout(config, slack_client=slack_client)
        logger.info("Literature scout run complete: %s", stats)
        if args.once:
            break
        time.sleep(interval_hours * 3600)


if __name__ == "__main__":
    main()
