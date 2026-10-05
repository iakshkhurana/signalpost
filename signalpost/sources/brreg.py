"""Brønnøysundregistrene (Brreg) open data: Enhetsregisteret, roles, sub-units,
and the update feed. Licence: NLOD 2.0. No authentication, no key."""
from __future__ import annotations

from typing import Iterable
from urllib.parse import urlencode

from ..models import SourceResult, fact, clean
from .base import Ctx, joined, address, code

BASE = "https://data.brreg.no/enhetsregisteret/api"
PUBLIC_PAGE = "https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}"

ROLE_KEYS = {
    "DAGL": ("roles.ceo", "Daglig leder / general manager"),
    "LEDE": ("roles.board_chair", "Styrets leder"),
    "NEST": ("roles.board_deputy_chair", "Nestleder"),
    "MEDL": ("roles.board_members", "Styremedlem"),
    "VARA": ("roles.board_deputies", "Varamedlem"),
    "OBS": ("roles.board_observers", "Observatør"),
    "REVI": ("roles.auditor", "Revisor"),
    "REGN": ("roles.accountant", "Regnskapsfører"),
    "KONT": ("roles.contact_person", "Kontaktperson"),
    "INNH": ("roles.owner", "Innehaver"),
    "KOMP": ("roles.general_partner", "Komplementar"),
    "DTPR": ("roles.partners", "Deltaker med proratarisk ansvar"),
    "DTSO": ("roles.partners", "Deltaker med solidarisk ansvar"),
    "BEST": ("roles.managing_director", "Bestyrende reder"),
    "FFØR": ("roles.business_manager", "Forretningsfører"),
    "REPR": ("roles.norwegian_representative", "Norsk representant for utenlandsk enhet"),
    "SAM": ("roles.co_owners", "Sameier"),
    "AVKL": ("roles.liquidation_board", "Avviklingsstyre"),
    "BOBE": ("roles.bankruptcy_trustee", "Bostyrer"),
}
LIST_ROLES = {"roles.board_members", "roles.board_deputies", "roles.board_observers", "roles.partners",
              "roles.co_owners", "roles.liquidation_board"}


def _person_or_entity(role: dict) -> dict | None:
    if role.get("person"):
        n = role["person"].get("navn") or {}
        parts = [n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")]
        name = " ".join(p for p in parts if p).strip()
        return {"name": name} if name else None       # birth dates deliberately not published
    if role.get("enhet"):
        e = role["enhet"]
        names = e.get("navn") or []
        return {"name": " ".join(names) if isinstance(names, list) else names, "orgnr": e.get("organisasjonsnummer")}
    return None


async def fetch_enhet(ctx: Ctx, orgnr: str) -> SourceResult:
    url = f"{BASE}/enheter/{orgnr}"
    public = PUBLIC_PAGE.format(orgnr=orgnr)
    before = ctx.tally.requests
    resp = await ctx.http.get(url, headers={"Accept": "application/json"}, tally=ctx.tally)
    res = SourceResult(source="brreg_enheter", url=url, status="error", retrieved_at=ctx.retrieved_at,
                       http_status=resp.status, requests=ctx.tally.requests - before)
    if resp.error:
        res.notes.append(resp.error)
        return res
    entity_kind = "enhet"
    if resp.status == 404:
        # Maybe the number belongs to a sub-unit (underenhet). One extra lookup.
        sub_url = f"{BASE}/underenheter/{orgnr}"
        before2 = ctx.tally.requests
        sub = await ctx.http.get(sub_url, headers={"Accept": "application/json"}, tally=ctx.tally)
        res.requests += ctx.tally.requests - before2
        if sub.ok:
            resp, url, entity_kind = sub, sub_url, "underenhet"
            res.url = sub_url
            res.notes.append("number is registered as a sub-unit (underenhet), not a main entity")
        else:
            res.status = "not_found"
            res.notes.append("no entity with this organisation number in Enhetsregisteret")
            return res
    if resp.status == 410:
        d = resp.json() or {}
        res.status = "deleted"
        res.http_status = 410
        res.data = d
        if d.get("organisasjonsnummer") == orgnr:
            res.checks.append({"check": "orgnr_echo", "passed": True, "detail": "deleted-entity record echoes the org number"})
            res.facts.append(fact("status.deleted", True, source=res.source, url=url, retrieved_at=ctx.retrieved_at,
                                  source_date=d.get("slettedato"), note="HTTP 410: entity removed from the register"))
            if d.get("slettedato"):
                res.facts.append(fact("deleted_date", d["slettedato"], source=res.source, url=url,
                                      retrieved_at=ctx.retrieved_at, source_date=d["slettedato"]))
            if d.get("navn"):
                res.facts.append(fact("name", d["navn"], source=res.source, url=url, retrieved_at=ctx.retrieved_at))
        return res
    if not resp.ok:
        res.notes.append(f"unexpected HTTP {resp.status}")
        return res

    d = resp.json() or {}
    res.data = d
    echo = d.get("organisasjonsnummer")
    res.checks.append({"check": "orgnr_echo", "passed": echo == orgnr,
                       "detail": f"registry record organisasjonsnummer={echo}; looked up {orgnr}"})
    if echo != orgnr:
        res.status = "error"
        res.notes.append("registry returned a different organisation number; refusing to use the record")
        return res
    res.status = "ok"
    ra = ctx.retrieved_at
    F = lambda key, value, **kw: res.facts.append(fact(key, clean(value), source=res.source, url=url, retrieved_at=ra, **kw))  # noqa: E731

    F("name", d.get("navn"))
    F("entity_type", entity_kind)
    if d.get("historiskeNavn"):
        F("name.history", [{"name": h.get("navn"), "from": h.get("fraDato"), "to": h.get("tilDato")} for h in d["historiskeNavn"]])
    F("org_form", code(d.get("organisasjonsform")))
    if d.get("stiftelsesdato"):
        F("founded", d["stiftelsesdato"], source_date=d["stiftelsesdato"])
    if d.get("registreringsdatoEnhetsregisteret"):
        F("registered.enhetsregisteret", d["registreringsdatoEnhetsregisteret"], source_date=d["registreringsdatoEnhetsregisteret"])
    if d.get("registreringsdatoForetaksregisteret"):
        F("registered.foretaksregisteret", d["registreringsdatoForetaksregisteret"], source_date=d["registreringsdatoForetaksregisteret"])
    if d.get("forretningsadresse"):
        F("address.business", address(d["forretningsadresse"]))
    if d.get("beliggenhetsadresse"):
        F("address.location", address(d["beliggenhetsadresse"]))
    if d.get("postadresse"):
        F("address.postal", address(d["postadresse"]))
    for i in (1, 2, 3):
        if d.get(f"naeringskode{i}"):
            F(f"industry.nace{i}", code(d[f"naeringskode{i}"]))
    if d.get("hjelpeenhetskode"):
        F("industry.auxiliary", code(d["hjelpeenhetskode"]))
    if d.get("institusjonellSektorkode"):
        F("sector.institutional", code(d["institusjonellSektorkode"]))
    if d.get("harRegistrertAntallAnsatte") and d.get("antallAnsatte") is not None:
        F("employees", d["antallAnsatte"],
          source_date=d.get("registreringsdatoAntallAnsatteNAVAaregisteret") or d.get("registreringsdatoAntallAnsatteEnhetsregisteret"),
          note="registered employee count (NAV Aa-registeret via Enhetsregisteret)")
    if d.get("hjemmeside"):
        F("website", d["hjemmeside"], match="registry_declared", note="declared by the entity to Enhetsregisteret")
    for src_key, key in (("telefon", "phone"), ("mobil", "mobile"), ("epostadresse", "email")):
        if d.get(src_key):
            F(key, d[src_key])
    if joined(d.get("vedtektsfestetFormaal")):
        F("purpose", joined(d["vedtektsfestetFormaal"]), source_date=d.get("vedtektsdato"))
    if joined(d.get("aktivitet")):
        F("activity", joined(d["aktivitet"]))
    if d.get("kapital"):
        k = d["kapital"]
        F("capital.share", {"amount": k.get("belop"), "currency": k.get("valuta"), "shares": k.get("antallAksjer"),
                            "type": k.get("type")}, source_date=k.get("innfortDato"))
    F("status", {
        "bankrupt": bool(d.get("konkurs")),
        "under_liquidation": bool(d.get("underAvvikling")),
        "forced_liquidation_or_dissolution": bool(d.get("underTvangsavviklingEllerTvangsopplosning")),
    })
    F("registers", {
        "foretaksregisteret": bool(d.get("registrertIForetaksregisteret")),
        "vat": bool(d.get("registrertIMvaregisteret")),
        "voluntary_vat": bool(d.get("registrertIFrivilligMvaregisteret") or d.get("frivilligMvaRegistrertBeskrivelser")),
        "foundation_register": bool(d.get("registrertIStiftelsesregisteret")),
        "voluntary_register": bool(d.get("registrertIFrivillighetsregisteret")),
        "party_register": bool(d.get("registrertIPartiregisteret")),
    })
    if d.get("erIKonsern") is not None:
        F("group.is_parent", bool(d.get("erIKonsern")))
    if d.get("overordnetEnhet"):
        F("parent_orgnr", d["overordnetEnhet"])
    if d.get("sisteInnsendteAarsregnskap"):
        F("last_filed_annual_accounts_year", str(d["sisteInnsendteAarsregnskap"]))
    if d.get("vedtektsdato"):
        F("bylaws_date", d["vedtektsdato"], source_date=d["vedtektsdato"])
    if d.get("maalform"):
        F("language_form", d["maalform"])
    F("links.brreg_public_page", public, note="human-readable registry page")
    return res


async def fetch_roller(ctx: Ctx, orgnr: str) -> SourceResult:
    url = f"{BASE}/enheter/{orgnr}/roller"
    before = ctx.tally.requests
    resp = await ctx.http.get(url, headers={"Accept": "application/json"}, tally=ctx.tally)
    res = SourceResult(source="brreg_roller", url=url, status="error", retrieved_at=ctx.retrieved_at,
                       http_status=resp.status, requests=ctx.tally.requests - before)
    if resp.error:
        res.notes.append(resp.error)
        return res
    if resp.status == 404:
        res.status = "not_found"
        return res
    if not resp.ok:
        res.notes.append(f"unexpected HTTP {resp.status}")
        return res
    d = resp.json() or {}
    res.status = "ok"
    res.checks.append({"check": "url_scoped", "passed": True,
                       "detail": "roles endpoint is addressed by org number; records are scoped to it by the registry"})
    grouped: dict[str, list] = {}
    dates: dict[str, str] = {}
    other: list[dict] = []
    for group in d.get("rollegrupper", []):
        gdate = group.get("sistEndret")
        for role in group.get("roller", []):
            if role.get("avregistrert"):
                continue
            who = _person_or_entity(role)
            if not who:
                continue
            kode = (role.get("type") or {}).get("kode")
            if kode in ROLE_KEYS:
                key, _ = ROLE_KEYS[kode]
                grouped.setdefault(key, []).append(who)
                if gdate:
                    dates[key] = max(dates.get(key, ""), gdate)
            else:
                other.append({**who, "role": (role.get("type") or {}).get("beskrivelse") or kode})
    for key, people in grouped.items():
        value = people if key in LIST_ROLES or len(people) > 1 else people[0]
        res.facts.append(fact(key, clean(value), source=res.source, url=url, retrieved_at=ctx.retrieved_at,
                              source_date=dates.get(key), note="role group last changed " + dates[key] if key in dates else None))
    if other:
        res.facts.append(fact("roles.other", clean(other), source=res.source, url=url, retrieved_at=ctx.retrieved_at))
    return res


async def fetch_underenheter(ctx: Ctx, orgnr: str) -> SourceResult:
    url = f"{BASE}/underenheter?" + urlencode({"overordnetEnhet": orgnr, "size": 100})
    before = ctx.tally.requests
    resp = await ctx.http.get(url, headers={"Accept": "application/json"}, tally=ctx.tally)
    res = SourceResult(source="brreg_underenheter", url=url, status="error", retrieved_at=ctx.retrieved_at,
                       http_status=resp.status, requests=ctx.tally.requests - before)
    if resp.error:
        res.notes.append(resp.error)
        return res
    if not resp.ok:
        res.notes.append(f"unexpected HTTP {resp.status}")
        return res
    d = resp.json() or {}
    units = (d.get("_embedded") or {}).get("underenheter") or []
    total = (d.get("page") or {}).get("totalElements", len(units))
    units = [u for u in units if u.get("overordnetEnhet") == orgnr]
    res.status = "ok"
    res.checks.append({"check": "parent_filter", "passed": True,
                       "detail": f"query overordnetEnhet={orgnr}; each returned unit re-checked for overordnetEnhet == {orgnr}"})
    res.facts.append(fact("subunits.count", total, source=res.source, url=url, retrieved_at=ctx.retrieved_at))
    if units:
        rows = [{
            "orgnr": u.get("organisasjonsnummer"),
            "name": u.get("navn"),
            "city": (u.get("beliggenhetsadresse") or {}).get("poststed"),
            "municipality": (u.get("beliggenhetsadresse") or {}).get("kommune"),
            "employees": u.get("antallAnsatte") if u.get("harRegistrertAntallAnsatte") else None,
            "nace": ((u.get("naeringskode1") or {}).get("kode")),
        } for u in units[:100]]
        res.facts.append(fact("subunits.list", clean(rows), source=res.source, url=url, retrieved_at=ctx.retrieved_at,
                              note=f"first {len(rows)} of {total}" if total > len(rows) else None))
        emp = [u.get("antallAnsatte") for u in units if u.get("harRegistrertAntallAnsatte") and isinstance(u.get("antallAnsatte"), int)]
        if emp and total == len(units):
            res.facts.append(fact("subunits.employees_total", sum(emp), source=res.source, url=url,
                                  retrieved_at=ctx.retrieved_at, note="sum of registered employees across all sub-units"))
    return res


# ------------------------------------------------------------------ feeds
async def updated_since(http, orgnrs: Iterable[str], since_iso: str, *, batch: int = 100, max_pages: int = 20) -> tuple[set[str], int]:
    """Return (set of orgnrs changed in Enhetsregisteret since `since_iso`, requests used)."""
    changed: set[str] = set()
    used = 0
    ids = list(orgnrs)
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        page = 0
        while page < max_pages:
            url = f"{BASE}/oppdateringer/enheter?" + urlencode({
                "dato": since_iso, "organisasjonsnummer": ",".join(chunk), "size": 500, "page": page})
            resp = await http.get(url, headers={"Accept": "application/json"}, cacheable=False)
            used += 1
            if not resp.ok:
                break
            d = resp.json() or {}
            for item in (d.get("_embedded") or {}).get("oppdaterteEnheter") or []:
                if item.get("organisasjonsnummer"):
                    changed.add(item["organisasjonsnummer"])
            pages = (d.get("page") or {}).get("totalPages", 1)
            page += 1
            if page >= pages:
                break
    return changed, used


async def search(http, params: dict, *, page: int = 0, size: int = 200) -> dict:
    url = f"{BASE}/enheter?" + urlencode({**params, "page": page, "size": size})
    resp = await http.get(url, headers={"Accept": "application/json"}, cacheable=False)
    if not resp.ok:
        return {}
    return resp.json() or {}
