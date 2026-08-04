# consent-gate

**A record without an active, purpose-bound consent is not retrieved, not
counted, not aggregated, never reaches the prompt, and cannot be told apart
from a record that does not exist.**

## The problem

Consent and de-identification answer different questions. De-identification
answers "who is this?", and 45 CFR 164.514(b)(2) is the standard form of the
answer: strip the eighteen identifier categories, and provided the covered
entity has no actual knowledge that what remains could identify someone
(164.514(b)(2)(ii)), the data is no longer PHI. Consent answers "may this be
used at all?". GDPR Article 5(1)(b) binds processing to the purpose the data
was collected for and Article 7(3) gives the data subject the right to withdraw
at any time, as easily as consent was given; India's DPDP Act 2023 says the
same in Section 6.

A RAG stack over clinical notes usually implements the first and calls it the
second: the retriever runs over everything, a masker redacts identifiers from
what comes back, and the result is described as privacy-preserving. It is not,
because the sentence survives the redaction. This is a document from the corpus
in this repo, after a masker with **100% identifier recall** has finished:

> This contact was a scheduled review of Harrowfield anaemia. Beacon, as the
> ward has called them since childhood, is 42 and still working as a lock
> keeper on the Cinderfell shorefront.

Name, MRN, phone, email and address were all removed. It still describes one
person, and that person withdrew consent. That is the actual-knowledge clause
at 164.514(b)(2)(ii) failing in the only way it ever fails in practice.

Two further surfaces leak without any document being returned at all. A count
over a stratum of two patients discloses both, and it does not matter that the
records were masked: they were *counted*, and the count is the disclosure. And
a search index is a membership oracle. Query a token unique to a withdrawn
patient's record, then query a token that exists nowhere. Any observable
difference (a hit, a count, a page boundary) answers "is this person in your
data?", which is the question the withdrawal was meant to close.

## What this is, and what it is not

This is a verifier. It audits where the consent predicate sits in someone
else's retrieval stack and produces an exhibit their engineers act on. It does
not build their retriever.

| ships | why |
|---|---|
| A probe suite (narrative, small cell, oracle, pagination) run unchanged against both configurations | every claimed improvement has its baseline in the same table, not a different document |
| A reference `QueryGate` enforcing exclusion below the retriever | "apply consent inside the scan" should be a working thing to copy, not an instruction |
| Two index adapters, SQLite and Postgres+pgvector, behind one interface | the same probes on two engines is how a plan-dependent claim gets caught |
| An append-only consent store with trigger-enforced immutability | revocation as an insert, so past state stays reconstructable |
| A markdown and JSON exhibit with a load-bearing exit code | an audit that fails a build is a control; one that produces a document is a document |

| does not ship | why |
|---|---|
| A production retrieval platform | the deliverable is the enforcement boundary and the evidence, not a service to operate |
| An embedding model | a deterministic hashed bag of words lets a sceptic reproduce the run with no download, and swapping in a real model does not move where the predicate is applied |
| A linkage table, anywhere in the package | the consent store is a membership oracle by construction, and letting it name people would make the system that proves non-use the most sensitive database in the deployment |
| Differential privacy for aggregates | exclusion fixes membership, DP perturbs values; compatible, different problem |
| Retrieval quality tuning | not what the exhibit tests |

## Measured results

Sources: `exhibits/exhibit.json` and `exhibits/exhibit.md` (SQLite),
`exhibits/exhibit-pgvector.json` and `exhibits/exhibit-pgvector.md`
(Postgres+pgvector). Identical results on both engines.

| | masking | exclusion |
|---|---|---|
| probes run | 113 | 113 |
| **total leaks** | **535** | **0** |
| narrative | 489 | 0 |
| oracle | 30 | 0 |
| small cell | 16 | 0 |

Scope: 200 patients, 1,000 documents, purpose `direct_care` evaluated at
`2026-08-04T00:00:00+00:00`, at which instant 80 subjects are consented and
120 are not (`counts` in both exhibit JSONs). The 113 probes are the 60
narrative traps, 20 small cells and 30 oracle pairs seeded in
`corpus/data/manifest.json`, plus 3 paging queries.

The masking side is not a straw man. It redacts every direct identifier at 100%
recall, possible only because the corpus is synthetic and every identifier's
offset is recorded, so every leak above survives a *perfect* masker. A real NER
masker also misses identifiers outright, so this understates masking's real
failure rather than exaggerating it. Per vector, what the number counts
(`exhibits/exhibit.json`):

| vector | masking | one leak is |
|---|---|---|
| narrative | 489 leak events, over 60 probes, 59 distinct documents, 26 distinct people | a returned document whose subject has no active consent, after masking |
| oracle | 30 of the 30 seeded pairs | the response to a hidden token and the response to a nonexistent token differ, by SHA-256 fingerprint over hits, count, page, page size, has-more and error |
| small cell | 16 of the 20 seeded cells (16 hold a non-consented member) | an aggregate cell counting a subject who withdrew; 5 of the 16 are cells of exactly one person |

**Revocation, mid-session, caches warm.** Every surface dark, worst surface
0.0755 s on SQLite (`exhibits/exhibit.json`) and 0.0789 s on pgvector
(`exhibits/exhibit-pgvector.json`), against a 5 s target. Five surfaces are
measured: vector and keyword search, result cache, prompt context, aggregates,
stratum membership. A surface that was not visible *before* the revocation is
not counted as a pass, because it proves nothing about revocation.

**Both engines, same corpus, same answer.** The full 1,000-document exhibit was
run on Postgres+pgvector and produced 535 leaks under masking and 0 under
exclusion, matching SQLite exactly. Comparing the two JSON files record by
record on the tuple (vector, probe id, subject, doc id, evidence), the 535 leak
records have symmetric difference zero.

## Quickstart

Python 3.10. The library imports the standard library only; `pytest` is needed
for the tests and `psycopg2` only for the pgvector path.

```bash
export PYTHONPATH=src            # PowerShell: $env:PYTHONPATH = "src"

# 1. Regenerate the corpus. Deterministic, seed 20260804, byte-reproducible.
python corpus/generate.py
#    patients 200  documents 1000  consents 170  identifiers 5000

# 2. Run both configurations over the same probes and diff them.
python -m consent_gate.cli exhibit --corpus corpus/data \
    --out exhibits/exhibit.md --json exhibits/exhibit.json
#    masking leaked on 535 probe(s); exclusion leaked on 0
#    revocation: all surfaces dark = True
#    exit 0

# 3. Check every figure the demo prints.
python examples/demo/verify.py
```

The reference engine, which needs a container pull on first run:

```bash
docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=consent \
    -e POSTGRES_DB=consent pgvector/pgvector:pg16
pip install psycopg2-binary

PYTHONPATH=src python -m consent_gate.cli exhibit --corpus corpus/data \
    --engine pgvector
#    masking leaked on 535 probe(s); exclusion leaked on 0
#    exit 0
```

The DSN defaults to `postgresql://postgres:consent@localhost:55432/consent`
and is overridden with `CONSENT_GATE_PG_DSN`. Loading 1,000 documents at 1,024
dimensions each takes a few seconds; nothing else here is slow.

## Command reference

`python -m consent_gate.cli exhibit`

| flag | default | what it does |
|---|---|---|
| `--corpus` | `corpus/data` | directory holding `patients.json`, `documents.json`, `consents.json`, `manifest.json` |
| `--purpose` | `direct_care` | the purpose the queries declare; one of `direct_care`, `research`, `outreach`, `quality_improvement` |
| `--engine` | `sqlite` | `sqlite` or `pgvector`; both run the same probes through the same gate |
| `--out` | stdout | markdown report path |
| `--json` | none | machine-readable output path |

| exit | meaning |
|---|---|
| `0` | exclusion leaked on nothing |
| `1` | exclusion leaked. The build should fail, loudly |
| `2` | the exhibit could not be run, **including** the case where the *masking* baseline leaked nothing |

Exit `2` on a clean masking run is the load-bearing one. If the baseline finds
no leaks, the corpus has no traps in it and the exhibit proved nothing: a green
run for the wrong reason is worse than a red one. And `2` is deliberately not
`0` for the same reason a gate that passes when it could not run is worse than
no gate, since it reports safety it never checked.

`python corpus/generate.py [--out DIR]` regenerates the corpus and prints the
counts and byte sizes. It writes four files and touches nothing else.

## How it works

A query arrives with a purpose. The gate resolves the consented subject set for
that purpose *at that instant* from the append-only consent store, and hands it
to the index, which materialises it into a temporary table and joins it into
the scan. Everything the caller sees (hits, count, page, has-more) is computed
over the joined set. There is no "total before filtering" anywhere in the
system, so there is no such number to leak.

```mermaid
flowchart LR
    Q["query + purpose"] --> G{"QueryGate"}
    G -- "no purpose, or store unreachable" --> E["empty response<br/>plus explicit error"]
    G --> S[("consent store<br/>append-only, triggers")]
    S --> C["consented subject set<br/>for this purpose, at this instant"]
    C --> I[["index scan<br/>JOIN consented c ON c.subject = d.subject"]]
    I --> R["hits, count, page, has_more<br/>all over the joined set"]
    R --> K["versioned caches<br/>stamped with consent versions"]
```

**Why post-filtering fails.** The tempting implementation is to retrieve
normally and drop non-consented records from the results. It leaks on three
surfaces at once (`src/consent_gate/gate.py`):

| surface | how post-filtering leaks |
|---|---|
| counts | "1,204 matches, showing 8": the 1,204 was computed over everyone, including the withdrawn. Facet counts the same. The number *is* the disclosure |
| scores | ranks and any normalisation are computed over the full set, so the shape of the surviving scores shifts with what was silently removed |
| pagination | a short page, or offset arithmetic that skips, marks exactly where a record was removed |

There is a correctness cost too. Top-k-then-filter returns fewer than k, and
over-fetching to compensate makes the retrieval budget depend on how many
hidden records exist, which is another oracle built to fix the first one.

`tests/test_sabotage.py` implements post-filtering deliberately and asserts the
probe suite catches it. It does, through the count channel, with no forbidden
document ever being returned.

### Design decisions

1. **Exclusion is a property of the candidate set, not of the result set.**
   Every query runs against a virtual corpus of only the records consented for
   the declared purpose. Nothing else is returned, counted, ranked or paged,
   because as far as the query is concerned nothing else is there. The next
   section is what Postgres forced us to correct about a stronger version of
   that sentence.
2. **Purpose is mandatory and the vocabulary is closed.** A query naming no
   purpose has no consented corpus to run against, so there is no default
   purpose: a default is a permission the patient never gave. The four purposes
   are an enum (`src/consent_gate/consent.py`), because a purpose the caller
   can invent is not a restriction, it is a text box that always says yes.
3. **Fail closed, and deny by default.** If the consent store cannot answer,
   the gate returns an empty result plus an explicit error and never degrades
   to unfiltered. No consent record is a denial, not an absence of opinion.
4. **Consent is append-only, enforced by database triggers.** Revocation is an
   insert, not an update, so the state at any past instant stays
   reconstructable. An application rule saying "we never update consent rows"
   is a comment; a trigger that raises on UPDATE and DELETE
   (`src/consent_gate/store.py`) is a control. A store that overwrites cannot
   answer "were we allowed to do that, then?", which is the only question an
   investigator asks.
5. **Keyed pseudonyms, and no linkage table in this package.** The consent
   store is a register of who has and has not permitted use of their data, a
   membership oracle with a schema. Subjects are HMAC pseudonyms, keyed rather
   than bare digests, because an unkeyed hash of an MRN is reversible by
   enumeration on a laptop.
6. **Caches are stamped with consent versions.** A cache keyed only on the
   query is a way for a withdrawn record to keep answering questions. Every
   entry carries the consent version of the subjects it depends on; revocation
   bumps the version and stale entries become *unreadable* rather than merely
   scheduled for eviction (`src/consent_gate/cache.py`). That is what makes the
   propagation figure a measurement and not a promise: no window exists in
   which a stale entry is still valid, only the time the next lookup takes to
   miss.
7. **The masking baseline is implemented at its theoretical best.** 100%
   identifier recall forestalls "your masker was just bad" and makes every leak
   one masking cannot fix even in principle.
8. **The similarity floor has a measured reason.** At 256 hashed dimensions a
   token present nowhere in the corpus scored 0.4 cosine against unrelated
   notes purely through hash collisions, and those collisions were served as
   search results: two queries that should both have returned nothing returned
   *different* nothing, which is a channel. Raised to 1,024 dimensions with a
   0.55 floor, recorded in `src/consent_gate/adapters/sqlite_index.py`.

## What running on Postgres disproved about our own phrase

"A non-consented record is never scored" was in this README. It is wrong, and
the pgvector run is what showed it. The full analysis is in the module
docstring of `src/consent_gate/adapters/pgvector_index.py`.

"Never scored" is a claim about the *query plan*, and the planner changes its
mind with scale. On a three-document table Postgres chose a nested loop driven
by the consented set, with `Index Cond: (subject = c.subject)`, and rows
belonging to non-consented subjects were genuinely never read. On the
thousand-document corpus it chose a hash join, and `EXPLAIN ANALYZE` is
explicit about the consequence:

```
Hash Join                        (actual rows=326)
  Hash Cond: (d.subject = c.subject)
  ->  Seq Scan on documents d    (actual rows=847)
        Filter: (lower(text) ~~ '%review%' OR (1 - (vec <=> ...)) > 0.55)
```

847 rows cleared the content filter and only then were cut to 326 by the
consent join. The cosine distance **was** computed for documents belonging to
subjects with no consent. Those two numbers are checkable independently: the
demo's live tier replays the same query and records masking reporting 847
matches for `review` where exclusion reports 326.

Nothing observable changes. Nothing non-consented is returned. The count is
computed over the joined set, 326 and not 847. Pagination is drawn from the
joined set. A token unique to a hidden record stays byte-identical to a token
that never existed. Those are what the probes verify and what a patient's
privacy actually depends on, and they held on both engines.

But the honest formulation of the guarantee is **exclusion is a property of the
candidate set: nothing non-consented is returned, counted, ranked or paged**.
It is not "never touched by the CPU". Physical non-access is a stronger
property, it is plan-dependent, and a deployment that needs it (for
side-channel reasons, or because reading the row at all is the concern) must
pin the plan or partition per purpose, and verify it on its own engine and
version. `tests/test_pgvector.py` now asserts the observable property
*regardless of join strategy*, and separately asserts that the real planner
output is published so a deployment can check its own.

That is why the exhibit prints the engine's own plan rather than a paraphrase.
Postgres, exclusion (`query_plan.exclusion` in
`exhibits/exhibit-pgvector.json`):

```
Limit  (cost=158.50..158.50 rows=1 width=136)
  ->  Sort  (cost=158.50..159.36 rows=344 width=136)
        Sort Key: (('1'::double precision - (d.vec <=> '[...1024 dims...]'::vector))) DESC, d.doc_id
        ->  Hash Join  (cost=2.80..156.78 rows=344 width=136)
              Hash Cond: (d.subject = c.subject)
              ->  Seq Scan on documents d  (cost=0.00..149.96 rows=859 width=160)
                    Filter: ((lower(text) ~~ '%review%'::text) OR (('1'::double precision - (vec <=> '[...1024 dims...]'::vector)) > '0.55'::double precision))
              ->  Hash  (cost=1.80..1.80 rows=80 width=5)
                    ->  Seq Scan on consented c  (cost=0.00..1.80 rows=80 width=5)
```

Postgres, masking (`query_plan.masking`), where no consent predicate exists to
push down:

```
Limit  (cost=158.55..158.55 rows=1 width=136)
  ->  Sort  (cost=158.55..160.70 rows=859 width=136)
        Sort Key: (('1'::double precision - (vec <=> '[...1024 dims...]'::vector))) DESC, doc_id
        ->  Seq Scan on documents d  (cost=0.00..154.25 rows=859 width=136)
              Filter: ((lower(text) ~~ '%review%'::text) OR (('1'::double precision - (vec <=> '[...1024 dims...]'::vector)) > '0.55'::double precision))
```

SQLite, the same two queries (`query_plan` in `exhibits/exhibit.json`),
exclusion then masking:

```
SCAN d
SEARCH c USING COVERING INDEX sqlite_autoindex_consented_1 (subject=?)
USE TEMP B-TREE FOR ORDER BY
```

```
SCAN d
USE TEMP B-TREE FOR ORDER BY
```

Only the 1,024-element vector literal is abbreviated in the Postgres plans, and
nothing else is touched, because the point of quoting a plan is that the reader
is seeing the engine's own words.

## Data

The corpus is entirely synthetic, generated by `corpus/generate.py` from seed
`20260804`. No real PHI enters this project at any stage: not for development,
not for calibration, not for the demo, not for a client assessment, where
clients get a synthetic mirror of their data shapes. Invented names, `MRN-ZZ-`
identifiers, 555-01xx telephone numbers and `.invalid` email domains, all
asserted by `tests/test_corpus.py`. From `corpus/data/manifest.json`:

| | |
|---|---|
| patients | 200 |
| documents | 1,000 |
| consent records | 170 |
| identifiers with known offsets | 5,000 (5 per document) |
| consent states | 80 active for `direct_care`, 30 active for `research` only, 20 expired, 30 revoked, 30 never granted, 10 future-dated |
| seeded narrative traps | 60 |
| seeded small cells | 20 |
| seeded oracle pairs | 30 |
| generator SHA-256 | `482304f5aaa31fa09fe98f540d929a291952de8d857097bd7dd4ae833a18c450` |

Byte-reproducible: regenerating into an empty directory and comparing byte for
byte against the shipped corpus gives four identical files. The manifest
carries the generator's own SHA-256, so a regenerated corpus that differs is
detectable rather than merely suspected.

## The demo

`examples/demo/index.html`, mirrored at
`E:\stable_diffusion\repos\upwork\projects\08-consent-aware-retrieval\demo\`.
Open the file in a browser. It is a single offline page: no server, no build
step, no network requests, no fonts or scripts or stylesheets loaded from
anywhere.

The narrative it walks: one query, run twice. Under masking it returns 533
matches with four non-consented records on the first page; under exclusion the
same query returns 204 and none. The page then shows what the model would have
been handed, the masked document with `[NAME]`, `[MRN]`, `[PHONE]`, `[EMAIL]`
and `[ADDRESS]` in place and the sentence that still names one person. From
there it widens to the measured difference (535 leaks against 0 over 113
probes), the search oracle (under exclusion a withdrawn record and one that
never existed share a SHA-256 fingerprint), why post-filtering leaks on counts,
scores and pagination, the query plan with the consent join inside the scan,
the revocation drill's five surfaces, and the three defects the build found in
its own exhibit code.

| file | what it holds |
|---|---|
| `index.html` | the page, self-contained and offline |
| `exhibits/exhibit.json` | a copy of the SQLite exhibit: counts, both configurations' probe results, every leak record with its evidence, the drill, both query plans |
| `exhibits/exhibit.md` | the generated markdown report |
| `verify.py` | re-derives every printed figure and exits non-zero on any mismatch |
| `run-demo.sh`, `run-demo.ps1` | the four commands end to end |

`verify.py` runs **112 checks** across four named tiers, so each figure's
source is visible in the output. `exhibit` reads the JSON copy beside it.
`manifest` cross-checks it against `corpus/data/manifest.json` (60 traps plus
20 cells plus 30 pairs plus 3 paging queries is exactly the 113 probes
reported). `live` rebuilds the store and index from `corpus/data` and replays
the demo query, the oracle pair and the page walks through both gates. `repo`
counts what pytest collects. No drill timing is asserted, since those are
wall-clock: what is asserted is the 5 s bound and the states that make the
drill a measurement, five named surfaces each visible before the revocation and
dark after it.

The Quickstart commands plus `python -m pytest -q` are what the run scripts do:

```bash
./examples/demo/run-demo.sh          # --dry-run prints without running
.\examples\demo\run-demo.ps1         # -DryRun
```

**Known state of this checkout: 111 of 112 checks pass.** The failing check is
in `verify.py` itself: it asserts pytest collects 70 tests, and pytest now
collects 80 because `tests/test_pgvector.py` was added. The gate, the exhibit
and every leak figure are unaffected. `verify.py` also still lists
"Postgres + pgvector behaviour" among the things it cannot check, which this
README supersedes.

## What the build found in itself

Three defects, all in the measurement apparatus rather than in the enforcement
path, all fixed. The exclusion result stayed at zero leaks throughout.

**1. The corpus split did not add up.** The exhibit reported 79 consented and
120 not against 200 patients. The consented count was being snapshotted *after*
the revocation drill, which revokes `P001`, so the drill's own revocation was
leaking into the corpus description. Found by adding the two published numbers
and getting 199. The snapshot is now taken before the drill
(`src/consent_gate/exhibit.py`) and reads 80 and 120. No leak figure moved,
because the probes always ran against the pre-drill split. The demo's live tier
keeps the evidence: it revokes `P001` itself, asserts the store then reports
79, and asserts that 79 is *not* what the exhibit publishes.

**2. Two of the 537 leaks were manufactured by the probe.** The pagination walk
stopped at its own page cap and then reported the resulting shortfall as a
count-channel leak, as though records had been filtered out after being
counted. A shortfall caused by the probe giving up is the probe's, not the
system's. The cap is now the named constant `MAX_PAGES = 52`
(`src/consent_gate/probes.py`) and a walk that ends that way reports nothing.
The masking total goes 537 to 535, and the `count` vector disappears from the
results rather than reading zero. The count channel itself is untouched:
masking reports 847 matches for `review` where exclusion reports 326.

**3. One drill surface measured nothing.** The revocation drill was being
called without a stratum, so its `aggregates` surface returned "dark" without
ever consulting the gate. A surface that measures nothing must not sit in a
table of measurements looking like a pass. The drill now receives the subject's
stratum and reports five genuinely measured surfaces, each visible before the
revocation and dark after it.

## Verified

- **535 leaks under masking, 0 under exclusion, over 113 probes**, on the
  200-patient, 1,000-document corpus at purpose `direct_care`,
  `2026-08-04T00:00:00+00:00`. Measured on both engines.
- **Byte-identical responses for all 30 oracle pairs under exclusion.** A
  withdrawn record and a record that never existed produce the same SHA-256
  fingerprint over hits, count, page, page size, has-more and error.
- **Postgres + pgvector, the reference path, run on the full corpus.** Same
  535, same 0, same per-vector split, and the 535 masking leak records have
  symmetric difference zero against the SQLite run on (vector, probe id,
  subject, doc id, evidence). Both engines' real EXPLAIN output is published in
  the exhibits.
- **Revocation reaches all five measured surfaces**, worst 0.0755 s (SQLite)
  and 0.0789 s (pgvector) against a 5 s target, mid-session with caches warm.
- **80 tests pass**, including 9 sabotage cases and 10 pgvector cases. Each
  sabotage breaks enforcement the way a tired engineer plausibly would: ignore
  the expiry, honour the grant but not the revocation, ignore the purpose,
  filter after ranking, forget to stamp the cache, fail open. Each is caught.
- **The corpus is byte-reproducible** from seed `20260804`: all four files
  regenerate identical.

## Not verified

- **Timing.** Exclusion closes the content channel. Response time is a separate
  channel and is not claimed closed. Closing it would mean measuring the
  latency distribution over the 30 oracle pairs under exclusion, publishing it
  whichever way it comes out, and constant-timing the gap if one exists. That
  is scoped work, not a checkbox.
- **Physical non-access on any engine other than the two measured here, or at
  any other scale.** The section above is the whole reason: the property is
  plan-dependent. What would settle it for a given deployment is
  `EXPLAIN ANALYZE` on that engine, that version and that data volume, plus a
  pinned plan or a per-purpose partition.
- **Approximate nearest-neighbour behaviour under a restrictive filter.** Both
  adapters use exact scans with no ANN index, so recall is exact and the join
  semantics are unambiguous. A filter can make an HNSW or IVFFlat traversal
  return fewer than `k` rows, and engines differ in whether they compensate. A
  deployment that adds an ANN index must re-verify both properties itself.
- **Corpus totals.** If overall corpus size is published, consented-count
  changes leak consent *events* by differencing. This gate exposes no totals; a
  deployment that does reopens the channel.
- **Retrieval quality.** Embeddings are a deterministic hashed bag of words so
  a sceptic can reproduce the run without downloading a model. Swapping in a
  real embedding model does not change where the consent predicate is applied,
  which is the claim being made.
- **Differential privacy for aggregates over consented data.** A different tool
  for a different problem: exclusion fixes membership, DP perturbs values.
  Compatible, out of scope.

## Development

```bash
python -m pytest -q                 # 80 tests
```

All 80 pass. `tests/test_pgvector.py` skips loudly when no Postgres is
reachable at the DSN, printing the `docker run` line, rather than passing
silently: a green suite that never exercised the reference adapter is a false
report.

```
src/consent_gate/
    consent.py          consent record, purposes, keyed pseudonyms, allow()
    store.py            append-only SQLite store, triggers, decision log
    gate.py             QueryGate (exclusion) and MaskingGate (baseline)
    cache.py            consent-version-stamped caches
    probes.py           narrative, small cell, oracle, pagination probes
    revocation.py       the mid-session drill, per surface
    exhibit.py          runs both configurations, writes the report
    cli.py              the exhibit subcommand and its exit codes
    adapters/           sqlite_index.py, pgvector_index.py, one interface
corpus/generate.py      seeded synthetic corpus, byte-reproducible
tests/                  corpus, gate, sabotage, pgvector
exhibits/               exhibit.{md,json}, exhibit-pgvector.{md,json}
examples/demo/          offline page, exhibit copy, verify.py, run scripts
```

Python 3.10. The library is standard library only (`sqlite3`, `hashlib`,
`hmac`, `struct`). `pytest` for the suite. `psycopg2` for the pgvector adapter
only, imported inside the adapter so the rest of the package does not depend on
it.

**RMAD evidence** (`PROJECT.md`): `rmad doctor` 69 passed, 0 failed, HEALTHY.
`rmad index build` 18,414 LOC, 0 extraction errors, exact Python AST. Task
`P08-GATE`, 9 acceptance criteria, all symbol-mapped. Obligations satisfied on
the source commit: O1 criteria realised, O3 no regression, O4 no structural
debt, O6 observed running. `PROJECT.md` records no residual R value.
