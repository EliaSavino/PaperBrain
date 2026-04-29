import unittest
from unittest.mock import patch

from tests.support import import_fresh


summarizer = import_fresh("summarizer")


class SummarizerFormattingTests(unittest.TestCase):
    def setUp(self):
        self.summary = summarizer.PaperSummary(
            title="A Useful Paper",
            authors_short="Smith et al.",
            year=2024,
            journal="JACS",
            doi="10.1021/jacs.3c01234",
            chem_one_liner="Chem TL;DR",
            chem_what="They made a thing.",
            chem_finding="The thing worked.",
            chem_method="Flow synthesis.",
            chem_relevance="Helpful in the lab.",
            ml_problem="Optimization under sparse data.",
            ml_method="Bayesian optimization.",
            ml_dataset="120 reactions.",
            ml_result="Better yield than baseline.",
            ml_angle="Could fit self-driving labs.",
            ml_limitations="Small dataset.",
            relevance_score=4,
            tags=["flow-chemistry"],
        )

    def test_combined_summary_contains_both_sections(self):
        message = summarizer.format_slack_combined_summary(self.summary)
        self.assertIn("*Chem pass*", message)
        self.assertIn("*ML pass*", message)
        self.assertIn("DOI: `10.1021/jacs.3c01234`", message)
        self.assertIn("Bayesian optimization.", message)
        self.assertIn("Flow synthesis.", message)

    @patch("summarizer._call_ollama", return_value="<think>hidden</think>\nShort reply\n\n\nCheers")
    def test_quick_slack_reply_strips_thinking_and_extra_spacing(self, mock_call):
        reply = summarizer.quick_slack_reply("hello there", {"ollama": {}})
        self.assertEqual(reply, "Short reply\n\nCheers")
        mock_call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
