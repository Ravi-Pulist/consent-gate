"""The Postgres + pgvector reference adapter.

This is the path the design calls reference, for one reason: the property the
whole library sells — that a non-consented record is *never scored* — is
visible in the query plan here rather than asserted. `EXPLAIN` over a real
pgvector scan shows whether the consent predicate is inside the plan or
applied to its output, and a reader can run it themselves.

The consented set is materialised into a temporary table and joined, exactly
as in the SQLite adapter, so both adapters are answering the same question the
same way and the exhibit can compare their plans side by side.

**What running this actually showed, and a phrase it forced us to correct.**
"A non-consented record is never scored" turns out to be a claim about the
*plan*, not about the library, and the planner changes its mind with scale.

On a three-document table Postgres chose a nested loop driven by the
consented set, with ``Index Cond: (subject = c.subject)`` — non-consented rows
were genuinely never read. On the thousand-document corpus it chose a hash
join, and ``EXPLAIN ANALYZE`` is explicit about the consequence::

    Hash Join                        (actual rows=326)
      Hash Cond: (d.subject = c.subject)
      ->  Seq Scan on documents d    (actual rows=847)
            Filter: (lower(text) ~~ '%review%' OR (1 - (vec <=> ...)) > 0.55)

847 rows cleared the content filter and only then were cut to 326 by the
consent join. The cosine distance *was* computed for documents belonging to
subjects with no consent.

The observable properties are unaffected and still hold exactly: nothing
non-consented is returned, the count is computed over the joined set (326, not
847), pagination is drawn from it, and a hidden token remains byte-identical
to a token that never existed. Those are what the probes verify and what a
patient's privacy actually depends on.

But the honest formulation of the guarantee is **exclusion is a property of
the candidate set — nothing non-consented is returned, counted, ranked or
paged** — not "never touched by the CPU". Physical non-access is a stronger
property, it is plan-dependent, and a deployment that needs it (for
side-channel reasons, or because reading the row at all is the concern) must
pin the plan or partition per purpose, and verify it on its own engine and
version. That is now said in the README rather than glossed.

**Filtered ANN, separately.** Approximate nearest-neighbour behaviour under a
restrictive filter differs by engine, index type and version: a filter can
make an HNSW or IVFFlat traversal return fewer than `k` rows and engines
differ in whether they compensate. This adapter uses an exact scan (no ANN
index), so recall is exact and the join semantics are unambiguous. A
deployment that adds an ANN index must re-verify both properties itself.
"""

from __future__ import annotations

import os
import re

from ..gate import Hit
from .sqlite_index import DIM, MIN_SIMILARITY, _WORD, _STOP, embed

DEFAULT_DSN = os.environ.get(
    "CONSENT_GATE_PG_DSN",
    "postgresql://postgres:consent@localhost:55432/consent")

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
DROP TABLE IF EXISTS documents CASCADE;
CREATE TABLE documents (
    doc_id    TEXT PRIMARY KEY,
    subject   TEXT NOT NULL,
    doc_type  TEXT NOT NULL DEFAULT '',
    title     TEXT NOT NULL DEFAULT '',
    text      TEXT NOT NULL,
    region    TEXT NOT NULL DEFAULT '',
    condition TEXT NOT NULL DEFAULT '',
    vec       vector(%(dim)s) NOT NULL
);
CREATE INDEX documents_subject ON documents(subject);
DROP TABLE IF EXISTS identifiers;
CREATE TABLE identifiers (
    doc_id TEXT NOT NULL, kind TEXT NOT NULL,
    start_ INTEGER NOT NULL, end_ INTEGER NOT NULL
);
"""


def _abbreviate_vectors(plan: str) -> str:
    """Collapse the 1,024-element vector literals so the plan is readable.

    Only the literal is touched. Every operator, cost and index condition is
    left exactly as Postgres emitted it, because the whole point of quoting a
    plan is that the reader is seeing the engine's own words.
    """
    return re.sub(r"'\[[0-9.,\-e]{60,}\]'::vector",
                  "'[...1024 dims...]'::vector", plan)


class PgVectorIndex:
    """Same interface as :class:`SqliteIndex`, over Postgres + pgvector."""

    def __init__(self, dsn: str = DEFAULT_DSN):
        import psycopg2
        self.dsn = dsn
        self._db = psycopg2.connect(dsn)
        self._db.autocommit = True
        with self._db.cursor() as cur:
            cur.execute(SCHEMA % {"dim": DIM})
        self._last_sql = ""
        self._last_params: tuple = ()

    @staticmethod
    def available(dsn: str = DEFAULT_DSN) -> bool:
        try:
            import psycopg2
            conn = psycopg2.connect(dsn, connect_timeout=3)
            conn.close()
            return True
        except Exception:                       # noqa: BLE001
            return False

    # ── loading ─────────────────────────────────────────────────────────
    def add(self, doc_id: str, subject: str, text: str, *, title: str = "",
            doc_type: str = "", region: str = "", condition: str = "",
            identifiers: list[dict] | None = None) -> None:
        vec = embed(f"{title} {text}")
        with self._db.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (doc_id, subject, doc_type, title, "
                "text, region, condition, vec) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (doc_id) DO UPDATE SET text = EXCLUDED.text",
                (doc_id, subject, doc_type, title, text, region, condition,
                 "[" + ",".join(f"{v:.6f}" for v in vec) + "]"))
            cur.execute("DELETE FROM identifiers WHERE doc_id = %s", (doc_id,))
            for ident in identifiers or []:
                cur.execute(
                    "INSERT INTO identifiers (doc_id, kind, start_, end_) "
                    "VALUES (%s,%s,%s,%s)",
                    (doc_id, ident.get("kind", ""), ident["start"],
                     ident["end"]))

    def add_many(self, rows: list[dict]) -> None:
        """Bulk load. One statement per batch rather than per row, because a
        thousand round trips is the difference between a demo that runs and a
        demo nobody waits for."""
        from psycopg2.extras import execute_values
        payload = [
            (r["doc_id"], r["subject"], r.get("doc_type", ""),
             r.get("title", ""), r["text"], r.get("region", ""),
             r.get("condition", ""),
             "[" + ",".join(f"{v:.6f}"
                            for v in embed(f"{r.get('title','')} {r['text']}"))
             + "]")
            for r in rows]
        with self._db.cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO documents (doc_id, subject, doc_type, title, "
                "text, region, condition, vec) VALUES %s "
                "ON CONFLICT (doc_id) DO NOTHING", payload)
            idents = [(r["doc_id"], i.get("kind", ""), i["start"], i["end"])
                      for r in rows for i in r.get("identifiers", [])]
            if idents:
                execute_values(
                    cur, "INSERT INTO identifiers (doc_id, kind, start_, end_)"
                         " VALUES %s", idents)

    # ── the consent join ────────────────────────────────────────────────
    def _bind_candidates(self, subjects: set[str] | None) -> str:
        if subjects is None:
            return ""
        with self._db.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS consented")
            cur.execute("CREATE TEMP TABLE consented "
                        "(subject TEXT PRIMARY KEY) ON COMMIT PRESERVE ROWS")
            if subjects:
                from psycopg2.extras import execute_values
                execute_values(cur, "INSERT INTO consented VALUES %s",
                               [(s,) for s in subjects])
            cur.execute("ANALYZE consented")
        return " JOIN consented c ON c.subject = d.subject "

    def _build(self, query: str, subjects: set[str] | None):
        join = self._bind_candidates(subjects)
        qvec = "[" + ",".join(f"{v:.6f}" for v in embed(query)) + "]"
        terms = [t for t in _WORD.findall(query.lower()) if t not in _STOP]
        like = " OR ".join(["lower(d.text) LIKE %s"] * len(terms)) or "FALSE"
        params = [f"%{t}%" for t in terms]
        # 1 - cosine distance is cosine similarity; the operator is <=>
        where = (f"(({like}) OR (1 - (d.vec <=> %s::vector)) > "
                 f"{MIN_SIMILARITY})")
        base = f"FROM documents d{join} WHERE {where}"
        return base, params + [qvec], qvec

    # ── retrieval ───────────────────────────────────────────────────────
    def search(self, query: str, subjects: set[str] | None, limit: int,
               offset: int) -> tuple[list[Hit], int]:
        base, params, qvec = self._build(query, subjects)
        with self._db.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) {base}", params)
            total = cur.fetchone()[0]
            sql = (f"SELECT d.doc_id, d.subject, d.title, d.text, "
                   f"1 - (d.vec <=> %s::vector) AS score {base} "
                   f"ORDER BY score DESC, d.doc_id ASC LIMIT %s OFFSET %s")
            self._last_sql, self._last_params = sql, tuple([qvec] + params)
            cur.execute(sql, [qvec] + params + [limit, offset])
            hits = [Hit(r[0], r[1], round(float(r[4]), 6), r[3], r[2])
                    for r in cur.fetchall()]
        return hits, total

    def count_by(self, field_name: str, subjects: set[str] | None
                 ) -> dict[str, int]:
        if field_name not in {"region", "condition", "doc_type"}:
            raise ValueError(f"cannot aggregate on {field_name!r}")
        join = self._bind_candidates(subjects)
        with self._db.cursor() as cur:
            cur.execute(f"SELECT d.{field_name}, COUNT(*) FROM documents d"
                        f"{join} GROUP BY d.{field_name} ORDER BY 1")
            return {r[0]: r[1] for r in cur.fetchall()}

    def strata(self, subjects: set[str] | None) -> dict[str, set[str]]:
        join = self._bind_candidates(subjects)
        out: dict[str, set[str]] = {}
        with self._db.cursor() as cur:
            cur.execute(f"SELECT d.condition, d.region, d.subject "
                        f"FROM documents d{join}")
            for cond, region, subject in cur.fetchall():
                out.setdefault(f"cond={cond}|region={region}",
                               set()).add(subject)
        return out

    # ── showing the work ────────────────────────────────────────────────
    def explain(self, query: str, subjects: set[str] | None,
                abbreviate: bool = True) -> str:
        """Postgres's own plan for the query that just ran.

        The query vector is 1,024 literals wide and appears three times in
        the plan, which buries the one line a reader needs. Abbreviated by
        default and never by editing the plan text itself — only the vector
        literal is collapsed, so what remains is verbatim.
        """
        self.search(query, subjects, limit=1, offset=0)
        with self._db.cursor() as cur:
            cur.execute("EXPLAIN " + self._last_sql,
                        list(self._last_params) + [1, 0])
            plan = "\n".join(r[0] for r in cur.fetchall())
        return _abbreviate_vectors(plan) if abbreviate else plan

    def mask(self, doc_id: str) -> str:
        with self._db.cursor() as cur:
            cur.execute("SELECT text FROM documents WHERE doc_id = %s",
                        (doc_id,))
            row = cur.fetchone()
            if row is None:
                return ""
            text = row[0]
            cur.execute("SELECT kind, start_, end_ FROM identifiers "
                        "WHERE doc_id = %s ORDER BY start_ DESC", (doc_id,))
            for kind, s, e in cur.fetchall():
                text = text[:s] + f"[{kind.upper()}]" + text[e:]
        return text

    def subjects(self) -> set[str]:
        with self._db.cursor() as cur:
            cur.execute("SELECT DISTINCT subject FROM documents")
            return {r[0] for r in cur.fetchall()}

    def close(self) -> None:
        self._db.close()
