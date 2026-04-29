"""
paper_fetcher.py
Resolves DOIs and fetches paper content via Unpaywall, CrossRef, and direct PDF parsing.
Falls back gracefully through multiple sources.
"""

import json
import re
import logging
import requests
import fitz  # pymupdf
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

try:
    import ollama as _ollama
except ImportError:
    _ollama = None

try:
    import cloudscraper
    _scraper = cloudscraper.create_scraper()
except ImportError:
    _scraper = None

logger = logging.getLogger(__name__)

# Chars to scan from the start of extracted text when hunting for a DOI.
# First page of most PDFs is ~3-5k chars; we go a bit wider to catch footer DOIs.
_DOI_SCAN_CHARS = 6000

# Contact email for Unpaywall API (required by their ToS, identifies your requests)
UNPAYWALL_EMAIL = "e.schiettekatte@uva.nl"

# Regex to catch DOIs in various formats people paste in Slack
DOI_PATTERNS = [
    r'\b(10\.\d{4,}/[^\s>"\'\]]+)',                          # bare DOI
    r'doi\.org/(10\.\d{4,}/[^\s>"\'\]]+)',                   # doi.org/xxx
    r'dx\.doi\.org/(10\.\d{4,}/[^\s>"\'\]]+)',               # dx.doi.org/xxx
    # Journal-specific URLs
    r'pubs\.acs\.org/doi/(10\.\d{4,}/[^\s>"\'\]]+)',
    r'rsc\.org/doi/(10\.\d{4,}/[^\s>"\'\]]+)',
    r'nature\.com/articles/(10\.\d{4,}/[^\s>"\'\]]+)',
    r'science\.org/doi/(10\.\d{4,}/[^\s>"\'\]]+)',
    r'wiley\.com/doi/(10\.\d{4,}/[^\s>"\'\]]+)',
    r'chemrxiv\.org/engage/chemrxiv/article-details/(10\.\d{4,}/[^\s>"\'\]]+)',
]


@dataclass
class PaperMetadata:
    doi: str
    title: str
    authors: list[str]
    journal: str
    year: Optional[int]
    abstract: Optional[str]
    full_text: Optional[str]      # extracted text if we got the PDF
    pdf_url: Optional[str]        # open access URL if found
    is_open_access: bool = False


def extract_doi_from_text(text: str) -> Optional[str]:
    """Extract first DOI found in a block of text."""
    for pattern in DOI_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            doi = match.group(1).rstrip('.,;)')  # strip trailing punctuation
            logger.info(f"Extracted DOI: {doi}")
            return doi
    return None


def fetch_metadata_crossref(doi: str) -> Optional[dict]:
    """Fetch paper metadata from CrossRef API."""
    url = f"https://api.crossref.org/works/{doi}"
    headers = {"User-Agent": f"PaperBrain/1.0 (mailto:{UNPAYWALL_EMAIL})"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()["message"]
        return data
    except Exception as e:
        logger.warning(f"CrossRef fetch failed for {doi}: {e}")
        return None


def fetch_open_access_url(doi: str) -> Optional[str]:
    """Check Unpaywall for open access PDF URL."""
    url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("is_oa"):
            best = data.get("best_oa_location", {})
            pdf_url = best.get("url_for_pdf") or best.get("url")
            logger.info(f"Found OA URL for {doi}: {pdf_url}")
            return pdf_url
    except Exception as e:
        logger.warning(f"Unpaywall fetch failed for {doi}: {e}")
    return None


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}


def download_and_extract_pdf(url: str) -> Optional[str]:
    """Download PDF from URL and extract text using PyMuPDF."""
    try:
        resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=30, stream=True)
        if resp.status_code == 403 and _scraper:
            logger.info(f"Got 403, retrying with cloudscraper: {url}")
            resp = _scraper.get(url, timeout=30)
        resp.raise_for_status()

        # Check it's actually a PDF
        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type and not url.endswith(".pdf"):
            logger.warning(f"URL may not be a PDF: {content_type}")

        pdf_bytes = resp.content
        return extract_text_from_pdf_bytes(pdf_bytes)
    except Exception as e:
        logger.warning(f"PDF download failed from {url}: {e}")
        return None


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> Optional[str]:
    """Extract text from PDF bytes using PyMuPDF."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        full_text = "\n".join(pages)
        # Basic cleanup
        full_text = re.sub(r'\n{3,}', '\n\n', full_text)
        logger.info(f"Extracted {len(full_text)} chars from PDF ({len(pages)} pages)")
        return full_text
    except Exception as e:
        logger.warning(f"PDF text extraction failed: {e}")
        return None


def extract_text_from_pdf_path(path: Path) -> Optional[str]:
    """Extract text from a local PDF file."""
    try:
        with open(path, "rb") as f:
            return extract_text_from_pdf_bytes(f.read())
    except Exception as e:
        logger.warning(f"Could not read PDF at {path}: {e}")
        return None


def parse_crossref_metadata(doi: str, data: dict) -> PaperMetadata:
    """Parse CrossRef API response into PaperMetadata."""
    # Authors
    authors = []
    for author in data.get("author", []):
        given = author.get("given", "")
        family = author.get("family", "")
        authors.append(f"{given} {family}".strip())

    # Year
    year = None
    date_parts = data.get("published", {}).get("date-parts", [[]])
    if date_parts and date_parts[0]:
        year = date_parts[0][0]

    # Journal
    journal = ""
    container = data.get("container-title", [])
    if container:
        journal = container[0]

    # Abstract (CrossRef sometimes has it, often doesn't)
    abstract = data.get("abstract", None)
    if abstract:
        # CrossRef abstracts sometimes have JATS XML tags
        abstract = re.sub(r'<[^>]+>', '', abstract).strip()

    return PaperMetadata(
        doi=doi,
        title=data.get("title", ["Unknown Title"])[0],
        authors=authors,
        journal=journal,
        year=year,
        abstract=abstract,
        full_text=None,
        pdf_url=None,
    )


def fetch_paper(doi: str) -> Optional[PaperMetadata]:
    """
    Main entry point. Given a DOI, returns PaperMetadata with as much
    content as we can get. Tries: CrossRef → Unpaywall → PDF extraction.
    """
    logger.info(f"Fetching paper: {doi}")

    # Step 1: Metadata from CrossRef
    crossref_data = fetch_metadata_crossref(doi)
    if not crossref_data:
        logger.error(f"Could not fetch metadata for DOI: {doi}")
        return None

    paper = parse_crossref_metadata(doi, crossref_data)

    # Step 2: Try to get full text via Unpaywall
    pdf_url = fetch_open_access_url(doi)
    if pdf_url:
        paper.pdf_url = pdf_url
        paper.is_open_access = True
        full_text = download_and_extract_pdf(pdf_url)
        if full_text:
            paper.full_text = full_text
            logger.info(f"Got full text for {doi} ({len(full_text)} chars)")
        else:
            logger.info(f"PDF download failed for {doi}, will use abstract only")
    else:
        logger.info(f"No OA PDF found for {doi}, will use abstract only")

    return paper


_METADATA_SYSTEM_PROMPT = (
    "You are a bibliographic metadata extractor. "
    "Given raw text from the first page of an academic PDF, extract the paper metadata. "
    "Return ONLY valid JSON, no markdown fences, no commentary."
)

_METADATA_USER_PROMPT = """Extract bibliographic metadata from this academic paper text.

Text (first page):
{text}

Return this exact JSON structure:
{{
  "title": "Full paper title",
  "authors": ["First Last", "First Last"],
  "journal": "Journal or conference name, or empty string if not found",
  "year": 2024,
  "doi": "10.xxxx/xxxxx or null if not found"
}}

Rules:
- title: the main paper title, not section headings
- authors: full names in order; omit affiliations
- year: integer, or null if not found
- doi: only include if you see an actual DOI string in the text; do not guess"""


def _extract_metadata_with_llm(first_page_text: str, config: dict) -> Optional[dict]:
    """Use local Ollama to extract metadata from raw first-page PDF text."""
    if not _ollama:
        return None
    try:
        response = _ollama.chat(
            model=config["ollama"]["model"],
            messages=[
                {"role": "system", "content": _METADATA_SYSTEM_PROMPT},
                {"role": "user", "content": _METADATA_USER_PROMPT.format(text=first_page_text[:3000])},
            ],
            options={
                "num_ctx": config["ollama"]["num_ctx"],
                "temperature": 0,
            },
        )
        raw = response["message"]["content"]
        # Strip thinking blocks (qwen3) and markdown fences
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        raw = re.sub(r"```[a-z]*\s*", "", raw)
        raw = raw.strip()
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"LLM metadata extraction failed: {e}")
        return None


def _search_crossref_by_title(title: str) -> Optional[PaperMetadata]:
    """
    Search CrossRef for a paper by title. Returns metadata only when the
    top result title matches closely enough to be trustworthy.
    """
    headers = {"User-Agent": f"PaperBrain/1.0 (mailto:{UNPAYWALL_EMAIL})"}
    try:
        resp = requests.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": title, "rows": 1,
                    "select": "DOI,title,author,container-title,published,abstract"},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
        if not items:
            return None

        item = items[0]
        found_title = (item.get("title") or [""])[0]

        # Sanity check: word overlap between query title and result title must be > 50%
        def _words(s):
            return set(re.sub(r"[^\w\s]", "", s.lower()).split())

        query_words = _words(title)
        result_words = _words(found_title)
        if not query_words:
            return None
        overlap = len(query_words & result_words) / len(query_words)
        if overlap < 0.5:
            logger.warning(
                f"CrossRef title search low confidence ({overlap:.0%}): "
                f"'{title}' vs '{found_title}'"
            )
            return None

        doi = item.get("DOI", "")
        logger.info(f"CrossRef title search matched: {found_title} ({doi})")
        return parse_crossref_metadata(doi, item)
    except Exception as e:
        logger.warning(f"CrossRef title search failed: {e}")
        return None


def fetch_paper_from_pdf(pdf_path: Path, doi_hint: Optional[str] = None,
                         config: Optional[dict] = None) -> Optional[PaperMetadata]:
    """
    Process a local PDF file. Extracts text then resolves metadata via:
      1. DOI regex on first ~6k chars of text
      2. LLM metadata extraction from first page (if config provided)
      3. CrossRef title search using LLM-extracted title
      4. LLM-extracted metadata as floor (still better than filename)
    """
    logger.info(f"Processing local PDF: {pdf_path}")
    full_text = extract_text_from_pdf_path(pdf_path)
    if not full_text:
        return None

    # --- Step 1: DOI via regex ---
    doi = doi_hint or extract_doi_from_text(full_text[:_DOI_SCAN_CHARS])
    if doi:
        logger.info(f"Found DOI in PDF text: {doi}")
        paper = fetch_paper(doi)
        if paper:
            if not paper.full_text:
                paper.full_text = full_text
            return paper

    # --- Step 2 & 3: LLM extraction + CrossRef title search ---
    if config:
        logger.info("No DOI found via regex, trying LLM metadata extraction...")
        llm_meta = _extract_metadata_with_llm(full_text, config)

        if llm_meta:
            # If LLM found a DOI, validate it against CrossRef before trusting it
            llm_doi = llm_meta.get("doi")
            if llm_doi:
                logger.info(f"LLM suggested DOI: {llm_doi}, validating with CrossRef...")
                paper = fetch_paper(llm_doi)
                if paper:
                    if not paper.full_text:
                        paper.full_text = full_text
                    return paper
                logger.warning(f"LLM DOI '{llm_doi}' not found in CrossRef, ignoring")

            # Try CrossRef title search
            llm_title = llm_meta.get("title", "").strip()
            if llm_title:
                logger.info(f"Searching CrossRef by title: {llm_title}")
                paper = _search_crossref_by_title(llm_title)
                if paper:
                    if not paper.full_text:
                        paper.full_text = full_text
                    return paper

            # Floor: use whatever the LLM extracted — still better than the filename
            logger.info("Using LLM-extracted metadata as fallback")
            authors = llm_meta.get("authors") or []
            return PaperMetadata(
                doi=llm_meta.get("doi") or "unknown",
                title=llm_meta.get("title") or pdf_path.stem.replace("-", " ").replace("_", " "),
                authors=authors if isinstance(authors, list) else [],
                journal=llm_meta.get("journal") or "",
                year=llm_meta.get("year"),
                abstract=None,
                full_text=full_text,
                pdf_url=None,
            )

    # --- Last resort: filename ---
    logger.warning(f"Metadata extraction failed for {pdf_path.name}, using filename")
    return PaperMetadata(
        doi="unknown",
        title=pdf_path.stem.replace("-", " ").replace("_", " "),
        authors=[],
        journal="",
        year=None,
        abstract=None,
        full_text=full_text,
        pdf_url=None,
    )
