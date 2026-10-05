"""Regnskapsregisteret open API: filed annual accounts. Values are copied
verbatim from the filing; nothing is computed or estimated."""
from __future__ import annotations

from ..models import SourceResult, fact, clean
from .base import Ctx

BASE = "https://data.brreg.no/regnskapsregisteret/regnskap"

# fact suffix -> path inside a filing
METRICS = {
    "revenue": ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
    "sales_revenue": ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "salgsinntekter"),
    "operating_costs": ("resultatregnskapResultat", "driftsresultat", "driftskostnad", "sumDriftskostnad"),
    "operating_result": ("resultatregnskapResultat", "driftsresultat", "driftsresultat"),
    "net_financial_items": ("resultatregnskapResultat", "finansresultat", "nettoFinans"),
    "result_before_tax": ("resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"),
    "tax": ("resultatregnskapResultat", "ordinaertResultatSkattekostnad"),
    "net_result": ("resultatregnskapResultat", "aarsresultat"),
    "total_assets": ("eiendeler", "sumEiendeler"),
    "fixed_assets": ("eiendeler", "anleggsmidler", "sumAnleggsmidler"),
    "current_assets": ("eiendeler", "omloepsmidler", "sumOmloepsmidler"),
    "cash": ("eiendeler", "sumBankinnskuddOgKontanter"),
    "equity": ("egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
    "total_debt": ("egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
    "short_term_debt": ("egenkapitalGjeld", "gjeldOversikt", "kortsiktigGjeld", "sumKortsiktigGjeld"),
    "long_term_debt": ("egenkapitalGjeld", "gjeldOversikt", "langsiktigGjeld", "sumLangsiktigGjeld"),
}


def _dig(d: dict, path: tuple) -> object:
    cur: object = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


async def fetch_regnskap(ctx: Ctx, orgnr: str) -> SourceResult:
    url = f"{BASE}/{orgnr}"
    before = ctx.tally.requests
    resp = await ctx.http.get(url, headers={"Accept": "application/json"}, tally=ctx.tally)
    res = SourceResult(source="brreg_regnskap", url=url, status="error", retrieved_at=ctx.retrieved_at,
                       http_status=resp.status, requests=ctx.tally.requests - before)
    if resp.error:
        res.notes.append(resp.error)
        return res
    if resp.status == 404:
        res.status = "not_found"
        res.notes.append("no annual accounts available through the open API for this entity")
        return res
    if not resp.ok:
        res.notes.append(f"unexpected HTTP {resp.status}" + (
            " (the open API answers 500 for some filers, typically banks, insurers and foundations with special statement formats)"
            if resp.status == 500 else ""))
        return res
    data = resp.json()
    filings = data if isinstance(data, list) else [data]
    res.status = "ok"
    latest: tuple[str, str] | None = None
    for filing in filings:
        if not isinstance(filing, dict):
            continue
        v = filing.get("virksomhet") or {}
        echo = v.get("organisasjonsnummer")
        period = filing.get("regnskapsperiode") or {}
        year = (period.get("tilDato") or "")[:4]
        ftype = (filing.get("regnskapstype") or "SELSKAP").lower()
        res.checks.append({"check": "filing_orgnr", "passed": echo == orgnr,
                           "detail": f"filing journalnr={filing.get('journalnr')} virksomhet.organisasjonsnummer={echo} ({year}, {ftype})"})
        if echo != orgnr or not year:
            res.rejected.append({"key": f"finance.{year or '?'}.{ftype}.*", "reason": "filing not addressed to this org number"})
            continue
        prefix = f"finance.{year}.{ftype}"
        currency = filing.get("valuta") or "NOK"
        note = f"Regnskapsregisteret filing id={filing.get('id')} journalnr={filing.get('journalnr')}; value copied verbatim"
        got_any = False
        for suffix, path in METRICS.items():
            amount = _dig(filing, path)
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                got_any = True
                res.facts.append(fact(f"{prefix}.{suffix}", {"amount": amount, "currency": currency,
                                                             "period_from": period.get("fraDato"), "period_to": period.get("tilDato")},
                                      source=res.source, url=url, retrieved_at=ctx.retrieved_at,
                                      source_date=period.get("tilDato"), note=note))
        rev = filing.get("revisjon") or {}
        princ = filing.get("regnkapsprinsipper") or {}
        res.facts.append(fact(f"{prefix}.meta", clean({
            "period_from": period.get("fraDato"), "period_to": period.get("tilDato"), "currency": currency,
            "statement_type": filing.get("regnskapstype"), "layout": filing.get("oppstillingsplan"),
            "audited": (not rev.get("ikkeRevidertAarsregnskap")) if "ikkeRevidertAarsregnskap" in rev else None,
            "audit_waived": rev.get("fravalgRevisjon"), "small_company": princ.get("smaaForetak"),
            "accounting_rules": princ.get("regnskapsregler"), "liquidation_accounts": filing.get("avviklingsregnskap"),
            "journalnr": filing.get("journalnr"), "parent_company": v.get("morselskap"),
        }), source=res.source, url=url, retrieved_at=ctx.retrieved_at, source_date=period.get("tilDato")))
        if got_any and (latest is None or year > latest[0] or (year == latest[0] and ftype == "selskap")):
            latest = (year, ftype)
    if latest:
        res.facts.append(fact("finance.latest", {"year": latest[0], "statement_type": latest[1]}, source=res.source,
                              url=url, retrieved_at=ctx.retrieved_at, note="most recent filing returned by the API"))
    return res
