"""Command line entry points, for running the audit without starting the API.

    python -m alcohol_audit report                 # full report as JSON
    python -m alcohol_audit report --indent 2      # pretty-printed
    python -m alcohol_audit deck audit.pptx        # build the slide deck
    python -m alcohol_audit excel merged.xlsx      # write the merged workbook
    python -m alcohol_audit summary                # human-readable headlines
    python -m alcohol_audit score 8                # one CIWA score in detail

Every command accepts ``--from`` and ``--to`` to restrict the period, and
``--data-dir`` to point at the source workbooks.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import analytics
from .deck import build_deck
from .etl import load_dataset
from .export import export_merged_workbook


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _rows(args):
    dataset = load_dataset(args.data_dir)
    rows = dataset.filtered(date_from=getattr(args, "date_from", None),
                            date_to=getattr(args, "date_to", None))
    if not rows:
        raise SystemExit("No attendances match those filters.")
    return rows


def _summary(rows) -> str:
    report = analytics.full_report(rows)
    overview, treatment = report["overview"], report["treatment"]
    documentation, repeats = report["ciwa_documentation"], report["ciwa_repeats"]
    outcomes, scores = report["outcomes"], report["ciwa_scores"]
    period = overview["period"]

    lines = [
        "A&E alcohol withdrawal audit",
        f"  Period                  {period['start']} to {period['end']} ({period['days']} days)",
        f"  Attendances             {overview['attendances']} by {overview['unique_patients']} patients",
        f"  Case notes reviewed     {overview['case_notes_reviewed']['count']} ({overview['case_notes_reviewed']['percent']}%)",
        "",
        f"  CIWA score documented   {documentation['documented']['count']} ({documentation['documented']['percent']}%)",
        f"  Treated                 {treatment['treated']['count']} ({treatment['treated']['percent']}%)",
        f"    benzodiazepine        {treatment['benzodiazepine']['count']}",
        f"    thiamine / Pabrinex   {treatment['thiamine_pabrinex']['count']}",
        f"    IV fluids             {treatment['iv_fluids']['count']}",
        f"  Treated with no score   {documentation['treated_without_any_ciwa_score']['count']} of {documentation['treated_without_any_ciwa_score']['denominator']}",
        f"  Score repeated          {repeats['repeated']['count']} of {repeats['denominator_scored_attendances']} scored"
        f" ({repeats['patients_with_a_repeat_score']} "
        f"{'patient' if repeats['patients_with_a_repeat_score'] == 1 else 'patients'})",
        "",
        f"  Admitted                {outcomes['admitted']['count']} ({outcomes['admitted']['percent']}%)",
        f"  Discharged              {outcomes['discharged']['count']} ({outcomes['discharged']['percent']}%)",
        f"  Self-discharged         {outcomes['self_discharged']['count']} ({outcomes['self_discharged']['percent']}%)",
        f"  Died                    {outcomes['died']['count']}",
        f"  Outcome not recorded    {outcomes['not_recorded']['count']}",
        "",
        "  Initial CIWA score      n   treated",
    ]
    for row in scores["by_score"]:
        lines.append(f"    {row['score']:>3}                 {row['attendances']:>3}   {row['treated']:>3}"
                     f" ({row['treated_percent']}%)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alcohol_audit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="Directory holding the two source workbooks.")
    parser.add_argument("--from", dest="date_from", type=_date, default=None,
                        help="Earliest arrival date to include (YYYY-MM-DD).")
    parser.add_argument("--to", dest="date_to", type=_date, default=None,
                        help="Latest arrival date to include (YYYY-MM-DD).")

    commands = parser.add_subparsers(dest="command", required=True)
    report_cmd = commands.add_parser("report", help="Print the full report as JSON.")
    report_cmd.add_argument("--indent", type=int, default=None)
    commands.add_parser("summary", help="Print the headline figures as text.")
    deck_cmd = commands.add_parser("deck", help="Build the PowerPoint deck.")
    deck_cmd.add_argument("output", type=Path, help="Path to write the .pptx to.")
    score_cmd = commands.add_parser("score", help="Detail for one CIWA-Ar score.")
    score_cmd.add_argument("value", type=int)
    excel_cmd = commands.add_parser(
        "excel",
        help="Write the merged source rows to one Excel sheet, NA for missing data.",
    )
    excel_cmd.add_argument("output", type=Path, help="Path to write the .xlsx to.")

    args = parser.parse_args(argv)

    if args.command == "excel":
        # The export is a faithful merge of the source rows, so it is not
        # filtered by period: --from and --to do not apply to it.
        path, counts = export_merged_workbook(args.output, args.data_dir)
        print(f"Wrote {path}")
        for label, value in counts.items():
            print(f"  {label.replace('_', ' '):<34} {value}")
        return 0

    rows = _rows(args)

    if args.command == "report":
        json.dump(analytics.full_report(rows), sys.stdout, indent=args.indent)
        sys.stdout.write("\n")
    elif args.command == "summary":
        print(_summary(rows))
    elif args.command == "score":
        json.dump(analytics.score_detail(rows, args.value), sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.command == "deck":
        path = build_deck(analytics.full_report(rows), args.output)
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
