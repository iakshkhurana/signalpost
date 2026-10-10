"""Geocoding of the registered business address via Kartverket's open address
API (ws.geonorge.no, licence CC BY 4.0). One request; gives WGS84 coordinates
and the cadastral reference of the address the registry holds."""
from __future__ import annotations

from urllib.parse import urlencode

from ..models import SourceResult, fact, clean
from .base import Ctx

API = "https://ws.geonorge.no/adresser/v1/sok"


async def fetch_geonorge(ctx: Ctx, orgnr: str, addr: dict | None, which: str = "business") -> SourceResult:
    res = SourceResult(source="geonorge", url="", status="skipped", retrieved_at=ctx.retrieved_at)
    street = " ".join(addr.get("street") or []) if addr else ""
    pnr = (addr or {}).get("postal_code")
    if not street or not pnr or (addr or {}).get("country_code") not in (None, "NO"):
        res.notes.append("no Norwegian street address to geocode")
        return res
    url = API + "?" + urlencode({"sok": street, "postnummer": pnr, "treffPerSide": 1, "fuzzy": "true"})
    res.url = url
    before = ctx.tally.requests
    try:
        resp = await ctx.http.get(url, headers={"Accept": "application/json"}, tally=ctx.tally)
        res.http_status = resp.status
        if resp.error:
            res.status = "error"
            res.notes.append(resp.error)
            return res
        if not resp.ok:
            res.status = "error"
            res.notes.append(f"HTTP {resp.status}")
            return res
        d = resp.json() or {}
        rows = d.get("adresser") or []
        if not rows:
            res.status = "not_found"
            res.notes.append(f"address {street!r}, {pnr} not found in the national address register")
            return res
        a = rows[0]
        same_pnr = str(a.get("postnummer")) == str(pnr)
        res.checks.append({"check": "postal_code_match", "passed": same_pnr,
                           "detail": f"registry address {street!r} {pnr} -> matched {a.get('adressetekst')!r} {a.get('postnummer')}"})
        if not same_pnr:
            res.status = "not_found"
            res.notes.append("best match has a different postal code; not used")
            return res
        res.status = "ok"
        pt = a.get("representasjonspunkt") or {}
        lat, lon = pt.get("lat"), pt.get("lon")
        res.facts.append(fact(f"location.{which}.geo", clean({
            "lat": lat, "lon": lon, "crs": "EPSG:4326",
            "matched_address": a.get("adressetekst"), "postal_code": a.get("postnummer"), "city": a.get("poststed"),
            "municipality": a.get("kommunenavn"), "municipality_number": a.get("kommunenummer"),
            "cadastral": {"gnr": a.get("gardsnummer"), "bnr": a.get("bruksnummer")} if a.get("gardsnummer") else None,
            "map_url": (f"https://www.norgeskart.no/#!?project=norgeskart&layers=1002&zoom=15&lat={lat}&lon={lon}"
                        f"&markerLat={lat}&markerLon={lon}") if lat is not None else None,
        }), source=res.source, url=url, retrieved_at=ctx.retrieved_at, source_date=(a.get("oppdateringsdato") or "")[:10] or None,
            match="address_lookup", confidence="medium",
            note="coordinates of the registered business address (Kartverket address register, CC BY 4.0); an address match, not an identity match"))
    finally:
        res.requests = ctx.tally.requests - before
    return res
