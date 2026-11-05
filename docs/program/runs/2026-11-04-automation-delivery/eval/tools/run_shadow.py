#!/usr/bin/env python3
"""Frozen SHADOW review calls for the reviewer-candidate evaluation.

Uses the production reviewer path (route_reviewer -> build_adapter -> adapter.invoke with the same
prompt builder and output schema) but writes NO control-plane state: no store, no lease, no
assignment, no verdict ingestion. Results are therefore never merge-eligible.

The runner reads ONLY the reviewer-visible case packs (--cases) and never opens the answer key.
Provider call identity (session/run ids) is minted here before the call and verified by the adapter.

  python3 run_shadow.py --repo ROOT --cases DIR --out DIR [--only C1,C2] --allow-candidate-bindings
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import uuid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--max-budget-usd", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--allow-candidate-bindings", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.repo).resolve()
    sys.path.insert(0, str(root / "tools/agentctl"))
    from agentctl import execute
    from agentctl.adapters.claude_cli import ClaudeCliAdapter
    from agentctl.routing import route_reviewer

    cases = pathlib.Path(args.cases)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted = {c for c in args.only.split(",") if c}
    ids = sorted(p.name for p in cases.iterdir() if p.is_dir() and (not wanted or p.name in wanted))
    route = route_reviewer(
        "standard_implementation", risk="low", root=root, check_auth=True,
        allow_candidate_bindings=args.allow_candidate_bindings,
    )
    if not route.available:
        print(json.dumps({"refused": True, "route": route.to_payload()}, indent=2))
        return 3
    for cid in ids:
        target = out / f"{cid}.json"
        if target.exists():
            print(f"{cid}: result exists, not repeating (no blind retries)")
            continue
        d = cases / cid
        pack = json.loads((d / "pack.json").read_text())
        contract = json.loads((d / "contract.json").read_text())
        diff = (d / "patch.diff").read_text()
        changed_files = [(p, t) for p, t in json.loads((d / "changed_files.json").read_text())]
        prompt = execute._build_review_prompt(
            root=root, contract=contract, pack=pack, diff=diff, changed_files=changed_files
        )
        session_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
        adapter = execute.build_adapter(route.provider, route.model_id, executable=route.executable)
        assert isinstance(adapter, ClaudeCliAdapter)
        t0 = time.time()
        result = adapter.invoke(
            prompt,
            schema=execute.build_review_schema(root),
            timeout_seconds=args.timeout,
            max_budget_usd=args.max_budget_usd,
            require_usage=False,
            session_id=session_id,
            bindings={"task_id": f"EVAL-{cid}", "attempt_id": f"EVAL-{cid}-shadow-1",
                      "context_hash": "0" * 64, "contract_hash": "0" * 64},
        )
        record = {
            "case": cid, "shadow": True, "merge_eligible": False,
            "route": route.to_payload(),
            "minted_session_id": session_id, "minted_run_id": run_id,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "started_epoch": t0, "elapsed_seconds": round(time.time() - t0, 3),
            "provider_result": result.to_record(),
            "structured_output": result.structured_output,
            "ok": bool(result.ok),
        }
        target.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n")
        print(f"{cid}: ok={record['ok']} elapsed={record['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
