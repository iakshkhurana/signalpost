"""Orchestration: fetch all sources for a company, match, merge, explain, save."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from . import AGENT_NAME, __version__
from .explain import template_explanation
from .http import Budget, BudgetExceeded, Http, Tally
from .match import assess
from .merge import merge
from .models import Fact, SourceResult, now_iso
from .sources.base import Ctx
from .sources.brreg import fetch_enhet, fetch_roller, fetch_underenheter
from .sources.gleif import fetch_gleif
from .sources.regnskap import fetch_regnskap
from .sources.website import fetch_website
from .sources.wikidata import fetch_wikidata
from .store import Store

ALL_SOURCES = ["brreg_enheter", "brreg_roller", "brreg_underenheter", "brreg_regnskap", "gleif", "wikidata", "website"]


async def _guard(coro, source: str, url: str, retrieved_at: str) -> SourceResult:
    try:
        return await coro
    except BudgetExceeded as e:
        return SourceResult(source=source, url=url, status="budget", retrieved_at=retrieved_at, notes=[str(e)])
    except Exception as e:  # a bug in one adapter must not sink the company
        return SourceResult(source=source, url=url, status="error", retrieved_at=retrieved_at,
                            notes=[f"adapter failure {type(e).__name__}: {e}"[:300]])


async def profile_company(http: Http, orgnr: str, existing: Optional[dict], options: dict, explainer=None) -> dict:
    t0 = time.monotonic()
    now = now_iso()
    tally = Tally(limit=int(options.get("per_company_requests", 30)))
    results: list[SourceResult] = []

    def ctx() -> Ctx:  # one child tally per source so per-source request counts are exact
        return Ctx(http=http, tally=tally.child(), retrieved_at=now, options=options)

    enhet = await _guard(fetch_enhet(ctx(), orgnr), "brreg_enheter", "", now)
    results.append(enhet)
    name = (enhet.data or {}).get("navn") if enhet.status in ("ok", "deleted") else None
    declared_site = (enhet.data or {}).get("hjemmeside") if enhet.status == "ok" else None

    if enhet.status in ("ok", "deleted"):
        tasks = [
            _guard(fetch_roller(ctx(), orgnr), "brreg_roller", "", now),
            _guard(fetch_underenheter(ctx(), orgnr), "brreg_underenheter", "", now),
            _guard(fetch_regnskap(ctx(), orgnr), "brreg_regnskap", "", now),
            _guard(fetch_gleif(ctx(), orgnr), "gleif", "", now),
            _guard(fetch_wikidata(ctx(), orgnr), "wikidata", "", now),
        ]
        results.extend(await asyncio.gather(*tasks))
        if options.get("website", True):
            site, declared_by = declared_site, "brreg_enheter"
            if not site:
                wd = next((r for r in results if r.source == "wikidata"), None)
                sites = next((f.value for f in (wd.facts if wd else []) if f.key == "wikidata.websites"), None)
                if sites:
                    site, declared_by = sites[0], "wikidata"
            web_cap = float(options.get("website_timeout", 60.0))
            try:
                results.append(await asyncio.wait_for(
                    _guard(fetch_website(ctx(), orgnr, site, name, declared_by=declared_by), "website", site or "", now), web_cap))
            except asyncio.TimeoutError:
                results.append(SourceResult(source="website", url=site or "", status="error", retrieved_at=now,
                                            notes=[f"website fetch exceeded {web_cap:.0f}s cap"]))

    fresh: list[Fact] = [f for r in results for f in r.facts]
    status = {r.source: r.status for r in results}
    for s in ALL_SOURCES:
        status.setdefault(s, "skipped")
    old_facts = [Fact.from_dict(d) for d in (existing or {}).get("facts", [])]
    facts, changes = merge(old_facts, fresh, status, now)
    matching = assess(orgnr, results)

    profile = {
        "schema_version": 1,
        "orgnr": orgnr,
        "name": name or (existing or {}).get("name"),
        "agent": {"name": AGENT_NAME, "version": __version__},
        "created_at": (existing or {}).get("created_at", now),
        "last_run": now,
        "run_count": int((existing or {}).get("run_count", 0)) + 1,
        "entity_status": enhet.status,
        "matching": matching,
        "facts": [f.to_dict() for f in facts],
        "sources": [r.summary() for r in results],
        "last_run_changes": changes,
        "changes": ((existing or {}).get("changes", []) + changes)[-500:],
        "stats": {
            "active_facts": sum(1 for f in facts if f.status == "active"),
            "retracted_facts": sum(1 for f in facts if f.status == "retracted"),
            "stale_facts": sum(1 for f in facts if f.stale and f.status == "active"),
            "requests": tally.requests,
            "seconds": round(time.monotonic() - t0, 2),
        },
    }
    profile["explanation"] = {"mode": "template", "text": template_explanation(profile)}
    if explainer is not None and explainer.available() and enhet.status == "ok":
        try:
            llm = await explainer.explain(profile)
        except BudgetExceeded as e:
            llm = {"error": str(e)}
        except Exception as e:  # never let the optional layer sink a profile
            llm = {"error": f"LLM failure {type(e).__name__}; template used"}
        if llm is None and explainer.disabled_reason:
            profile["explanation"]["llm_note"] = explainer.disabled_reason
        if llm and not llm.get("error"):
            profile["explanation"] = {"mode": "llm", **llm, "template_text": profile["explanation"]["text"]}
        elif llm:
            profile["explanation"]["llm_note"] = llm.get("error")
            profile["stats"]["llm_cost_usd"] = llm.get("cost_usd", 0.0)
    return profile


async def run_many(orgnrs: list[str], options: dict, log=print) -> dict:
    started = now_iso()
    budget = Budget(max_requests=int(options.get("max_requests", 2000)),
                    max_seconds=float(options.get("time_budget", 45 * 60)),
                    max_cost_usd=float(options.get("max_cost", 10.0)))
    store = Store(options.get("out", "profiles"))
    explainer = None
    if options.get("llm"):
        from .llm import DEFAULT_MODEL, Explainer
        explainer = Explainer(budget, model=options.get("model") or DEFAULT_MODEL)
        if not explainer.available():
            log(f"LLM explanations disabled: {explainer.disabled_reason}")
    sem = asyncio.Semaphore(int(options.get("concurrency", 6)))
    rows: list[dict] = []
    skipped: list[str] = []

    async with Http(budget, concurrency=int(options.get("concurrency", 6)) * 3,
                    cache_dir=options.get("cache_dir"), timeout=float(options.get("timeout", 20.0))) as http:
        async def one(orgnr: str, i: int):
            async with sem:
                if budget.exhausted():
                    skipped.append(orgnr)
                    return
                existing = store.load(orgnr)
                try:
                    profile = await profile_company(http, orgnr, existing, options, explainer)
                except BudgetExceeded as e:
                    skipped.append(orgnr)
                    log(f"[{i}/{len(orgnrs)}] {orgnr}: stopped ({e})")
                    return
                store.save(profile)
                st = profile["stats"]
                rows.append({"orgnr": orgnr, "name": profile.get("name"), "entity_status": profile["entity_status"],
                             "match": profile["matching"]["overall"], "active_facts": st["active_facts"],
                             "changes": len(profile["last_run_changes"]), "requests": st["requests"], "seconds": st["seconds"],
                             "explanation_mode": profile["explanation"]["mode"],
                             "sources": {s["source"]: s["status"] for s in profile["sources"]}})
                if not options.get("quiet"):
                    log(f"[{i}/{len(orgnrs)}] {orgnr} {profile.get('name') or '-'}: {profile['entity_status']}, "
                        f"{st['active_facts']} facts, {len(profile['last_run_changes'])} changes, {st['requests']} req, "
                        f"{st['seconds']}s, match={profile['matching']['overall']}")

        await asyncio.gather(*(one(o, i + 1) for i, o in enumerate(orgnrs)))

    index = store.rebuild_index()
    report = {
        "started_at": started,
        "finished_at": now_iso(),
        "agent": {"name": AGENT_NAME, "version": __version__},
        "options": {k: v for k, v in options.items() if k not in ("quiet",)},
        "budget": budget.to_dict(),
        "requested": len(orgnrs),
        "completed": len(rows),
        "skipped_for_budget": skipped,
        "profiles_in_store": index["count"],
        "source_status_counts": _count_sources(rows),
        "companies": sorted(rows, key=lambda r: r["orgnr"]),
    }
    if explainer is not None:
        report["llm"] = {"model": explainer.model, "calls": budget.llm_calls, "cost_usd": round(budget.llm_cost_usd, 4),
                         "disabled_reason": explainer.disabled_reason}
    return report


def _count_sources(rows: list[dict]) -> dict:
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        for s, st in r["sources"].items():
            out.setdefault(s, {})
            out[s][st] = out[s].get(st, 0) + 1
    return out
