#!/usr/bin/env python3
"""Refresh the visa statistics dashboard data.

    python3 scripts/build_visa_stats.py [--output visa/data/latest.json] [--dry-run]

Run daily from CI. A run that fetches nothing does not blank an existing
dashboard: the previous series are kept and only the freshness block is
updated, so a data.gov.au outage shows as "last checked" moving without the
numbers changing.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from visa_stats.build import build  # noqa: E402
from visa_stats.sources import SPECS  # noqa: E402

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "visa" / "data" / "latest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="SERIES_ID",
        help="build only these series (repeatable); default builds all",
    )
    parser.add_argument("--dry-run", action="store_true", help="print, do not write")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    specs = tuple(s for s in SPECS if not args.only or s.id in args.only)
    if not specs:
        print(f"no series matched {args.only}", file=sys.stderr)
        return 2

    payload = build(specs)
    previous = _read_existing(args.output)
    payload = _preserve_on_total_failure(payload, previous)

    run = payload["run"]
    print(
        f"status={run['status']} "
        f"series={run['series_available']}/{run['series_total']} "
        f"sources={len(payload['sources'])}"
    )
    for problem in run["problems"]:
        print(f"  ! {problem['series']}: {problem['reason']}", file=sys.stderr)

    if args.dry_run:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        print()
        return 0 if run["status"] != "failed" else 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")

    return 0 if run["status"] != "failed" else 1


def _read_existing(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _preserve_on_total_failure(payload: dict, previous: dict | None) -> dict:
    """Keep the last good numbers when a run fetches nothing at all."""
    if payload["run"]["status"] != "failed" or not previous:
        return payload

    kept = [s for s in (previous.get("series") or {}).values() if s.get("available")]
    if not kept:
        return payload

    logging.warning("run fetched nothing; retaining %d previously published series", len(kept))
    merged = dict(previous)
    merged["last_checked_at"] = payload["generated_at"]
    merged["run"] = dict(payload["run"]) | {"retained_previous": True}
    return merged


if __name__ == "__main__":
    raise SystemExit(main())
