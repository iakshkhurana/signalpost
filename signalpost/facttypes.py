"""Information types: every fact key maps to one type. Used by the viewer, the
explanation ("what remains unknown"), `stats` and coverage reporting."""
from __future__ import annotations

import re

# (regex on the fact key, type id, human label). First match wins.
_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^(jobs\.|web\.careers)"), "jobs", "Job postings"),
    (re.compile(r"^(news\.|web\.news)"), "news", "News"),
    (re.compile(r"^finance\."), "finance", "Financial statements"),
    (re.compile(r"^capital\."), "capital", "Share capital"),
    (re.compile(r"^roles\."), "people", "Leadership and roles"),
    (re.compile(r"^(employees$|subunits\.employees_total$|wikidata\.employees$)"), "employees", "Employees"),
    (re.compile(r"^subunits\."), "subunits", "Sub-units"),
    (re.compile(r"^(address\.)"), "address", "Addresses"),
    (re.compile(r"^location\."), "location", "Location (map)"),
    (re.compile(r"^(industry\.|purpose$|activity$|sector\.)"), "industry", "Industry and purpose"),
    (re.compile(r"^(website$|phone$|mobile$|email$|web\.emails$|web\.phones$|web\.contact_page$|web\.url$|approvals\.dibk_declared_website$)"), "contacts", "Contact details"),
    (re.compile(r"^web\.social$"), "social", "Social media"),
    (re.compile(r"^web\.products"), "products", "Products and services"),
    (re.compile(r"^web\."), "web", "Website content"),
    (re.compile(r"^approvals\."), "approvals", "Licences and approvals"),
    (re.compile(r"^lei"), "lei", "LEI"),
    (re.compile(r"^(wikidata\.|wikipedia\.)"), "wikipedia", "Wikipedia / Wikidata"),
    (re.compile(r"^(group\.|parent_orgnr$|wikidata\.(parent|owned))"), "ownership", "Group and ownership"),
    (re.compile(r".*"), "identity", "Identity and registration"),
]

TYPE_ORDER = ["identity", "industry", "address", "location", "contacts", "social", "people", "employees", "subunits",
              "finance", "capital", "ownership", "jobs", "news", "products", "web", "approvals", "lei", "wikipedia"]
LABELS = {t: label for _, t, label in _RULES}

# What a reader can expect per type and which sources would provide it; used
# for the "what remains unknown" part of the explanation.
EXPECTED: list[tuple[str, str, str]] = [
    ("finance", "filed annual accounts", "brreg_regnskap"),
    ("people", "leadership and board", "brreg_roller"),
    ("employees", "registered employee count", "brreg_enheter"),
    ("contacts", "website or contact details", "website"),
    ("web", "website content (description, about text)", "website"),
    ("social", "social media profiles", "website"),
    ("news", "news items", "website"),
    ("jobs", "job postings", "nav_jobs"),
    ("products", "products and services", "website"),
    ("location", "map coordinates of the business address", "geonorge"),
    ("lei", "Legal Entity Identifier", "gleif"),
    ("wikipedia", "Wikipedia / Wikidata entry", "wikidata"),
    ("approvals", "central approval for building work (DiBK)", "dibk"),
]


def fact_type(key: str) -> str:
    for rx, t, _ in _RULES:
        if rx.search(key):
            return t
    return "identity"


def group_facts(facts: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in facts:
        out.setdefault(fact_type(f["key"]), []).append(f)
    return {t: out[t] for t in TYPE_ORDER if t in out} | {t: v for t, v in out.items() if t not in TYPE_ORDER}
