"""Command-line interface.

  signalpost run 923609016 [more orgnrs...]      profile one or more companies
  signalpost run --input companies.txt           profile every org number in a file
  signalpost refresh                             re-run companies the registry says changed
  signalpost discover --count 1000               pick a sample of companies from the registry
  signalpost show 923609016                      print the explanation and facts
  signalpost validate                            check every stored profile for integrity
  signalpost stats                               coverage overview of the store
  signalpost export --out profiles.jsonl         single-file export
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import __version__
from .orgnr import is_valid, normalize, parse_many, spaced
from .store import Store, write_run_report


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default="profiles", help="profile directory (default: profiles)")
    p.add_argument("--runs-dir", default="runs", help="where run reports are written")
    p.add_argument("--concurrency", type=int, default=6, help="companies processed in parallel")
    p.add_argument("--max-requests", type=int, default=2000, help="hard cap on outbound HTTP requests for the run")
    p.add_argument("--time-budget", type=float, default=45 * 60, help="hard cap in seconds (default 2700 = 45 min)")
    p.add_argument("--max-cost", type=float, default=8.0, help="hard cap on declared LLM spend in USD")
    p.add_argument("--per-company-requests", type=int, default=25, help="cap on requests spent on one company")
    p.add_argument("--timeout", type=float, default=20.0, help="per-request timeout in seconds")
    p.add_argument("--llm", action="store_true", help="use Claude for the explanation (needs ANTHROPIC_API_KEY)")
    p.add_argument("--model", default=None, help="Claude model id for --llm (default claude-opus-5-5)")
    p.add_argument("--no-website", action="store_true", help="do not fetch company websites")
    p.add_argument("--website-pages", type=int, default=2, help="max pages per website (1 = home only)")
    p.add_argument("--website-timeout", type=float, default=60.0, help="cap in seconds on all website fetching for one company")
    p.add_argument("--min-confidence", choices=["low", "medium", "high"], default="medium",
                   help="lowest confidence at which website-derived facts are published")
    p.add_argument("--cache-dir", default=os.environ.get("SIGNALPOST_CACHE_DIR"), help="on-disk HTTP cache (dev only)")
    p.add_argument("--quiet", action="store_true")


def _options(a: argparse.Namespace) -> dict:
    return {
        "out": a.out, "concurrency": a.concurrency, "max_requests": a.max_requests, "time_budget": a.time_budget,
        "max_cost": a.max_cost, "per_company_requests": a.per_company_requests, "timeout": a.timeout, "llm": a.llm,
        "model": a.model, "website": not a.no_website, "website_pages": a.website_pages, "website_timeout": a.website_timeout,
        "min_confidence": a.min_confidence, "cache_dir": a.cache_dir, "quiet": a.quiet,
    }


def _load_orgnrs(values: list[str], input_path: str | None) -> list[str]:
    text = " ".join(values or [])
    if input_path:
        with open(input_path, encoding="utf-8") as f:
            text += "\n" + f.read()
    good = parse_many(text)
    bad = [t for line in text.splitlines() if not line.strip().startswith("#")
           for t in line.replace(",", " ").replace(";", " ").split()
           if normalize(t) and not is_valid(normalize(t))]
    for b in bad:
        print(f"warning: '{b}' is not a valid Norwegian organisation number (check digit); skipped", file=sys.stderr)
    return good


def cmd_run(a: argparse.Namespace) -> int:
    from .pipeline import run_many
    orgnrs = _load_orgnrs(a.orgnr, a.input)
    if not orgnrs:
        print("no valid organisation numbers given", file=sys.stderr)
        return 2
    print(f"signalpost {__version__}: profiling {len(orgnrs)} compan{'y' if len(orgnrs) == 1 else 'ies'} -> {a.out}/")
    report = asyncio.run(run_many(orgnrs, _options(a)))
    path = write_run_report(report, a.runs_dir)
    b = report["budget"]
    print(f"done: {report['completed']}/{report['requested']} profiles, {b['outbound_requests']} outbound requests, "
          f"{b['elapsed_seconds']}s, LLM ${b['llm_cost_usd']:.4f}; report {path}")
    if report["skipped_for_budget"]:
        print(f"skipped for budget: {len(report['skipped_for_budget'])}")
    return 0 if report["completed"] == report["requested"] else 1


def cmd_refresh(a: argparse.Namespace) -> int:
    from .http import Budget, Http
    from .pipeline import run_many
    from .sources.brreg import updated_since
    store = Store(a.out)
    all_ids = store.orgnrs()
    if not all_ids:
        print("store is empty; nothing to refresh")
        return 0
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=a.stale_days)
    if a.all:
        targets = all_ids
        reason = "--all"
    else:
        last_runs = {}
        for o in all_ids:
            p = store.load(o)
            last_runs[o] = p.get("last_run") if p else None
        since = a.since or min((lr for lr in last_runs.values() if lr), default=None)
        if since is None:
            targets, reason = all_ids, "no previous run timestamps"
        else:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

            async def feed():
                budget = Budget(max_requests=a.max_requests, max_seconds=a.time_budget)
                async with Http(budget) as http:
                    return await updated_since(http, all_ids, since_iso)

            changed, used = asyncio.run(feed())
            stale = [o for o, lr in last_runs.items() if not lr or datetime.fromisoformat(lr.replace("Z", "+00:00")) < stale_cutoff]
            targets = sorted(set(changed) | set(stale))
            reason = f"{len(changed)} changed in Enhetsregisteret since {since_iso} ({used} feed requests); {len(stale)} older than {a.stale_days} days"
    print(f"refresh: {len(targets)} of {len(all_ids)} profiles selected ({reason})")
    if not targets:
        return 0
    if a.limit:
        targets = targets[: a.limit]
    report = asyncio.run(run_many(targets, _options(a)))
    report["refresh_reason"] = reason
    path = write_run_report(report, a.runs_dir)
    print(f"done: {report['completed']} refreshed, {report['budget']['outbound_requests']} requests; report {path}")
    return 0


def cmd_discover(a: argparse.Namespace) -> int:
    from .discover import discover
    from .http import Budget, Http

    async def go():
        budget = Budget(max_requests=500, max_seconds=1200)
        async with Http(budget) as http:
            return await discover(http, a.count, a.seed)

    ids = asyncio.run(go())
    os.makedirs(os.path.dirname(a.out_file) or ".", exist_ok=True)
    with open(a.out_file, "w", encoding="utf-8") as f:
        f.write(f"# {len(ids)} Norwegian organisation numbers sampled from Enhetsregisteret (seed {a.seed})\n")
        f.write("\n".join(ids) + "\n")
    print(f"wrote {len(ids)} org numbers to {a.out_file}")
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    store = Store(a.out)
    p = store.load(normalize(a.orgnr))
    if not p:
        print(f"no profile for {a.orgnr} in {a.out}/ (run `signalpost run {a.orgnr}` first)")
        return 1
    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=1))
        return 0
    ex = p["explanation"]
    print(f"{p.get('name')} — {spaced(p['orgnr'])}  [match: {p['matching']['overall']}, runs: {p['run_count']}, last: {p['last_run']}]")
    print()
    if ex["mode"] == "llm":
        print(ex["summary"])
        for h in ex.get("highlights", []):
            print(f"  • {h}")
        for c in ex.get("caveats", []):
            print(f"  ! {c}")
        print()
        print(ex["template_text"])
    else:
        print(ex["text"])
    print()
    print(f"{'FACT':42} {'VALUE':48} {'SOURCE':18} {'CONF':6} DATE")
    for f in p["facts"]:
        if f["status"] != "active" and not a.all:
            continue
        v = json.dumps(f["value"], ensure_ascii=False) if not isinstance(f["value"], str) else f["value"]
        v = v if len(v) <= 46 else v[:43] + "..."
        e = f["evidence"][0]
        flag = "" if f["status"] == "active" else " (retracted)"
        print(f"{f['key'][:42]:42} {v:48} {e['source'][:18]:18} {f['confidence']:6} {e.get('source_date') or e['retrieved_at'][:10]}{flag}")
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    store = Store(a.out)
    problems = 0
    count = 0
    for p in store.iter_profiles():
        count += 1
        errs = []
        if not is_valid(p["orgnr"]):
            errs.append("invalid org number")
        if os.path.basename(store.path(p["orgnr"])) != f"{p['orgnr']}.json":
            errs.append("file name / orgnr mismatch")
        for f in p.get("facts", []):
            if not f.get("evidence"):
                errs.append(f"{f['key']}: no evidence")
                continue
            for e in f["evidence"]:
                if not e.get("url") or not e.get("retrieved_at"):
                    errs.append(f"{f['key']}: evidence missing url/date")
                if f["key"].startswith("finance.") and e.get("source") != "brreg_regnskap":
                    errs.append(f"{f['key']}: financial fact from non-filing source {e.get('source')}")
                if e.get("source") == "website" and e.get("confidence") == "low" and f.get("status") == "active":
                    errs.append(f"{f['key']}: low-confidence website fact published")
            if f["key"].startswith("finance.") and isinstance(f["value"], dict) and "amount" in f["value"]:
                if not isinstance(f["value"]["amount"], (int, float)):
                    errs.append(f"{f['key']}: non-numeric amount")
        if "explanation" not in p or not (p["explanation"].get("text") or p["explanation"].get("summary")):
            errs.append("missing explanation")
        if errs:
            problems += 1
            print(f"{p['orgnr']}: " + "; ".join(errs[:5]))
    print(f"validated {count} profiles, {problems} with problems")
    return 1 if problems else 0


def cmd_stats(a: argparse.Namespace) -> int:
    store = Store(a.out)
    n = 0
    src: dict[str, int] = {}
    keys: dict[str, int] = {}
    match: dict[str, int] = {}
    facts_total = 0
    for p in store.iter_profiles():
        n += 1
        for s in p.get("sources", []):
            if s["status"] == "ok":
                src[s["source"]] = src.get(s["source"], 0) + 1
        for f in p.get("facts", []):
            if f["status"] == "active":
                facts_total += 1
                k = f["key"].split(".")[0]
                keys[k] = keys.get(k, 0) + 1
        m = (p.get("matching") or {}).get("overall", "?")
        match[m] = match.get(m, 0) + 1
    if not n:
        print("store is empty")
        return 0
    print(f"profiles: {n}   active facts: {facts_total}   avg facts/profile: {facts_total / n:.1f}")
    print("source coverage (profiles where source returned data):")
    for s, c in sorted(src.items(), key=lambda x: -x[1]):
        print(f"  {s:20} {c:5}  {100 * c / n:5.1f}%")
    print("matching overall: " + ", ".join(f"{k}={v}" for k, v in sorted(match.items())))
    print("fact groups: " + ", ".join(f"{k}={v}" for k, v in sorted(keys.items(), key=lambda x: -x[1])))
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    n = Store(a.out).export_jsonl(a.out_file)
    print(f"exported {n} profiles to {a.out_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="signalpost", description="Evidence-backed company profiles for Norwegian organisation numbers.")
    p.add_argument("--version", action="version", version=f"signalpost {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="profile companies")
    r.add_argument("orgnr", nargs="*", help="organisation numbers")
    r.add_argument("--input", "-i", help="file with organisation numbers (one per line)")
    _common(r)
    r.set_defaults(fn=cmd_run)

    f = sub.add_parser("refresh", help="re-run companies that changed (Brreg update feed) or are stale")
    f.add_argument("--since", help="ISO timestamp; default = oldest last_run in the store")
    f.add_argument("--stale-days", type=int, default=7, help="also refresh profiles older than this (default 7)")
    f.add_argument("--all", action="store_true", help="refresh every stored profile")
    f.add_argument("--limit", type=int, default=0, help="refresh at most N companies")
    _common(f)
    f.set_defaults(fn=cmd_refresh)

    d = sub.add_parser("discover", help="sample organisation numbers from Enhetsregisteret")
    d.add_argument("--count", type=int, default=1000)
    d.add_argument("--seed", type=int, default=20261005)
    d.add_argument("--out-file", default="data/companies.txt")
    d.set_defaults(fn=cmd_discover)

    s = sub.add_parser("show", help="print a stored profile")
    s.add_argument("orgnr")
    s.add_argument("--out", default="profiles")
    s.add_argument("--json", action="store_true")
    s.add_argument("--all", action="store_true", help="include retracted facts")
    s.set_defaults(fn=cmd_show)

    v = sub.add_parser("validate", help="integrity checks on stored profiles")
    v.add_argument("--out", default="profiles")
    v.set_defaults(fn=cmd_validate)

    st = sub.add_parser("stats", help="coverage statistics")
    st.add_argument("--out", default="profiles")
    st.set_defaults(fn=cmd_stats)

    e = sub.add_parser("export", help="write all profiles to one JSONL file")
    e.add_argument("--out", default="profiles")
    e.add_argument("--out-file", default="profiles.jsonl")
    e.set_defaults(fn=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    return args.fn(args)
