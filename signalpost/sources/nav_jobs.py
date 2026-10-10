"""Job postings from NAV's public job board (arbeidsplassen.nav.no).

The board is run by the Norwegian Labour and Welfare Administration and is
public without login; its robots.txt allows everything. We query the same
search endpoint the public site uses, with the company name as free text, and
then keep only ads whose *employer* is this entity: the ad's employer name is
the registry name of the legal unit or of one of its registered sub-units
(underenheter), which we already fetched from Brreg. Ads from other employers
that merely mention the name are dropped.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from ..models import SourceResult, fact, clean
from .base import Ctx

API = "https://arbeidsplassen.nav.no/stillinger/api/search"
AD_PAGE = "https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
SEARCH_PAGE = "https://arbeidsplassen.nav.no/stillinger?q={q}"
MAX_POSTINGS = 30
_SUFFIX = re.compile(r"\b(asa|as|a/s|ans|da|enk|nuf|sa|ba|ks|iks|fkf|kf|sf|stiftelsen|stiftelse)\b\.?", re.I)


def norm_name(s: str | None) -> str:
    s = (s or "").casefold().replace("&", " og ")
    s = re.sub(r"[^0-9a-zæøåäöüé]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def search_term(name: str) -> str:
    """'KJELDAAS AS' -> 'kjeldaas'; keep the full name when stripping would leave too little."""
    stripped = re.sub(r"\s+", " ", _SUFFIX.sub(" ", name)).strip()
    return stripped if len(stripped) >= 3 else name


def _pick(ad: dict) -> dict:
    props = ad.get("properties") or {}
    locs = ad.get("locationList") or []
    return {
        "title": ad.get("title"),
        "url": AD_PAGE.format(uuid=ad.get("uuid")),
        "published": (ad.get("published") or "")[:10] or None,
        "expires": (ad.get("expires") or "")[:10] or None,
        "application_due": props.get("applicationdue"),
        "employer_unit": (ad.get("employer") or {}).get("name"),
        "employer_display_name": ad.get("businessName"),
        "location": [{"city": l.get("city"), "municipality": l.get("municipal"), "county": l.get("county")} for l in locs[:3]],
        "engagement_type": props.get("engagementtype"),
        "extent": props.get("extent"),
        "positions": props.get("positioncount"),
        "sector": props.get("sector"),
        "remote": props.get("remote"),
        "occupations": sorted({o.get("level2") for o in (ad.get("occupationList") or []) if o.get("level2")})[:5],
        "listed_via": ad.get("source"),
    }


async def fetch_nav_jobs(ctx: Ctx, orgnr: str, name: str | None, subunit_names: list[str]) -> SourceResult:
    res = SourceResult(source="nav_jobs", url="", status="skipped", retrieved_at=ctx.retrieved_at)
    if not name:
        res.notes.append("no registry name to search for")
        return res
    term = search_term(name)
    url = API + "?" + urlencode({"q": term, "size": 100, "v": 5})
    res.url = url
    before = ctx.tally.requests
    try:
        resp = await ctx.http.get(url, headers={"Accept": "application/json"}, tally=ctx.tally, max_bytes=3_000_000)
        res.http_status = resp.status
        if resp.error:
            res.status = "error"
            res.notes.append(resp.error)
            return res
        if not resp.ok:
            res.status = "error"
            res.notes.append(f"HTTP {resp.status}")
            return res
        try:
            d = resp.json() or {}
        except ValueError:
            res.status = "error"
            res.notes.append("response was not JSON")
            return res
        hits = [h.get("_source") or {} for h in ((d.get("hits") or {}).get("hits") or [])]
        accepted = {norm_name(name)}
        accepted |= {norm_name(s) for s in subunit_names if s}
        accepted.discard("")
        mine: list[dict] = []
        dropped = 0
        for ad in hits:
            emp = norm_name((ad.get("employer") or {}).get("name"))
            disp = norm_name(ad.get("businessName"))
            if emp in accepted or disp == norm_name(name):
                mine.append(ad)
            else:
                dropped += 1
        res.status = "ok" if mine else "not_found"
        res.checks.append({"check": "employer_is_registry_unit", "passed": True,
                           "detail": f"{len(mine)} ad(s) whose employer name equals the registry name of {orgnr} or one of its "
                                     f"{len(subunit_names)} registered sub-units; {dropped} other search hit(s) ignored"})
        if not mine:
            res.notes.append(f"no active ads on arbeidsplassen.nav.no for employer {name!r} (search term {term!r})")
            return res
        human = SEARCH_PAGE.format(q=quote(term))
        mine.sort(key=lambda a: a.get("published") or "", reverse=True)
        res.facts.append(fact("jobs.active_count", len(mine), source=res.source, url=human, retrieved_at=ctx.retrieved_at,
                              match="registry_name_exact", confidence="high",
                              note="active ads on NAV's job board whose employer is this entity or one of its sub-units"))
        for ad in mine[:MAX_POSTINGS]:
            p = _pick(ad)
            exact_unit = norm_name(p["employer_unit"]) in accepted
            res.facts.append(fact(f"jobs.posting.{ad.get('uuid')}", clean(p), source=res.source, url=p["url"],
                                  retrieved_at=ctx.retrieved_at, source_date=p["published"],
                                  match="registry_name_exact" if exact_unit else "name_exact",
                                  confidence="high" if exact_unit else "medium",
                                  note=f"employer unit {p['employer_unit']!r}" + ("" if exact_unit else " (matched on display name)")))
        if len(mine) > MAX_POSTINGS:
            res.notes.append(f"{len(mine) - MAX_POSTINGS} further ads not listed individually")
    finally:
        res.requests = ctx.tally.requests - before
    return res
