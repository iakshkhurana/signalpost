"""GLEIF LEI records (CC0). Matched on entity.registeredAs == org number and a
Norwegian registration authority / jurisdiction."""
from __future__ import annotations

from urllib.parse import urlencode

from ..models import SourceResult, fact, clean
from ..orgnr import normalize, spaced
from .base import Ctx

BASE = "https://api.gleif.org/api/v1/lei-records"
BRREG_RA = "RA000472"  # GLEIF registration-authority code for Brønnøysundregistrene


def _addr(a: dict | None) -> dict | None:
    if not a:
        return None
    return clean({"street": a.get("addressLines"), "postal_code": a.get("postalCode"), "city": a.get("city"),
                  "region": a.get("region"), "country": a.get("country")})


async def fetch_gleif(ctx: Ctx, orgnr: str) -> SourceResult:
    headers = {"Accept": "application/vnd.api+json"}
    before = ctx.tally.requests
    res = SourceResult(source="gleif", url="", status="error", retrieved_at=ctx.retrieved_at)
    records: list[dict] = []
    publish_date = None
    for form in (spaced(orgnr), orgnr):
        url = BASE + "?" + urlencode({"filter[entity.registeredAs]": form, "page[size]": 10})
        res.url = url
        resp = await ctx.http.get(url, headers=headers, tally=ctx.tally)
        if resp.error:
            res.notes.append(resp.error)
            res.requests = ctx.tally.requests - before
            return res
        res.http_status = resp.status
        if not resp.ok:
            res.notes.append(f"unexpected HTTP {resp.status}")
            res.requests = ctx.tally.requests - before
            return res
        d = resp.json() or {}
        publish_date = ((d.get("meta") or {}).get("goldenCopy") or {}).get("publishDate")
        records = d.get("data") or []
        if records:
            break
    res.requests = ctx.tally.requests - before
    if not records:
        res.status = "not_found"
        res.notes.append("no LEI record lists this organisation number")
        return res

    matched: list[dict] = []
    for r in records:
        ent = (r.get("attributes") or {}).get("entity") or {}
        reg_as = normalize(ent.get("registeredAs") or "")
        ra = (ent.get("registeredAt") or {}).get("id")
        country = (ent.get("legalAddress") or {}).get("country")
        ok = reg_as == orgnr and (ra == BRREG_RA or country == "NO" or (ent.get("jurisdiction") or "").startswith("NO"))
        res.checks.append({"check": "lei_registered_as", "passed": ok,
                           "detail": f"LEI {r.get('id')}: registeredAs={ent.get('registeredAs')!r} registeredAt={ra} country={country}"})
        if ok:
            matched.append(r)
        else:
            res.rejected.append({"key": "lei", "reason": f"LEI {r.get('id')} is not a Norwegian record for {orgnr}"})
    if not matched:
        res.status = "not_found"
        return res
    res.status = "ok"
    # Prefer an ISSUED/active LEI if several match (e.g. a lapsed duplicate).
    matched.sort(key=lambda r: ((r["attributes"].get("registration") or {}).get("status") != "ISSUED",
                                (r["attributes"].get("entity") or {}).get("status") != "ACTIVE"))
    r = matched[0]
    a = r["attributes"]
    ent, reg = a.get("entity") or {}, a.get("registration") or {}
    lei = r.get("id")
    page = f"https://search.gleif.org/#/record/{lei}"
    last_update = reg.get("lastUpdateDate")
    F = lambda key, value, **kw: res.facts.append(fact(key, clean(value), source="gleif", url=page, retrieved_at=ctx.retrieved_at,  # noqa: E731
                                                       source_date=kw.pop("source_date", last_update), **kw))
    F("lei", lei, note=f"GLEIF golden copy published {publish_date}")
    F("lei.legal_name", (ent.get("legalName") or {}).get("name"))
    other = [{"name": n.get("name"), "type": n.get("type")} for n in ent.get("otherNames") or [] if n.get("name")]
    if other:
        F("lei.other_names", other)
    F("lei.entity_status", ent.get("status"))
    F("lei.legal_address", _addr(ent.get("legalAddress")))
    F("lei.headquarters_address", _addr(ent.get("headquartersAddress")))
    if (ent.get("legalForm") or {}).get("id"):
        F("lei.legal_form_elf", ent["legalForm"]["id"], note="ISO 20275 Entity Legal Form code")
    if ent.get("creationDate"):
        F("lei.entity_creation_date", ent["creationDate"][:10], source_date=ent["creationDate"][:10])
    F("lei.registration", {"status": reg.get("status"), "initial": (reg.get("initialRegistrationDate") or "")[:10] or None,
                           "last_update": (last_update or "")[:10] or None, "next_renewal": (reg.get("nextRenewalDate") or "")[:10] or None,
                           "managing_lou": reg.get("managingLou"), "validation_sources": reg.get("validatedAs") or reg.get("corroborationLevel")})
    rel = r.get("relationships") or {}
    dp = (rel.get("direct-parent") or {}).get("links") or {}
    up = (rel.get("ultimate-parent") or {}).get("links") or {}
    F("lei.parent_reporting", {
        "direct_parent_reported": "relationship-record" in dp,
        "direct_parent_exception": "reporting-exception" in dp,
        "ultimate_parent_reported": "relationship-record" in up,
        "ultimate_parent_exception": "reporting-exception" in up,
    }, note="whether the entity reports an accounting-consolidating parent to GLEIF")
    if "relationship-record" in dp and ctx.opt("gleif_parent", True) and not ctx.tally.exhausted():
        before2 = ctx.tally.requests
        pr = await ctx.http.get(dp["relationship-record"], headers=headers, tally=ctx.tally)
        res.requests += ctx.tally.requests - before2
        if pr.ok:
            rr = ((pr.json() or {}).get("data") or {}).get("attributes") or {}
            end = ((rr.get("relationship") or {}).get("endNode") or {})
            if end.get("id"):
                F("lei.direct_parent", {"lei": end["id"], "relationship": (rr.get("relationship") or {}).get("type"),
                                        "gleif_url": f"https://search.gleif.org/#/record/{end['id']}"},
                  source_date=((rr.get("registration") or {}).get("lastUpdateDate") or "")[:10] or last_update)
    return res
