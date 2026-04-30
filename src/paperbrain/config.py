import logging
import sys
from pathlib import Path

import yaml


def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parents[2] / "config" / "config.secret.work.yaml"
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
        ],
    )

