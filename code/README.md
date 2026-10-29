# Invoice exception desk

An agent system that checks one supplier invoice against its PO, the goods received and Kestrel's AP policy, and decides to pay, hold or escalate it.

## Set up and run

```
cd code
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # add GROQ_API_KEY
python -m app run --case ../samples/UC1-S01.json --out out/UC1-S01.json --auto-approve
python -m app eval --cases tests/cases
```

Without `--auto-approve`, holds, escalations and payments over ₹5 lakh pause for the AP manager: `python -m app approve <run_id> --action approve|reject|change`.

## Design

| Agent | Job | Tools |
| --- | --- | --- |
| Investigator | Gathers the facts for all eight policy checks | Typed lookups over the AP data, line comparison, duplicate search, agreements, emails, date and % calculators |
| Decider | Picks the reason and citations from the findings, under the full AP policy | None |
| Reviewer | Checks findings and decision against the policy; can send the work back once, to the investigator or the decider | None |
| Vendor writer | Drafts the hold email to the vendor (AP-POL 9.1) | None |

The orchestrator (`app/orchestrator.py`) runs them and applies the policy's hard rules in code: the priority order of reasons (7.1), the decision for each reason (7), the amount (invoice total) and the approval rule (8.1, 8.2).

## Tools

All in `app/tools.py`. Arithmetic is done in tools, never by the model. Tools return `{"error": ...}` instead of raising, and the model sees the error.

## Models used and why

`openai/gpt-oss-120b` on Groq for every agent: reliable tool calling and JSON. Set `GROQ_MODEL` to change it.

## Failure handling

- Rate limits and server errors: the Groq SDK retries up to 6 times, honouring Retry-After.
- A reply Groq rejects as a bad tool call or bad JSON is asked again (up to 3 times).
- A final answer that isn't valid JSON gets one more try.
- The result is validated against `schema/result.schema.json` before anything is written.

## Tests and score

`tests/cases` holds the five samples for now. `python -m app eval --cases tests/cases` writes `tests/score.json`.

## Traces

One file per run in `traces/`: every model call (agent, model, tokens, seconds), every tool call and every routing decision.
