"""Cross-source entity matching summary."""
from __future__ import annotations

import re
from typing import Iterable

from .models import SourceResult


def _norm_name(n: str | None) -> str:
    return re.sub(r"[^a-z0-9æøå]+", " ", (n or "").casefold()).strip()


def assess(orgnr: str, results: Iterable[SourceResult]) -> dict:
    results = list(results)
    by = {r.source: r for r in results}
    checks: list[dict] = []
    for r in results:
        for c in r.checks:
            checks.append({"source": r.source, **c})

    brreg = by.get("brreg_enheter")
    name = (brreg.data or {}).get("navn") if brreg and brreg.status == "ok" else None
    cross: list[dict] = []

    gleif = by.get("gleif")
    if gleif and gleif.status == "ok":
        lei_name = next((f.value for f in gleif.facts if f.key == "lei.legal_name"), None)
        same = _norm_name(lei_name) == _norm_name(name)
        cross.append({"check": "name_consistency.gleif_vs_brreg", "passed": same or lei_name is None,
                      "detail": f"GLEIF legal name {lei_name!r} vs registry name {name!r}"})

    wd = by.get("wikidata")
    if wd and wd.status == "ok":
        label = next((f.value for f in wd.facts if f.key == "wikidata.label"), None)
        a, b = _norm_name(label), _norm_name(name)
        overlap = bool(a and b and (a in b or b in a or len(set(a.split()) & set(b.split())) >= 1))
        cross.append({"check": "name_consistency.wikidata_vs_brreg", "passed": overlap,
                      "detail": f"Wikidata label {label!r} vs registry name {name!r}"})

    web = by.get("website")
    web_conf = (web.data or {}).get("confidence") if web and web.status == "ok" else None

    failed = [c for c in checks + cross if not c.get("passed")]
    if brreg is None or brreg.status not in ("ok", "deleted"):
        overall = "none"
    elif not failed:
        overall = "high"
    elif all(c["check"] in ("orgnr_on_page", "name_on_page", "same_host_after_redirect", "not_placeholder",
                             "name_consistency.wikidata_vs_brreg") for c in failed):
        overall = "high" if web_conf in (None, "high", "medium") else "medium"
    elif all(c["check"] in ("orgnr_on_page", "name_on_page", "same_host_after_redirect", "not_placeholder",
                             "name_consistency.wikidata_vs_brreg", "postal_code_match") for c in failed):
        overall = "high" if web_conf in (None, "high", "medium") else "medium"
    else:
        overall = "medium"

    lines = []
    for r in results:
        if r.status == "ok":
            how = {
                "brreg_enheter": "looked up by org number; record echoes the number",
                "brreg_roller": "roles endpoint addressed by org number",
                "brreg_underenheter": "sub-units filtered on overordnetEnhet = org number and re-checked",
                "brreg_regnskap": "each filing's virksomhet.organisasjonsnummer verified",
                "gleif": "LEI record's registeredAs equals the org number (Norwegian registration authority)",
                "wikidata": "item carries P2333 = org number",
                "website": (f"{'home page guessed from the registered name' if any(c.get('check') == 'guessed_from_name' for c in web.checks) else 'URL declared by the company in the registry'}; "
                            f"page match = {(web.data or {}).get('match')} ({web_conf})") if web else "",
                "nav_jobs": "ads kept only when the employer name equals the registry name of the entity or one of its sub-units",
                "geonorge": "registered business address looked up in Kartverket's address register (postal code must match)",
                "dibk": "approval record addressed by org number; record echoes the number",
                "wikipedia": "article linked from the Wikidata item whose P2333 equals the org number",
            }.get(r.source, "")
            lines.append(f"{r.source}: {how}")
        elif r.status in ("not_found", "deleted", "blocked", "skipped"):
            lines.append(f"{r.source}: {r.status}" + (f" ({r.notes[0]})" if r.notes else ""))
        elif r.status in ("error", "budget"):
            lines.append(f"{r.source}: {r.status}" + (f" ({r.notes[0]})" if r.notes else ""))
    return {
        "overall": overall,
        "registry_name": name,
        "checks": checks + cross,
        "failed_checks": [c["check"] for c in failed],
        "summary": lines,
    }
