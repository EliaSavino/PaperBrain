import unittest
from unittest.mock import patch

from tests.support import import_fresh


paper_fetcher = import_fresh("paper_fetcher")


class ExtractDoiTests(unittest.TestCase):
    def test_extracts_bare_doi(self):
        doi = paper_fetcher.extract_doi_from_text("Please read 10.1021/jacs.3c01234 today.")
        self.assertEqual(doi, "10.1021/jacs.3c01234")

    def test_extracts_slack_formatted_doi_url(self):
        text = "<https://doi.org/10.1021/jacs.3c01234|paper link>"
        doi = paper_fetcher.extract_doi_from_text(text)
        self.assertEqual(doi, "10.1021/jacs.3c01234")

    def test_strips_trailing_punctuation(self):
        doi = paper_fetcher.extract_doi_from_text("(10.1021/jacs.3c01234);")
        self.assertEqual(doi, "10.1021/jacs.3c01234")


class CrossrefRequestTests(unittest.TestCase):
    @patch("paper_fetcher.requests.get")
    def test_crossref_request_uses_encoded_doi_and_longer_timeout(self, mock_get):
        mock_get.return_value.json.return_value = {"message": {"title": ["Test"]}}
        mock_get.return_value.raise_for_status.return_value = None

        paper_fetcher.fetch_metadata_crossref("10.1002/anie.202400123")

        called_url = mock_get.call_args.args[0]
        self.assertIn("10.1002%2Fanie.202400123", called_url)
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
