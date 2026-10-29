"""Runs one invoice through the agents, applies the policy's hard rules in code, and handles approval."""
import json
import sys
from datetime import datetime, timezone

import jsonschema

from . import agents, config, tools
from .trace import Trace

DECISION_FOR = {"CLEAN_MATCH": "pay", "QTY_SHORT": "hold", "PRICE_VARIANCE": "hold", "GST_MISMATCH": "hold",
                "MISSING_GRN": "hold", "DUPLICATE": "escalate", "VENDOR_BLOCKED": "escalate",
                "BANK_CHANGE_RISK": "escalate", "UNKNOWN_PO": "escalate"}
ERP_STATUS = {"pay": "approved_for_payment", "hold": "on_hold", "escalate": "escalated"}


def run_case(case: dict, auto_approve: bool) -> dict:
    trace = Trace(case["case_id"])
    invoice_id = case["input"]["invoice_id"]
    findings = agents.investigate(trace, invoice_id)
    decision = agents.decide(trace, findings)

    # The reviewer may send the work back once, to whichever agent got it wrong.
    for _ in range(config.MAX_REVIEW_ROUNDS):
        verdict = agents.review(trace, findings, decision)
        if verdict.get("agree", True):
            break
        issues = verdict.get("issues") or ["recheck"]
        trace.event("orchestrator", f"reviewer sent it back to the {verdict.get('send_back_to') or 'decider'}", issues)
        if verdict.get("send_back_to") == "investigator":
            findings = agents.investigate(trace, invoice_id, feedback=issues)
        decision = agents.decide(trace, findings, feedback=issues)

    result = finalise(trace, case, findings, decision)
    jsonschema.validate(result, json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8")))

    if result["needs_human"] and not auto_approve:
        save_pending(trace, result, findings)
    else:
        commit(trace, result, findings, approved_by="auto-approve" if result["needs_human"] else "not required")
    trace.save(result)
    return result


def finalise(trace: Trace, case: dict, findings: dict, decision: dict) -> dict:
    """Hard rules from the policy, applied in code rather than trusted to a model."""
    failed = [r for r in agents.REASONS if findings.get("checks", {}).get(r.lower(), {}).get("failed")]
    expected_main = failed[0] if failed else "CLEAN_MATCH"
    reason = decision.get("reason_code")
    if reason != expected_main:  # AP-POL 7.1: main reason is the first failure in priority order
        trace.event("orchestrator", f"guard: reason {reason} -> {expected_main} (priority order from findings)")
        reason = expected_main

    invoice = tools.get_invoice(case["input"]["invoice_id"])
    total = invoice.get("total")
    action = DECISION_FOR[reason]
    cites = list(decision.get("citations") or [])
    for i in [invoice.get("invoice_id"), findings.get("po_id"), *findings.get("grn_ids", []), *findings.get("agreement_ids", [])]:
        if i and i not in cites:
            cites.append(i)
    return {
        "case_id": case["case_id"],
        "invoice_id": case["input"]["invoice_id"],
        "decision": action,
        "reason_code": reason,
        "other_reasons": [r for r in failed if r != reason],
        "amount": total,
        "citations": cites,
        "needs_human": action != "pay" or (total or 0) > config.APPROVAL_LIMIT,  # AP-POL 8.1, 8.2
        "summary": decision.get("summary"),
        "trace_id": trace.trace_id,
    }


def save_pending(trace: Trace, result: dict, findings: dict) -> None:
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.RUNS_DIR / f"{trace.trace_id}.json"
    path.write_text(json.dumps({"result": result, "findings": findings}, indent=2, ensure_ascii=False), encoding="utf-8")
    trace.event("orchestrator", "waiting for the AP manager")
    print(f"\n{result['case_id']}: {result['decision']} ({result['reason_code']}) needs the AP manager's approval.\n"
          f"  python -m app approve {trace.trace_id} --action approve|reject|change", file=sys.stderr)


def commit(trace: Trace, result: dict, findings: dict, approved_by: str) -> None:
    """Write to the mock ERP, only once the decision is final (COMMON: writes to the mock system)."""
    config.MOCK_DIR.mkdir(parents=True, exist_ok=True)
    invoice_id = result["invoice_id"]
    line = {"invoice_id": invoice_id, "status": ERP_STATUS[result["decision"]], "reason": result["reason_code"],
            "approved_by": approved_by, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with open(config.MOCK_DIR / "erp_payment_status.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    if result["decision"] == "hold":
        email = agents.write_vendor_email(trace, findings, result)
        (config.MOCK_DIR / "outbox").mkdir(exist_ok=True)
        (config.MOCK_DIR / "outbox" / f"{invoice_id}.md").write_text(
            f"**Subject:** {email.get('subject', '')}\n\n{email.get('body', '')}\n", encoding="utf-8")
    trace.event("orchestrator", f"recorded in the ERP as {line['status']}")


def approve(run_id: str, action: str) -> None:
    path = config.RUNS_DIR / f"{run_id}.json"
    if not path.is_file():
        sys.exit(f"No paused run {run_id}")
    pending = json.loads(path.read_text(encoding="utf-8"))
    result, findings = pending["result"], pending["findings"]
    trace = Trace(result["case_id"])
    if action == "reject":
        trace.event("orchestrator", "rejected by the AP manager; nothing written")
    else:
        if action == "change":
            reason = input(f"New reason code {sorted(DECISION_FOR)}: ").strip().upper()
            if reason not in DECISION_FOR:
                sys.exit(f"Unknown reason {reason}")
            result.update(reason_code=reason, decision=DECISION_FOR[reason])
        commit(trace, result, findings, approved_by="AP manager")
    path.unlink()
    trace.save(result)
