"""Sentral godkjenning (central approval for building enterprises) from the
Norwegian Building Authority (DiBK). Public register, open JSON API keyed by
organisation number. Only queried for companies in construction / engineering
industries (NACE 41-43, 71) to save requests."""
from __future__ import annotations

from ..models import SourceResult, fact, clean
from .base import Ctx

API = "https://sgregister.dibk.no/api/enterprises/{orgnr}"
PAGE = "https://sgregister.dibk.no/enterprises/{orgnr}"
RELEVANT_NACE_PREFIXES = ("41", "42", "43", "71")


def relevant(nace_codes: list[str]) -> bool:
    return any((c or "").startswith(RELEVANT_NACE_PREFIXES) for c in nace_codes)


async def fetch_dibk(ctx: Ctx, orgnr: str) -> SourceResult:
    url = API.format(orgnr=orgnr)
    res = SourceResult(source="dibk", url=url, status="error", retrieved_at=ctx.retrieved_at)
    before = ctx.tally.requests
    try:
        resp = await ctx.http.get(url, headers={"Accept": "application/json"}, tally=ctx.tally)
        res.http_status = resp.status
        if resp.error:
            res.notes.append(resp.error)
            return res
        if resp.status == 404:
            res.status = "not_found"
            res.notes.append("no central approval registered")
            return res
        if not resp.ok:
            res.notes.append(f"HTTP {resp.status}")
            return res
        d = ((resp.json() or {}).get("dibk-sgdata")) or {}
        ent = d.get("enterprise") or {}
        echo = str(ent.get("organizational_number") or "")
        res.checks.append({"check": "orgnr_echo", "passed": echo == orgnr,
                           "detail": f"register record organizational_number={echo}; looked up {orgnr}"})
        if echo != orgnr:
            res.notes.append("register returned a different organisation number; record not used")
            return res
        res.status = "ok"
        st = d.get("status") or {}
        page = PAGE.format(orgnr=orgnr)
        areas = [{"function": a.get("function"), "subject_area": a.get("subject_area"), "grade": a.get("grade"),
                  "law": a.get("pbl")} for a in (d.get("valid_approval_areas") or [])]
        res.facts.append(fact("approvals.dibk_central_approval", clean({
            "approved": bool(st.get("approved")),
            "valid_to": st.get("approval_period_to"),
            "certificate_url": st.get("approval_certificate"),
            "liability_insurance": (d.get("additional_terms") or {}).get("liability_insurance"),
            "industrial_injury_insurance": (d.get("additional_terms") or {}).get("industrial_injury_insurance"),
            "approved_training_enterprise": (d.get("additional_terms") or {}).get("educational_enterprise_approved"),
            "areas": areas,
        }), source=res.source, url=page, retrieved_at=ctx.retrieved_at, source_date=st.get("approval_period_to"),
            note="Sentral godkjenning for ansvarsrett (DiBK); areas = function / subject area / tiltaksklasse"))
        if ent.get("www"):
            res.facts.append(fact("approvals.dibk_declared_website", ent["www"], source=res.source, url=page,
                                  retrieved_at=ctx.retrieved_at, match="orgnr_exact", confidence="medium",
                                  note="website as given to DiBK"))
    finally:
        res.requests = ctx.tally.requests - before
    return res
