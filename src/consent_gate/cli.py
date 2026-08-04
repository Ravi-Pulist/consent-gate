"""Command line: the exhibit, and the sabotage check that proves it works.

Exit codes are load-bearing:

- ``0`` — exclusion leaked on nothing.
- ``1`` — exclusion leaked. The build should fail, loudly.
- ``2`` — the exhibit could not be run. Deliberately not ``0``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .consent import Purpose
from .exhibit import as_json, markdown, run

EXIT_OK, EXIT_LEAKED, EXIT_INCOMPLETE = 0, 1, 2


def _use_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_exhibit(args) -> int:
    ex = run(Path(args.corpus), Purpose.parse(args.purpose))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(markdown(ex), encoding="utf-8")
        print(f"report -> {args.out}")
    else:
        print(markdown(ex))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(as_json(ex), indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"json   -> {args.json}")

    m, e = len(ex.masking.leaks), len(ex.exclusion.leaks)
    print(f"\nmasking leaked on {m} probe(s); exclusion leaked on {e}",
          file=sys.stderr)
    print(f"revocation: all surfaces dark = {ex.drill['all_dark']}, "
          f"worst {ex.drill['worst_seconds']:.4f}s", file=sys.stderr)

    if e:
        print("EXCLUSION LEAKED — this is a failure, not a caveat",
              file=sys.stderr)
        return EXIT_LEAKED
    if not m:
        # If the baseline leaks nothing, the corpus has no traps in it and
        # the exhibit proves nothing. A green run for the wrong reason is
        # worse than a red one.
        print("the masking baseline leaked nothing, so this corpus cannot "
              "demonstrate anything; check the traps", file=sys.stderr)
        return EXIT_INCOMPLETE
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="consent-gate",
        description="Consent as exclusion: a record without purpose-bound "
                    "consent is not retrieved, not counted, not aggregated, "
                    "and cannot be inferred to exist.")
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("exhibit", help="run both configurations and diff them")
    x.add_argument("--corpus", default="corpus/data")
    x.add_argument("--purpose", default="direct_care")
    x.add_argument("--out")
    x.add_argument("--json")
    x.set_defaults(fn=cmd_exhibit)
    return p


def main(argv=None) -> int:
    _use_utf8()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as e:
        print(f"could not read {e.filename}", file=sys.stderr)
        return EXIT_INCOMPLETE


if __name__ == "__main__":
    raise SystemExit(main())
