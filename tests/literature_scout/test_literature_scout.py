import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tests.support import SRC

import sys

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from literature_scout.collectors.arxiv_collector import parse_arxiv_response
from literature_scout.models import Paper, deduplicate_papers
from literature_scout.resolvers.pdf_resolver import resolve_pdf_url, resolve_unpaywall_pdf
from literature_scout.scoring import parse_relevance_response
from literature_scout.slack.post_literature_update import build_missing_pdf_message
from literature_scout.storage import ScoutStorage


def _paper(**overrides):
    data = {
        "title": "Bayesian optimization for robotic chemistry",
        "authors": ["Ada Lovelace", "Grace Hopper"],
        "abstract": "A self-driving laboratory optimizes reactions.",
        "doi": None,
        "arxiv_id": None,
        "source": "test",
        "source_url": "https://example.test/source",
        "canonical_url": "https://example.test/paper",
        "published_date": date(2026, 1, 1),
        "pdf_url": None,
        "pdf_path": None,
        "relevance_score": None,
        "relevance_reason": None,
        "summary": None,
        "summary_type": "metadata_only",
        "status": "candidate",
    }
    data.update(overrides)
    return Paper(**data)


class DeduplicationTests(unittest.TestCase):
    def test_deduplicates_by_doi_arxiv_id_and_title_hash(self):
        papers = [
            _paper(doi="10.1000/example", title="First title"),
            _paper(doi="10.1000/EXAMPLE", title="Different title"),
            _paper(arxiv_id="2601.00001", title="Second title"),
            _paper(arxiv_id="2601.00001", title="Other title"),
            _paper(title="Same punctuation: title!"),
            _paper(title="Same punctuation title"),
        ]

        unique = deduplicate_papers(papers)

        self.assertEqual(len(unique), 3)


class ArxivParsingTests(unittest.TestCase):
    def test_parses_arxiv_atom_entry(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2601.00001v1</id>
            <title>Self-driving labs for reaction optimization</title>
            <summary>We combine active learning and flow chemistry.</summary>
            <published>2026-01-02T00:00:00Z</published>
            <author><name>Ada Lovelace</name></author>
            <arxiv:doi>10.1000/example</arxiv:doi>
            <link href="http://arxiv.org/abs/2601.00001v1" rel="alternate" type="text/html"/>
            <link title="pdf" href="http://arxiv.org/pdf/2601.00001v1" rel="related" type="application/pdf"/>
          </entry>
        </feed>"""

        papers = parse_arxiv_response(xml)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].arxiv_id, "2601.00001v1")
        self.assertEqual(papers[0].doi, "10.1000/example")
        self.assertEqual(papers[0].published_date, date(2026, 1, 2))
        self.assertEqual(papers[0].pdf_url, "http://arxiv.org/pdf/2601.00001v1")


class RelevanceParsingTests(unittest.TestCase):
    def test_parses_relevance_json_and_defaults_decision_from_score(self):
        raw = """```json
        {"score": 4, "reason": "Directly about autonomous reaction optimization.", "topics": ["active-learning"], "priority": "high"}
        ```"""

        decision = parse_relevance_response(raw)

        self.assertEqual(decision.decision, "ingest")
        self.assertEqual(decision.score, 4)
        self.assertEqual(decision.priority, "high")


class PdfResolverTests(unittest.TestCase):
    def test_resolves_arxiv_pdf_without_network(self):
        paper = _paper(arxiv_id="2601.00001v1")

        self.assertEqual(resolve_pdf_url(paper, {}), "https://arxiv.org/pdf/2601.00001v1.pdf")

    @patch("literature_scout.resolvers.pdf_resolver.requests.get")
    def test_resolves_unpaywall_when_configured(self, mock_get):
        mock_get.return_value.json.return_value = {
            "is_oa": True,
            "best_oa_location": {"url_for_pdf": "https://example.test/paper.pdf"},
        }
        mock_get.return_value.raise_for_status.return_value = None

        url = resolve_unpaywall_pdf(
            "10.1000/example",
            {"literature_scout": {"unpaywall_email": "test@example.com"}},
        )

        self.assertEqual(url, "https://example.test/paper.pdf")


class SlackPayloadTests(unittest.TestCase):
    def test_missing_pdf_message_contains_required_fields(self):
        paper = _paper(relevance_reason="Directly relevant to robotic chemistry.")

        message = build_missing_pdf_message(paper)

        self.assertIn("New paper found:", message)
        self.assertIn("Title: Bayesian optimization", message)
        self.assertIn("Why relevant: Directly relevant", message)
        self.assertIn("Reply in this thread with the PDF", message)


class StorageTests(unittest.TestCase):
    def test_upsert_is_idempotent_and_thread_lookup_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = ScoutStorage(Path(tmp) / "scout.sqlite")
            paper = _paper(doi="10.1000/example")

            storage.upsert_paper(paper)
            storage.upsert_paper(paper)
            storage.link_slack_thread(paper, "C123", "123.456")

            self.assertTrue(storage.has_seen(paper))
            loaded = storage.get_by_slack_thread("C123", "123.456")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.doi, "10.1000/example")


if __name__ == "__main__":
    unittest.main()
