# Signalpost

An agent that takes a Norwegian organisation number (organisasjonsnummer) and
builds an evidence-backed company profile from permitted public sources. Every
fact carries the URL it came from, the date the source attaches to it, when we
fetched it, and an explicit statement of *why the record belongs to this
company*. Profiles are kept current: re-running the agent diffs the new facts
against the stored profile and records additions, changes and retractions.

Built for the Builderr "Signalpost" challenge.

## One command

```sh
# Linux / macOS
./run.sh companies.txt            # one organisation number per line
./run.sh 923609016 878703812      # or inline

# Windows
.\run.ps1 companies.txt

# or directly
pip install -r requirements.txt
python -m signalpost run --input companies.txt
```

Output: `profiles/<orgnr>.json` (one per company), `profiles/index.json`, and a
run report `runs/<timestamp>.json` with the budget actually used.

The defaults enforce the daily limits: at most **2000 outbound requests**,
**45 minutes**, and **$8 of declared LLM spend** (the LLM is off unless `--llm`
is passed). When a limit is hit the run stops cleanly, saves what it has, and
lists the companies it skipped.

Requirements: Python 3.11+, `httpx`, `beautifulsoup4`. Optional: `anthropic`
for `--llm`. No API keys are needed for the data sources.

## Sources (all permitted public sources, no scraping behind logins)

| Source | What it gives | Licence / terms | How we tie it to the company |
|---|---|---|---|
| Brreg **Enhetsregisteret** API (`data.brreg.no/enhetsregisteret/api/enheter/{orgnr}`) | legal name and name history, legal form, dates, addresses, NACE codes, sector, employees, declared website/phone, purpose, share capital, status flags (bankruptcy, liquidation), register memberships, parent | NLOD 2.0 (open government data) | looked up by org number; the record must echo the same number |
| Brreg **roles** (`/enheter/{orgnr}/roller`) | general manager (daglig leder), board chair and members, auditor, accountant, owner, partners | NLOD 2.0 | endpoint is addressed by org number; birth dates are **not** published |
| Brreg **sub-units** (`/underenheter?overordnetEnhet=`) | count and list of registered sub-units with location and employees | NLOD 2.0 | filtered on parent = org number and re-checked per unit |
| Brreg **Regnskapsregisteret** (`data.brreg.no/regnskapsregisteret/regnskap/{orgnr}`) | filed annual accounts: revenue, operating result, net result, assets, equity, debt, cash, audit info; company and group statements for the years the API returns | NLOD 2.0 | each filing's `virksomhet.organisasjonsnummer` must equal the org number; values are copied verbatim, never computed |
| **GLEIF** LEI API | LEI, legal name, addresses, legal form (ELF), registration status, parent reporting | CC0 | `entity.registeredAs` (digits) must equal the org number and the record must be Norwegian (RA000472 / NO) |
| **Wikidata** SPARQL | Wikipedia links, description, websites, inception, HQ, industries, employees, parent/owner, CEO, tickers | CC0 | item must carry property P2333 (Norwegian organisation number) = org number |
| **Company website** | title, description, language, e-mails, phones, social profiles, JSON-LD organisation data, about-page text | the company's own public pages; `robots.txt` honoured; at most 2 pages; identified user agent | URL is the one the company declared to Enhetsregisteret (or Wikidata). Page facts are published only if the page itself identifies the company: org number printed on the page (**high**) or company name on the page / in the domain (**medium**). Otherwise the facts are withheld and listed under `sources[].rejected`. |

Not used on purpose: commercial aggregators (proff.no, 1881.no, Purehelp, etc.)
whose terms forbid automated access, search engines, social networks' own
pages, and anything requiring login.

## Profile format

```jsonc
{
  "orgnr": "923609016",
  "name": "EQUINOR ASA",
  "last_run": "2026-10-05T05:06:17Z", "run_count": 3,
  "entity_status": "ok",                      // ok | not_found | deleted
  "matching": {
    "overall": "high",                        // high | medium | none
    "checks": [ { "source": "gleif", "check": "lei_registered_as", "passed": true, "detail": "..." }, ... ],
    "summary": [ "brreg_enheter: looked up by org number; record echoes the number", ... ]
  },
  "facts": [
    {
      "key": "finance.2024.konsern.revenue",
      "value": { "amount": 103774000000.0, "currency": "USD", "period_from": "2024-01-01", "period_to": "2024-12-31" },
      "confidence": "high",
      "status": "active",                     // active | retracted
      "first_seen": "...", "last_seen": "...",
      "history": [ { "value": ..., "until": "..." } ],
      "evidence": [ {
        "source": "brreg_regnskap",
        "url": "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
        "source_date": "2024-12-31",          // date the source attaches to the fact
        "retrieved_at": "2026-10-05T05:06:17Z",
        "match": "orgnr_exact",               // orgnr_exact | registry_declared | orgnr_on_page | name_on_page
        "note": "Regnskapsregisteret filing id=5667199 journalnr=2025428073; value copied verbatim"
      } ]
    }
  ],
  "sources": [ { "source": "website", "status": "ok", "url": "...", "requests": 3, "checks": [...], "rejected": [...] } ],
  "last_run_changes": [ { "at": "...", "key": "employees", "type": "changed", "old": 21272, "new": 21300 } ],
  "changes": [ ... ],                         // full change log (last 500)
  "explanation": { "mode": "template", "text": "..." },   // or mode "llm" with summary/highlights/caveats
  "stats": { "active_facts": 170, "retracted_facts": 0, "stale_facts": 0, "requests": 9, "seconds": 4.0 }
}
```

Fact keys are stable, dotted names: `name`, `org_form`, `founded`,
`address.business`, `industry.nace1`, `employees`, `website`, `status`,
`registers`, `capital.share`, `roles.ceo`, `roles.board_chair`,
`roles.board_members`, `subunits.count`, `finance.<year>.<selskap|konsern>.<metric>`,
`lei`, `lei.registration`, `wikipedia.en`, `wikidata.description`, `web.title`,
`web.description`, `web.social`, `web.emails`, ... Use `python -m signalpost show <orgnr>`
to see them as a table.

## Keeping profiles current

* `python -m signalpost run ...` on an already-profiled company merges the new
  facts into the stored profile: new keys are **added**, different values are
  **changed** (the old value moves to `history`), keys that a *successfully
  fetched* source no longer reports are **retracted** (kept with
  `status: retracted`), and keys whose source errored or ran out of budget are
  kept and marked `stale` rather than deleted.
* `python -m signalpost refresh` asks Brreg's update feed
  (`/oppdateringer/enheter`) which stored companies changed since the last run,
  adds profiles older than `--stale-days` (default 7, because accounts, LEI and
  Wikidata have no change feed), and re-runs only those. `--all` re-runs everything.

## Accuracy safeguards

* Financial values come **only** from Regnskapsregisteret filings and are copied
  verbatim with the filing id and journal number in the evidence. Nothing is
  derived, scaled or converted.
* Every registry response is checked to echo the requested org number; GLEIF and
  Wikidata are matched on exact identifiers; website facts need the org number
  or company name on the page or domain.
* Sub-unit numbers (underenheter) are recognised and labelled, with a pointer to
  the parent entity.
* Personal data is minimised: role holders are published by name and role only
  (no birth dates), as they appear in the public register.
* `python -m signalpost validate` checks every stored profile: valid check digit,
  evidence with URL and date on every fact, no financial fact from a non-filing
  source, no low-confidence website fact published.

## Explanations

Each profile carries a deterministic, template-based explanation: a short brief
of the company, how each source was tied to it, what changed in this run, and
which sources were unavailable. With `--llm` and `ANTHROPIC_API_KEY` set, Claude
(`claude-opus-5-5` by default, `--model` to change) writes a richer
summary/highlights/caveats from the same facts. Its output is accepted only if
every number of four or more digits it mentions exists in the input facts;
otherwise the template is used. The LLM never adds facts to the profile.

## Model / API details and expected costs

| Item | Detail |
|---|---|
| Data APIs | Brreg (Enhetsregisteret, Regnskapsregisteret), GLEIF, Wikidata: free, no key, no declared cost |
| Website fetches | ≤ 2 pages + robots.txt per company, only the registry-declared site |
| Requests per company | measured over 1000 companies: mean 8.0, median 7, p95 11, max 16; hard cap `--per-company-requests 25` |
| 100 companies / day | ≈ 800 outbound requests (cap 2000), ≈ 2 minutes at `--concurrency 8` (1000 companies took 17.7 min and 8036 requests) |
| LLM (optional) | `claude-opus-5-5` via the Anthropic API, structured JSON output, low effort, server-side refusal fallback enabled; ≈ 3k input + 400 output tokens per company → ≈ $0.02 per company, ≈ $2 per 100 companies; hard cap `--max-cost 8` |
| Declared daily external API cost | **$0.00** without `--llm`; **≈ $2 (≤ $8 cap)** with `--llm` |

## Measured coverage (the 1000 submitted profiles)

| | |
|---|---|
| Profiles | 1000, `validate` reports 0 problems |
| Facts per profile | mean 80.6 (min 16, max 170) |
| Brreg entity / roles / sub-units | 100 % |
| Filed annual accounts | 88 % (10.8 % have no accounts in the open API, 1.2 % the API answers HTTP 500) |
| Company website | 33 % of companies declare a site; 27 % could be fetched. Of the pages fetched, 23 % print the org number (high), 62 % match by name (medium), 15 % withheld as unverifiable |
| GLEIF LEI | 21 % |
| Wikidata / Wikipedia | 9.5 % |
| Entity-match verdict | high 91 %, medium 9 % (medium = website facts matched by name only) |

## Commands

```
python -m signalpost run <orgnr ...> | --input FILE   profile companies (see --help for budgets, --llm, --cache-dir)
python -m signalpost refresh [--all] [--stale-days N]  re-run changed / stale profiles via the Brreg update feed
python -m signalpost discover --count 1000            sample a varied set of companies from Enhetsregisteret
python -m signalpost show <orgnr> [--json] [--all]    print explanation and fact table
python -m signalpost validate                         integrity checks over the store
python -m signalpost stats                            coverage statistics
python -m signalpost export --out-file profiles.jsonl single-file export
python -m unittest discover -s tests                  unit tests (offline)
```

## Repository layout

```
signalpost/
  cli.py          commands
  pipeline.py     per-company orchestration: fetch -> match -> merge -> explain -> save
  http.py         budgeted async client (request/time/cost caps, retries, robots.txt, dev cache)
  models.py       Fact / Evidence / SourceResult
  merge.py        update logic (added / changed / retracted / stale)
  match.py        cross-source entity-matching summary
  explain.py      template explanation      llm.py  optional Claude explanation
  discover.py     sampling companies from the registry
  sources/        brreg.py  regnskap.py  gleif.py  wikidata.py  website.py
data/companies-1000.txt   the sample used for the submitted profiles
profiles/                 1000+ generated profiles + index.json
tests/                    unit tests
```
