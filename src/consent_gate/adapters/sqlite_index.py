"""The reference adapter: SQLite, with the consent predicate inside the plan.

The property this project sells is that a non-consented record is *never
scored*, and the only honest way to show that is to make it visible in the
query plan rather than assert it in a README. So the consented subject set is
materialised into a temporary table and joined, and
:meth:`SqliteIndex.explain` returns SQLite's own plan for the exact query that
ran. A reader can see the join in the scan.

The vector path scores with a SQL function registered on the connection, so
similarity is computed *in the same statement* as the consent join. There is
no intermediate result set that contains a non-consented document, not even
for a moment, because such a set would be exactly the thing a post-filtering
implementation builds and then leaks through counts and pagination.

Embeddings are a deterministic hashed bag of words rather than a learned
model. That is a real vector index with real cosine similarity and no
external dependency, which keeps the exhibit reproducible by a sceptic on a
clean checkout — the retrieval quality is not what is under test here, the
enforcement boundary is.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
from datetime import datetime

from ..gate import Hit

DIM = 1024
#: Semantic recall fires only above this. Measured reason for the value: at
#: DIM=256 a token present nowhere in the corpus scored 0.4 against unrelated
#: notes purely through hash collisions, and those collisions were being
#: served as search results. Noise returned as a match is bad retrieval
#: anywhere; here it is worse, because two queries that should both return
#: nothing returned *different* nothing, which is a channel.
MIN_SIMILARITY = 0.55
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")
_STOP = frozenset("""
a an the of in on at to for with by from is are was were be been and or that
this these those it its as not no than then so such
""".split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id   TEXT PRIMARY KEY,
    subject  TEXT NOT NULL,
    doc_type TEXT NOT NULL DEFAULT '',
    title    TEXT NOT NULL DEFAULT '',
    text     TEXT NOT NULL,
    region   TEXT NOT NULL DEFAULT '',
    condition TEXT NOT NULL DEFAULT '',
    vec      BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_subject ON documents(subject);
CREATE TABLE IF NOT EXISTS identifiers (
    doc_id TEXT NOT NULL, kind TEXT NOT NULL,
    start_ INTEGER NOT NULL, end_ INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS identifiers_doc ON identifiers(doc_id);
"""


def embed(text: str) -> list[float]:
    """Deterministic hashed bag of words, L2-normalised."""
    vec = [0.0] * DIM
    for token in _WORD.findall(text.lower()):
        if token in _STOP:
            continue
        h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8
                                           ).digest(), "big")
        vec[h % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{DIM}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{DIM}f", blob))


class SqliteIndex:
    """Vector and keyword retrieval over one table, consent-joined."""

    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._qvec: list[float] = []
        self._db.create_function("cosine", 1, self._cosine, deterministic=True)
        self._db.commit()
        self._last_sql = ""
        self._last_params: tuple = ()

    def _cosine(self, blob: bytes) -> float:
        if not self._qvec:
            return 0.0
        return sum(a * b for a, b in zip(_unpack(blob), self._qvec))

    # ── loading ─────────────────────────────────────────────────────────
    def add(self, doc_id: str, subject: str, text: str, *, title: str = "",
            doc_type: str = "", region: str = "", condition: str = "",
            identifiers: list[dict] | None = None) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO documents "
            "(doc_id, subject, doc_type, title, text, region, condition, vec) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, subject, doc_type, title, text, region, condition,
             _pack(embed(f"{title} {text}"))))
        self._db.execute("DELETE FROM identifiers WHERE doc_id = ?", (doc_id,))
        for ident in identifiers or []:
            self._db.execute(
                "INSERT INTO identifiers (doc_id, kind, start_, end_) "
                "VALUES (?,?,?,?)",
                (doc_id, ident.get("kind", ""), ident["start"], ident["end"]))
        self._db.commit()

    # ── the consent join ────────────────────────────────────────────────
    def _bind_candidates(self, subjects: set[str] | None) -> str:
        """Materialise the consented set and return the SQL that joins it.

        A temporary table joined into the scan, rather than a Python-side
        filter over rows the engine already produced. The distinction is the
        entire product.
        """
        if subjects is None:                  # the masking baseline
            return ""
        self._db.execute("DROP TABLE IF EXISTS temp.consented")
        self._db.execute(
            "CREATE TEMP TABLE consented (subject TEXT PRIMARY KEY)")
        self._db.executemany("INSERT OR IGNORE INTO temp.consented VALUES (?)",
                             [(s,) for s in subjects])
        return " JOIN temp.consented c ON c.subject = d.subject "

    # ── retrieval ───────────────────────────────────────────────────────
    def search(self, query: str, subjects: set[str] | None, limit: int,
               offset: int) -> tuple[list[Hit], int]:
        join = self._bind_candidates(subjects)
        self._qvec = embed(query)
        terms = [t for t in _WORD.findall(query.lower()) if t not in _STOP]

        # Keyword recall and vector similarity in one statement, so the
        # consent join constrains both. A document matches if it contains a
        # query term or is similar enough to be worth returning.
        like = " OR ".join(["lower(d.text) LIKE ?"] * len(terms)) or "0"
        params = [f"%{t}%" for t in terms]

        where = f"(({like}) OR cosine(d.vec) > {MIN_SIMILARITY})"
        base = (f"FROM documents d{join} WHERE {where}")

        total = self._db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]

        sql = (f"SELECT d.doc_id, d.subject, d.title, d.text, "
               f"cosine(d.vec) AS score {base} "
               f"ORDER BY score DESC, d.doc_id ASC LIMIT ? OFFSET ?")
        self._last_sql, self._last_params = sql, tuple(params)
        rows = self._db.execute(sql, (*params, limit, offset)).fetchall()
        hits = [Hit(r["doc_id"], r["subject"], round(r["score"], 6),
                    r["text"], r["title"]) for r in rows]
        return hits, total

    def count_by(self, field_name: str, subjects: set[str] | None
                 ) -> dict[str, int]:
        if field_name not in {"region", "condition", "doc_type"}:
            raise ValueError(f"cannot aggregate on {field_name!r}")
        join = self._bind_candidates(subjects)
        rows = self._db.execute(
            f"SELECT d.{field_name} AS k, COUNT(*) AS n "
            f"FROM documents d{join} GROUP BY d.{field_name} ORDER BY k"
        ).fetchall()
        return {r["k"]: r["n"] for r in rows}

    def strata(self, subjects: set[str] | None) -> dict[str, set[str]]:
        """Subjects per condition-and-region stratum, for the small-cell probe."""
        join = self._bind_candidates(subjects)
        rows = self._db.execute(
            f"SELECT d.condition AS c, d.region AS r, d.subject AS s "
            f"FROM documents d{join}").fetchall()
        out: dict[str, set[str]] = {}
        for r in rows:
            out.setdefault(f"cond={r['c']}|region={r['r']}", set()).add(r["s"])
        return out

    # ── showing the work ────────────────────────────────────────────────
    def explain(self, query: str, subjects: set[str] | None) -> str:
        """SQLite's plan for the query that just ran.

        Published in the exhibit. "The predicate is part of the query, not
        applied to its results" is a claim a reader should be able to check
        rather than take on trust.
        """
        self.search(query, subjects, limit=1, offset=0)
        rows = self._db.execute(
            f"EXPLAIN QUERY PLAN {self._last_sql}",
            (*self._last_params, 1, 0)).fetchall()
        return "\n".join(r["detail"] for r in rows)

    # ── the masking baseline's masker ───────────────────────────────────
    def mask(self, doc_id: str) -> str:
        """Mask every direct identifier, at 100% recall by construction.

        Only possible because the corpus is synthetic and every identifier's
        offset is recorded. That is the point: the leaks the exhibit shows
        survive a *perfect* masker.
        """
        row = self._db.execute(
            "SELECT text FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is None:
            return ""
        text = row["text"]
        spans = self._db.execute(
            "SELECT kind, start_, end_ FROM identifiers WHERE doc_id = ? "
            "ORDER BY start_ DESC", (doc_id,)).fetchall()
        for s in spans:
            text = (text[:s["start_"]] + f"[{s['kind'].upper()}]"
                    + text[s["end_"]:])
        return text

    def subjects(self) -> set[str]:
        return {r[0] for r in
                self._db.execute("SELECT DISTINCT subject FROM documents")}

    def close(self) -> None:
        self._db.close()
