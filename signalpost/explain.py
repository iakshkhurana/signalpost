"""Deterministic, template-based explanation of a profile. Always available;
the LLM explanation (llm.py) is optional and layered on top."""
from __future__ import annotations

from .orgnr import spaced


def _facts(profile: dict) -> dict:
    return {f["key"]: f["value"] for f in profile.get("facts", []) if f.get("status", "active") == "active"}


def fmt_amount(v: dict | None) -> str | None:
    if not isinstance(v, dict) or v.get("amount") is None:
        return None
    amt = v["amount"]
    s = f"{amt:,.0f}" if float(amt).is_integer() else f"{amt:,.2f}"
    return f"{v.get('currency', 'NOK')} {s}"


def company_summary(profile: dict) -> str:
    f = _facts(profile)
    orgnr = profile["orgnr"]
    name = f.get("name") or profile.get("name") or "Unknown entity"
    parts: list[str] = []
    form = f.get("org_form") or {}
    head = f"{name} (org.nr {spaced(orgnr)})"
    if f.get("status.deleted"):
        head += f" was removed from Enhetsregisteret on {f.get('deleted_date', 'an unknown date')}."
        parts.append(head)
        return " ".join(parts)
    if form:
        head += f" is registered as {form.get('description', '')} ({form.get('code', '')})"
    if f.get("entity_type") == "underenhet":
        head += f", a sub-unit of {f.get('parent_orgnr')}"
    if f.get("founded"):
        head += f", founded {f['founded']}"
    if f.get("registered.enhetsregisteret"):
        head += f", registered {f['registered.enhetsregisteret']}"
    parts.append(head + ".")
    addr = f.get("address.business") or f.get("address.location") or f.get("address.postal")
    if addr:
        street = ", ".join(addr.get("street") or [])
        loc = " ".join(x for x in (addr.get("postal_code"), addr.get("city")) if x)
        parts.append(f"Business address: {', '.join(x for x in (street, loc) if x)}.")
    if f.get("industry.nace1"):
        n = f["industry.nace1"]
        parts.append(f"Main industry (NACE): {n.get('code')} {n.get('description')}.")
    if f.get("employees") is not None:
        ev = next((x for x in profile["facts"] if x["key"] == "employees"), {})
        sd = (ev.get("evidence") or [{}])[0].get("source_date")
        parts.append(f"Registered employees: {f['employees']:,}" + (f" (as of {sd})" if sd else "") + ".")
    latest = f.get("finance.latest")
    if latest:
        p = f"finance.{latest['year']}.{latest['statement_type']}"
        bits = []
        for key, label in (("revenue", "revenue"), ("operating_result", "operating result"), ("net_result", "net result"),
                           ("total_assets", "total assets"), ("equity", "equity")):
            s = fmt_amount(f.get(f"{p}.{key}"))
            if s:
                bits.append(f"{label} {s}")
        if bits:
            kind = "group" if latest["statement_type"] == "konsern" else "company"
            sentence = f"Latest filed annual accounts ({latest['year']}, {kind} statement): " + "; ".join(bits)
            other_type = "selskap" if latest["statement_type"] == "konsern" else "konsern"
            other_rev = fmt_amount(f.get(f"finance.{latest['year']}.{other_type}.revenue"))
            if other_rev:
                other_kind = "company" if other_type == "selskap" else "group"
                sentence += f" (the {other_kind} statement for the same year reports revenue {other_rev})"
            parts.append(sentence + ".")
    st = f.get("status") or {}
    flags = [k.replace("_", " ") for k, v in st.items() if v]
    if flags:
        parts.append("Registry status flags: " + ", ".join(flags) + ".")
    ceo = f.get("roles.ceo")
    chair = f.get("roles.board_chair")
    lead = []
    if isinstance(ceo, dict):
        lead.append(f"general manager (daglig leder) {ceo.get('name')}")
    if isinstance(chair, dict):
        lead.append(f"board chair {chair.get('name')}")
    members = f.get("roles.board_members")
    if isinstance(members, list):
        lead.append(f"{len(members)} other board member{'s' if len(members) != 1 else ''}")
    if lead:
        parts.append("Leadership: " + "; ".join(lead) + ".")
    if f.get("subunits.count"):
        parts.append(f"Sub-units registered: {f['subunits.count']}.")
    if f.get("website"):
        parts.append(f"Website: {f['website']}.")
    if f.get("web.description"):
        parts.append(f"The website describes the company as: \"{f['web.description'][:220]}\"")
    if f.get("lei"):
        parts.append(f"LEI: {f['lei']} ({f.get('lei.entity_status', '')}).")
    if f.get("wikipedia.en") or f.get("wikipedia.no"):
        parts.append(f"Wikipedia: {f.get('wikipedia.en') or f.get('wikipedia.no')}.")
    return " ".join(parts)


def template_explanation(profile: dict) -> str:
    lines = [company_summary(profile), ""]
    m = profile.get("matching") or {}
    lines.append("How each source was tied to this company:")
    for s in m.get("summary", []):
        lines.append(f"- {s}")
    rejected = [r for s in profile.get("sources", []) for r in s.get("rejected", [])]
    if rejected:
        lines.append(f"- {len(rejected)} fact(s) withheld because their page/record could not be tied to the company "
                     f"(see sources[].rejected).")
    lines.append("")
    ch = profile.get("last_run_changes") or []
    if profile.get("run_count", 1) <= 1:
        lines.append(f"First run: {sum(1 for x in profile.get('facts', []) if x.get('status') == 'active')} facts recorded.")
    elif not ch:
        lines.append("No changes since the previous run.")
    else:
        lines.append(f"Changes in this run ({len(ch)}):")
        for c in ch[:15]:
            if c["type"] == "changed":
                lines.append(f"- {c['key']}: {_short(c.get('old'))} -> {_short(c.get('new'))}")
            elif c["type"] == "added":
                lines.append(f"- {c['key']}: added ({_short(c.get('new'))})")
            else:
                lines.append(f"- {c['key']}: {c['type']}")
        if len(ch) > 15:
            lines.append(f"- ... and {len(ch) - 15} more")
    gaps = [s["source"] for s in profile.get("sources", []) if s.get("status") in ("not_found", "blocked", "skipped")]
    if gaps:
        lines.append("Not available: " + ", ".join(gaps) + ".")
    errs = [s["source"] for s in profile.get("sources", []) if s.get("status") in ("error", "budget")]
    if errs:
        lines.append("Could not be checked this run (previous values kept and marked stale): " + ", ".join(errs) + ".")
    return "\n".join(lines).strip()


def _short(v, n: int = 60) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 3] + "..."
