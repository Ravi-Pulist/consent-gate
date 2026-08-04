# consent-gate demo

`index.html` is a single offline page. Open it in a browser; it makes no network
requests and loads no fonts, scripts or stylesheets from anywhere.

It shows one thing: the difference between masking identifiers and excluding
records, measured on the same corpus with the same probes under both
configurations.

## What is on the page

| section | claim | source |
|---|---|---|
| one query, two configurations | the same query returns 4 non-consented records under masking and 0 under exclusion, with counts of 533 and 204 | live replay of the recorded query through `MaskingGate` and `QueryGate` |
| what the model would have been handed | the masked document, with name, MRN, phone, email and address replaced, still names one person | live replay; the body text is also at `exhibits/exhibit.json` `masking.leaks[0].evidence` |
| the measured difference | 537 leaks under masking, 0 under exclusion, across 113 probes | `exhibits/exhibit.json` `masking.by_vector`, `exclusion.by_vector` |
| the narrative leak | 489 leak events over 60 probes, 59 documents, 26 people, quoted | `exhibits/exhibit.json` narrative leaks |
| the search oracle | under exclusion a withdrawn record and a record that never existed share one SHA-256 response fingerprint | live replay of `manifest.oracle_pairs[0]`; totals from the exhibit |
| why post-filtering does not work | counts, scores and pagination each leak | `src/consent_gate/gate.py` docstring, `tests/test_sabotage.py` |
| the query plan | the consent predicate is inside the scan | `exhibits/exhibit.json` `query_plan` |
| revocation, measured | every surface dark, worst surface 0.0799 s against a 5 s target | `exhibits/exhibit.json` `revocation_drill` |
| what this does not close | the README's not-verified list, plus three defects found while building this page | `README.md`, plus `verify.py` |

Nothing on the page was typed from memory. `verify.py` re-derives every figure
and prints a pass/fail tally.

## Reproduce it

From the repository root. `src/consent_gate` imports the standard library only;
pytest is needed for the test suite and nothing else is.

```bash
export PYTHONPATH=src
python corpus/generate.py
python -m consent_gate.cli exhibit --corpus corpus/data \
    --out exhibits/exhibit.md --json exhibits/exhibit.json
python -m pytest -q
python examples/demo/verify.py
```

On Windows PowerShell, `$env:PYTHONPATH = "src"` and the same four commands.
Either run script does all of it from the repo root:

```bash
./examples/demo/run-demo.sh          # add --dry-run to print without running
.\examples\demo\run-demo.ps1         # add -DryRun
```

The exhibit's exit code is load-bearing: `0` if exclusion leaked nothing, `1` if
it leaked, `2` if it could not run. `2` includes the case where the *masking*
baseline leaked nothing, because that means the corpus has no traps in it and a
green run would be green for the wrong reason.

## What reproduces exactly, and what does not

Corpus generation is deterministic and byte-reproducible from seed `20260804`.
The manifest carries the generator's own SHA-256,
`482304f5aaa31fa09fe98f540d929a291952de8d857097bd7dd4ae833a18c450`, so a
regenerated corpus that differs is detectable rather than merely suspected.

Everything the page prints from that corpus is deterministic: leak counts, the
quoted evidence, ranked hit lists, response fingerprints and query plans.

The revocation drill's seconds are wall-clock measurements and will differ on
your machine. Measured here by running the four commands above into a scratch
copy of the repository: all four corpus files came back byte-identical, and the
re-run exhibit differed from the shipped one in exactly three fields, all of
them drill timings (`worst_seconds` 0.0799 to 0.0682, `total_seconds` 0.16 to
0.1444, and the same worst figure inside `surfaces[0]`). Every leak, quote,
count, fingerprint and query plan was identical. Those second-run timings are
recorded here and nowhere else, so treat them as one observation rather than as
a published figure.

`verify.py` reads the exhibit copy in this folder by default, so re-running the
exhibit in the repository does not change what it checks. Point it at a fresh
run with `--exhibits ../../exhibits` and the drill timings are the fields
expected to disagree.

## verify.py

```bash
python examples/demo/verify.py              # all four tiers
python examples/demo/verify.py --no-live    # skip the replay, JSON tiers only
python examples/demo/verify.py --exhibits ../../exhibits
```

Each check names its source:

- `exhibit` reads `examples/demo/exhibits/exhibit.json`, the copy beside this
  file.
- `manifest` reads `corpus/data/manifest.json` and cross-checks the exhibit
  against the seeded traps: 60 narrative traps plus 20 small cells plus 30
  oracle pairs plus 3 paging queries is the 113 probes the exhibit reports, 30
  oracle pairs sit on non-consented subjects and 30 oracle leaks were found, 16
  small cells hold a non-consented member and 16 small-cell leaks were found.
- `live` rebuilds the store and index from `corpus/data` and replays the demo
  query, the oracle pair and the page walks through both gates. Reports SKIP if
  `src/` or `corpus/data/` are absent.
- `repo` counts what pytest collects out of `tests/`.

It exits 0 when every check that ran passed, and lists what it could not check:
byte-reproducibility of the corpus, Postgres and pgvector behaviour, and the
response-time channel.

Current state of this checkout: 108 of 108 checks pass, 0 skipped.

## Synthetic data

Every patient, note, identifier and address is generated. Invented names,
`MRN-ZZ-` identifiers, 555-01xx telephone numbers, `.invalid` email domains, all
asserted by test in `tests/test_corpus.py`. No real PHI enters this project, and
none is needed to show the failure.
