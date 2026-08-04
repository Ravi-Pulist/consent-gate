# consent-gate

**Consent as exclusion, not masking.** A record without an active,
purpose-bound consent is not retrieved, not counted, not aggregated, never
reaches the prompt, and cannot be told apart from a record that does not
exist.

Masking answers the wrong question. It hides *who*; consent governs
*whether*. The two are not the same, and the gap between them is measurable.

## The three leaks

Measured on a 200-patient, 1,000-document synthetic corpus with seeded traps,
under a masking configuration that redacts every direct identifier at **100%
recall** — possible only because the corpus is synthetic and every
identifier's offset is known. Every leak below survives a *perfect* masker.

**The narrative.** Masking removes the name and leaves the sentence:

> This contact was a scheduled review of Harrowfield anaemia. Beacon, as the
> ward has called them since childhood, is 42 and still working as a lock
> keeper on the Cinderfell shorefront.

Name, MRN, phone, email and address were all redacted from that document. It
still describes one person, and that person withdrew consent.

**Small cells.** A count over a stratum of two patients discloses both. It
does not matter that the records were masked — they were *counted*, and the
count is the disclosure.

**The search oracle.** A hit, a count, even an empty result set is
information about who is in the index. Query a token unique to a withdrawn
patient's record, then query a token that exists nowhere. Any observable
difference answers "is this person in your data?"

## The result

| | masking | exclusion |
|---|---|---|
| probes run | 113 | 113 |
| **total leaks** | **535** | **0** |
| narrative | 489 | 0 |
| oracle | 30 | 0 |
| small cell | 16 | 0 |

Revocation, measured mid-session with caches warm: every surface dark —
vector path, keyword path, aggregates, result cache, prompt context — in
**0.08 s**, against a 5 s target.

## Quickstart

```bash
export PYTHONPATH=src
python corpus/generate.py                       # deterministic, seed 20260804
python -m consent_gate.cli exhibit --corpus corpus/data \
    --out exhibits/exhibit.md --json exhibits/exhibit.json
```

Exit `0` if exclusion leaked nothing, `1` if it leaked, `2` if the exhibit
could not run — including the case where the *masking* baseline leaked
nothing, because that means the corpus has no traps in it and a green run
would be green for the wrong reason.

## Why post-filtering does not work

The tempting implementation is to retrieve normally and drop non-consented
records from the results. It leaks on three surfaces at once:

| surface | how post-filtering leaks |
|---|---|
| counts | "1,204 matches, showing 8" — the 1,204 was computed over everyone, including the withdrawn |
| scores | ranks and normalisation are computed over the full set, so the shape of the surviving scores shifts with what was removed |
| pagination | a short page, or offset arithmetic that skips, marks exactly where a record was removed |

There is a correctness cost too: top-k-then-filter returns fewer than k, and
over-fetching to compensate makes the retrieval budget depend on how many
hidden records exist — another oracle, built to fix the first one.

The rule that resolves all of it: **exclusion is a property of the candidate
set, not of the result set.** Every query runs against a virtual corpus
containing only the records consented for the declared purpose.

`tests/test_sabotage.py` implements post-filtering deliberately and asserts
the probe suite catches it. It does — through the count channel, without any
forbidden document being returned.

## The predicate is inside the query plan

Not applied to results afterwards. This is the engine's own plan for the
query that ran:

```
SCAN d
SEARCH c USING COVERING INDEX sqlite_autoindex_consented_1 (subject=?)
USE TEMP B-TREE FOR ORDER BY
```

`c` is the consented subject set, joined into the scan. A reader can check
this rather than take it on trust; `SqliteIndex.explain()` prints it for any
query.

## Design commitments

- **Purpose is mandatory.** Consent is purpose-bound in both GDPR and India's
  DPDP, so a query naming no purpose has no consented corpus to run against.
  There is no default purpose, because a default is a permission the patient
  never gave.
- **Fail closed.** If the consent store cannot answer, the gate returns an
  empty result and an explicit error. It never degrades to unfiltered.
- **Deny by default.** No consent record is a denial, not an absence of
  opinion.
- **Append-only, enforced by the database.** Revocation is an insert, not an
  update, so the consent state at any past instant stays reconstructable. A
  store that overwrites cannot answer "were we allowed to do that, then?" —
  the only question an investigator asks. Triggers raise on UPDATE and DELETE.
- **Keyed pseudonyms, no linkage table.** A consent store is a register of
  who has and has not permitted use of their data — a membership oracle with
  a schema. It must not also be able to name people.
- **Caches are stamped with consent versions.** A cache keyed only on the
  query is a way for a withdrawn record to keep answering questions.
  Revocation bumps the version and stale entries become *unreadable*, not
  merely scheduled for eviction.

## Verified

- 535 leaks under masking, 0 under exclusion, on 113 probes over the
  published corpus.
- Byte-identical responses for all 30 oracle pairs under exclusion: a
  withdrawn record and a record that never existed produce the same response
  fingerprint (SHA-256 over hits, count, page, page size, has-more, error).
- Revocation propagates to every measured surface in 0.08 s.
- 70 tests, including 9 sabotage cases. Each sabotage breaks enforcement the
  way a tired engineer plausibly would — ignore the expiry, honour the grant
  but not the revocation, filter after ranking, forget to stamp the cache,
  fail open — and each is caught.

## Not verified

- **Timing.** Exclusion closes the content channel. Response time is a
  separate channel, and it is not claimed closed. Measuring it is scoped work,
  not a checkbox.
- **Corpus totals.** If overall corpus size is published, consented-count
  changes leak consent *events* by differencing. This gate exposes no totals;
  a deployment that does reopens the channel.
- **Postgres + pgvector.** The adapter interface is the extension point and
  the reference path is specified, but the pgvector adapter has not been run
  here — no Postgres was available in this environment. The SQLite path is
  what the numbers above were measured on, and its query plan is what is
  quoted. Claiming a pgvector result without running it would be exactly the
  vendor behaviour this project exists to oppose.
- **Retrieval quality** is not what the exhibit tests. Embeddings are a
  deterministic hashed bag of words so a sceptic can reproduce the run
  without downloading a model. Swapping in a real embedding model does not
  change where the consent predicate is applied, which is the claim.
- **Differential privacy for aggregates over consented data** is a different
  tool for a different problem: exclusion fixes membership, DP perturbs
  values. Compatible, out of scope.

## Synthetic data

No real PHI enters this project — not for development, not for the demo, not
for a client assessment; clients get a synthetic mirror of their data shapes.
`corpus/generate.py` is deterministic and byte-reproducible from seed
`20260804`, and the manifest carries the generator's own SHA-256. Invented
names, `MRN-ZZ-` identifiers, 555-01xx phones and `.invalid` email domains,
asserted by test.

## Tests

```bash
python -m pytest -q
```
