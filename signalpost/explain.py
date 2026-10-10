"""Deterministic, template-based explanation of a profile. Always available;
the LLM explanation (llm.py) is optional and layered on top.

Returns both a plain-text version and structured sections:
  what_it_does      - what the company is and does, each sentence cited [source]
  key_numbers       - employees, latest accounts with year-over-year direction
  changes           - what changed in this run
  unknown           - information types we looked for and did not find, and why
  sources           - how each source was tied to the company
"""
from __future__ import annotations

from .facttypes import EXPECTED, fact_type
from .orgnr import spaced


def _facts(profile: dict) -> dict:
    return {f["key"]: f["value"] for f in profile.get("facts", []) if f.get("status", "active") == "active"}


def _src(profile: dict, key: str) -> str:
    f = next((x for x in profile.get("facts", []) if x["key"] == key and x.get("status") == "active"), None)
    return (f["evidence"][0]["source"] if f and f.get("evidence") else "") or ""


def _cite(profile: dict, key: str) -> str:
    s = _src(profile, key)
    return f" [{s}]" if s else ""


def fmt_amount(v: dict | None) -> str | None:
    if not isinstance(v, dict) or v.get("amount") is None:
        return None
    amt = v["amount"]
    s = f"{amt:,.0f}" if float(amt).is_integer() else f"{amt:,.2f}"
    return f"{v.get('currency', 'NOK')} {s}"


def what_it_does(profile: dict) -> list[str]:
    f = _facts(profile)
    orgnr = profile["orgnr"]
    name = f.get("name") or profile.get("name") or "Unknown entity"
    out: list[str] = []
    form = f.get("org_form") or {}
    head = f"{name} (org.nr {spaced(orgnr)})"
    if f.get("status.deleted"):
        out.append(head + f" was removed from Enhetsregisteret on {f.get('deleted_date', 'an unknown date')}." + _cite(profile, "status.deleted"))
        return out
    if form:
        head += f" is registered as {form.get('description', '')} ({form.get('code', '')})"
    if f.get("entity_type") == "underenhet":
        head += f", a sub-unit of {f.get('parent_orgnr')}"
    if f.get("founded"):
        head += f", founded {f['founded']}"
    if f.get("registered.enhetsregisteret"):
        head += f", registered {f['registered.enhetsregisteret']}"
    out.append(head + "." + _cite(profile, "name"))
    if f.get("industry.nace1"):
        n = f["industry.nace1"]
        extra = [f[k]["description"] for k in ("industry.nace2", "industry.nace3") if isinstance(f.get(k), dict) and f[k].get("description")]
        s = f"Main industry (NACE {n.get('code')}): {n.get('description')}"
        if extra:
            s += "; also " + "; ".join(extra)
        out.append(s + "." + _cite(profile, "industry.nace1"))
    if f.get("purpose"):
        out.append(f"Registered purpose: \"{str(f['purpose'])[:300]}\"" + _cite(profile, "purpose"))
    elif f.get("activity"):
        out.append(f"Registered activity: \"{str(f['activity'])[:300]}\"" + _cite(profile, "activity"))
    desc = f.get("web.description") or f.get("web.about_text") or f.get("web.home_text")
    dkey = "web.description" if f.get("web.description") else "web.about_text" if f.get("web.about_text") else "web.home_text"
    if desc:
        out.append(f"The company's website describes it as: \"{str(desc)[:300]}\"" + _cite(profile, dkey))
    wp = f.get("wikipedia.en.summary") or f.get("wikipedia.no.summary")
    wkey = "wikipedia.en.summary" if f.get("wikipedia.en.summary") else "wikipedia.no.summary"
    if isinstance(wp, dict) and wp.get("extract"):
        out.append(f"Wikipedia: \"{wp['extract'][:300]}\"" + _cite(profile, wkey))
    elif f.get("wikidata.description"):
        out.append(f"Wikidata describes it as: {f['wikidata.description']}." + _cite(profile, "wikidata.description"))
    addr = f.get("address.business") or f.get("address.location") or f.get("address.postal")
    akey = "address.business" if f.get("address.business") else "address.location" if f.get("address.location") else "address.postal"
    if addr:
        street = ", ".join(addr.get("street") or [])
        loc = " ".join(x for x in (addr.get("postal_code"), addr.get("city")) if x)
        out.append(f"Business address: {', '.join(x for x in (street, loc) if x)}." + _cite(profile, akey))
    prods = f.get("web.products")
    if isinstance(prods, list) and prods:
        out.append("Products / services named on its website: " + "; ".join(prods[:8]) + "." + _cite(profile, "web.products"))
    ceo, chair = f.get("roles.ceo"), f.get("roles.board_chair")
    lead = []
    if isinstance(ceo, dict):
        lead.append(f"general manager (daglig leder) {ceo.get('name')}")
    if isinstance(chair, dict):
        lead.append(f"board chair {chair.get('name')}")
    members = f.get("roles.board_members")
    if isinstance(members, list):
        lead.append(f"{len(members)} other board member{'s' if len(members) != 1 else ''}")
    if lead:
        out.append("Leadership: " + "; ".join(lead) + "." + _cite(profile, "roles.ceo" if ceo else "roles.board_chair"))
    if f.get("parent_orgnr"):
        out.append(f"Parent entity: {f['parent_orgnr']}." + _cite(profile, "parent_orgnr"))
    owners = f.get("wikidata.owned_by") or f.get("wikidata.parent_organizations")
    if owners:
        out.append("Owner / parent organisation (Wikidata): " + ", ".join(owners[:3]) + "." + _cite(profile, "wikidata.owned_by" if f.get("wikidata.owned_by") else "wikidata.parent_organizations"))
    st = f.get("status") or {}
    flags = [k.replace("_", " ") for k, v in st.items() if v]
    if flags:
        out.append("Registry status flags: " + ", ".join(flags) + "." + _cite(profile, "status"))
    return out


def key_numbers(profile: dict) -> list[str]:
    f = _facts(profile)
    out: list[str] = []
    if f.get("employees") is not None:
        ev = next((x for x in profile["facts"] if x["key"] == "employees"), {})
        sd = (ev.get("evidence") or [{}])[0].get("source_date")
        s = f"Registered employees: {f['employees']:,}" + (f" (as of {sd})" if sd else "")
        if f.get("subunits.count"):
            s += f"; {f['subunits.count']} registered sub-unit{'s' if f['subunits.count'] != 1 else ''}"
        out.append(s + "." + _cite(profile, "employees"))
    latest = f.get("finance.latest")
    if latest:
        y, t = latest["year"], latest["statement_type"]
        p = f"finance.{y}.{t}"
        bits = []
        for key, label in (("revenue", "revenue"), ("operating_result", "operating result"), ("net_result", "net result"),
                           ("total_assets", "total assets"), ("equity", "equity")):
            s = fmt_amount(f.get(f"{p}.{key}"))
            if s:
                prev = f.get(f"finance.{int(y) - 1}.{t}.{key}")
                cur = f.get(f"{p}.{key}")
                if isinstance(prev, dict) and prev.get("amount") not in (None, 0) and isinstance(cur, dict) and cur.get("amount") is not None:
                    pct = (cur["amount"] - prev["amount"]) / abs(prev["amount"]) * 100
                    s += f" ({'+' if pct >= 0 else ''}{pct:.0f}% vs {int(y) - 1})"
                bits.append(f"{label} {s}")
        if bits:
            kind = "group" if t == "konsern" else "company"
            out.append(f"Latest filed annual accounts ({y}, {kind} statement): " + "; ".join(bits) + "." + _cite(profile, f"{p}.revenue"))
        years = sorted({k.split(".")[1] for k in f if k.startswith("finance.") and k.split(".")[1].isdigit()})
        if len(years) > 1:
            out.append(f"Accounts on file for {', '.join(years)}.")
    if f.get("capital.share"):
        out.append(f"Share capital: {fmt_amount(f['capital.share'])}." + _cite(profile, "capital.share"))
    if f.get("jobs.active_count"):
        out.append(f"Open job postings on NAV's job board: {f['jobs.active_count']}." + _cite(profile, "jobs.active_count"))
    news = [k for k in f if k.startswith("news.item.")]
    if news:
        latest_news = max((f[k].get("date") or "" for k in news), default="")
        out.append(f"{len(news)} news item{'s' if len(news) != 1 else ''} from the company's own site" + (f", latest {latest_news}" if latest_news else "") + ". [website]")
    if f.get("lei"):
        out.append(f"LEI: {f['lei']} ({f.get('lei.entity_status', '')})." + _cite(profile, "lei"))
    appr = f.get("approvals.dibk_central_approval")
    if isinstance(appr, dict):
        out.append(f"DiBK central approval: {'valid' if appr.get('approved') else 'not valid'}" + (f" until {appr['valid_to']}" if appr.get("valid_to") else "") +
                   f", {len(appr.get('areas') or [])} approval area(s)." + _cite(profile, "approvals.dibk_central_approval"))
    return out


def changes_text(profile: dict) -> list[str]:
    ch = profile.get("last_run_changes") or []
    if profile.get("run_count", 1) <= 1:
        return [f"First run: {sum(1 for x in profile.get('facts', []) if x.get('status') == 'active')} facts recorded."]
    if not ch:
        return ["No changes since the previous run."]
    out = [f"Changes in this run ({len(ch)}):"]
    for c in ch[:15]:
        if c["type"] == "changed":
            out.append(f"- {c['key']}: {_short(c.get('old'))} -> {_short(c.get('new'))}")
        elif c["type"] == "added":
            out.append(f"- {c['key']}: added ({_short(c.get('new'))})")
        else:
            out.append(f"- {c['key']}: {c['type']}")
    if len(ch) > 15:
        out.append(f"- ... and {len(ch) - 15} more")
    return out


def unknowns(profile: dict) -> list[str]:
    present = {fact_type(x["key"]) for x in profile.get("facts", []) if x.get("status") == "active"}
    status = {s["source"]: s for s in profile.get("sources", [])}
    out: list[str] = []
    for t, label, src in EXPECTED:
        if t in present:
            continue
        s = status.get(src)
        st = s.get("status") if s else "skipped"
        note = (s.get("notes") or [""])[0] if s else ""
        if t == "approvals" and st == "skipped":
            continue  # only relevant for building trades; not a gap otherwise
        if t == "employees" and st == "ok":
            why = "the registry holds no employee count for this entity"
        elif st == "ok" and (s.get("rejected") or []):
            why = f"{src} reached, but " + (note or "its facts were withheld")
        elif st == "ok":
            why = f"{src} reached, but it carries no such information"
        elif st == "not_found":
            why = f"checked {src}: nothing registered" + (f" ({note})" if note else "")
        elif st in ("error", "budget"):
            why = f"{src} could not be reached this run" + (f" ({note})" if note else "")
        elif st == "blocked":
            why = f"{src} disallows automated access ({note})"
        elif st == "skipped":
            why = note or f"{src} not applicable"
        else:
            why = f"{src}: {st}"
        out.append(f"No {label}: {why}.")
    rejected = [r for s in profile.get("sources", []) for r in s.get("rejected", [])]
    if rejected:
        out.append(f"{len(rejected)} website fact(s) withheld because the page could not be tied to the company (see sources[].rejected).")
    stale = [x["key"] for x in profile.get("facts", []) if x.get("stale") and x.get("status") == "active"]
    if stale:
        out.append(f"{len(stale)} fact(s) could not be re-checked this run and are marked stale.")
    return out


def template_explanation(profile: dict) -> dict:
    sections = {
        "what_it_does": what_it_does(profile),
        "key_numbers": key_numbers(profile),
        "changes": changes_text(profile),
        "unknown": unknowns(profile),
        "sources": list((profile.get("matching") or {}).get("summary", [])),
    }
    lines = [" ".join(sections["what_it_does"]), ""]
    if sections["key_numbers"]:
        lines += ["Key numbers:"] + [f"- {s}" for s in sections["key_numbers"]] + [""]
    lines += sections["changes"] + [""]
    if sections["unknown"]:
        lines += ["What remains unknown:"] + [f"- {s}" for s in sections["unknown"]] + [""]
    lines += ["How each source was tied to this company:"] + [f"- {s}" for s in sections["sources"]]
    return {"text": "\n".join(lines).strip(), "sections": sections}


def _short(v, n: int = 60) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 3] + "..."
