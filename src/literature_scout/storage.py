from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Paper


class ScoutStorage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS papers (
                    paper_key TEXT PRIMARY KEY,
                    doi TEXT,
                    arxiv_id TEXT,
                    title_hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    slack_channel TEXT,
                    slack_thread_ts TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_arxiv ON papers(arxiv_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_title_hash ON papers(title_hash)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_papers_slack_thread "
                "ON papers(slack_channel, slack_thread_ts)"
            )

    def has_seen(self, paper: Paper) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM papers
                WHERE paper_key = ?
                   OR (doi IS NOT NULL AND doi = ?)
                   OR (arxiv_id IS NOT NULL AND arxiv_id = ?)
                   OR title_hash = ?
                LIMIT 1
                """,
                (paper.dedupe_key(), paper.doi, paper.arxiv_id, paper.title_hash()),
            ).fetchone()
        return row is not None

    def upsert_paper(self, paper: Paper) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO papers (
                    paper_key, doi, arxiv_id, title_hash, title, status, data_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(paper_key) DO UPDATE SET
                    status = excluded.status,
                    data_json = excluded.data_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    paper.dedupe_key(),
                    paper.doi,
                    paper.arxiv_id,
                    paper.title_hash(),
                    paper.title,
                    paper.status,
                    json.dumps(paper.to_dict(), sort_keys=True),
                ),
            )

    def mark_status(self, paper: Paper, status: str) -> None:
        paper.status = status
        self.upsert_paper(paper)

    def link_slack_thread(self, paper: Paper, channel: str, thread_ts: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE papers
                SET slack_channel = ?, slack_thread_ts = ?, updated_at = CURRENT_TIMESTAMP
                WHERE paper_key = ?
                """,
                (channel, thread_ts, paper.dedupe_key()),
            )

    def get_by_slack_thread(self, channel: str, thread_ts: str) -> Paper | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT data_json FROM papers
                WHERE slack_channel = ? AND slack_thread_ts = ?
                LIMIT 1
                """,
                (channel, thread_ts),
            ).fetchone()
        if not row:
            return None
        return Paper.from_dict(json.loads(row[0]))

