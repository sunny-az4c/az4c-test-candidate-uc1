"""Entry point for your system. Run these from the code/ folder:

    python -m app run --case ../samples/UC1-S01.json --out out/UC1-S01.json
    python -m app run --case <case.json> --out <result.json> --auto-approve
    python -m app approve <run_id> --action approve|reject|change
    python -m app eval --cases tests/cases

Keep the command names and options. Everything behind them is yours to build,
in this package or in modules it imports.

eval writes tests/score.json when it runs tests/cases, in this format:
    {"cases": 20, "score": 85.0, "per_case": [{"case_id": "...", "score": 1.0}]}
score is 0 to 100; each per_case score is 0 to 1.
"""
import argparse
import json
import sys
from pathlib import Path

from .config import CODE_DIR
from .orchestrator import approve, run_case


def score_case(result: dict, expected: dict) -> float:
    """Share of the scored fields we got right: decision, reason, amount, citations, needs_human."""
    checks = [result.get("decision") == expected["decision"],
              result.get("reason_code") in expected["reason_code"],
              result.get("needs_human") is expected["needs_human"]]
    if expected.get("amount"):
        a = result.get("amount")
        checks.append(a is not None and abs(a - expected["amount"]["value"]) <= expected["amount"]["tolerance"])
    cites = " ".join(result.get("citations") or [])
    checks += [c in cites for c in expected.get("must_cite", [])]
    return round(sum(checks) / len(checks), 3)


def cmd_run(args) -> None:
    case = json.loads(Path(args.case).read_text(encoding="utf-8"))
    result = run_case(case, args.auto_approve)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_eval(args) -> None:
    cases_dir = Path(args.cases).resolve()
    expected_dir = cases_dir / "expected"
    if not expected_dir.is_dir():
        expected_dir = cases_dir.parent / "expected"
    per_case = []
    for case_file in sorted(cases_dir.glob("*.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        expected = json.loads((expected_dir / case_file.name).read_text(encoding="utf-8"))
        try:
            result = run_case(case, auto_approve=True)
            out = CODE_DIR / "out" / case_file.name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            score = score_case(result, expected)
        except Exception as e:
            print(f"{case['case_id']}: {type(e).__name__}: {e}", file=sys.stderr)
            score = 0.0
        per_case.append({"case_id": case["case_id"], "score": score})
        print(f"{case['case_id']}  {score:.2f}")
    total = round(100 * sum(c["score"] for c in per_case) / len(per_case), 1) if per_case else 0.0
    print(f"Score: {total}% over {len(per_case)} cases")
    if cases_dir.is_relative_to(CODE_DIR / "tests"):
        report = {"cases": len(per_case), "score": total, "per_case": per_case}
        (CODE_DIR / "tests" / "score.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def cmd_approve(args) -> None:
    approve(args.run_id, args.action)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description="UC1: Invoice exception desk")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run one case end to end and write the result")
    p.add_argument("--case", required=True, help="case JSON file")
    p.add_argument("--out", required=True, help="where to write the result JSON")
    p.add_argument("--auto-approve", action="store_true", help="approve at the approval step without waiting")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("eval", help="run every case in a folder and print the score")
    p.add_argument("--cases", required=True, help="folder of case JSON files")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("approve", help="resume a paused run")
    p.add_argument("run_id")
    p.add_argument("--action", required=True, choices=["approve", "reject", "change"])
    p.set_defaults(func=cmd_approve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
