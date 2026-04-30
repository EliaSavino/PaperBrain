import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class FakeSlackClient:
    def __init__(self):
        self.posted_messages = []
        self.reactions = []

    def auth_test(self):
        return {"user_id": "UBOT"}

    def chat_postMessage(self, **kwargs):
        self.posted_messages.append(kwargs)
        return {"ok": True}

    def reactions_add(self, **kwargs):
        self.reactions.append(kwargs)
        return {"ok": True}


class FakeApp:
    def __init__(self, token=None):
        self.token = token
        self.client = FakeSlackClient()
        self.handlers = {}

    def event(self, name):
        def decorator(func):
            self.handlers[name] = func
            return func

        return decorator


class FakeSocketModeHandler:
    def __init__(self, app, token):
        self.app = app
        self.token = token

    def start(self):
        return None


def install_dependency_stubs():
    slack_bolt = types.ModuleType("slack_bolt")
    slack_bolt.App = FakeApp

    slack_bolt_adapter = types.ModuleType("slack_bolt.adapter")
    slack_bolt_socket_mode = types.ModuleType("slack_bolt.adapter.socket_mode")
    slack_bolt_socket_mode.SocketModeHandler = FakeSocketModeHandler

    ollama = types.ModuleType("ollama")
    ollama.chat = lambda *args, **kwargs: {"message": {"content": ""}}

    fitz = types.ModuleType("fitz")
    fitz.open = lambda *args, **kwargs: None

    cloudscraper = types.ModuleType("cloudscraper")
    cloudscraper.create_scraper = lambda: types.SimpleNamespace(get=lambda *a, **k: None)

    sys.modules["slack_bolt"] = slack_bolt
    sys.modules["slack_bolt.adapter"] = slack_bolt_adapter
    sys.modules["slack_bolt.adapter.socket_mode"] = slack_bolt_socket_mode
    sys.modules["ollama"] = ollama
    sys.modules["fitz"] = fitz
    sys.modules["cloudscraper"] = cloudscraper


def import_fresh(module_name):
    install_dependency_stubs()
    for name in [
        module_name,
        "slack_bot",
        "paperbrain.slack_bot",
        "summarizer",
        "paperbrain.summarizer",
        "paper_fetcher",
        "paperbrain.paper_fetcher",
        "pipeline",
        "paperbrain.pipeline",
        "obsidian_writer",
        "paperbrain.obsidian_writer",
        "paperbrain.config",
        "paperbrain.slack.files",
    ]:
        sys.modules.pop(name, None)
    return importlib.import_module(module_name)
