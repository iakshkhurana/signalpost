# Signalpost — submission sheet

Entry for **Signalpost: Build An Agent That Finds Company Information** (Builderr / Unstop).
Send this file, the repository link and the commit hash to submit@builderr.ai.

| Item | Value |
|---|---|
| Repository | https://github.com/iakshkhurana/signalpost |
| Exact commit hash | `dccd4e58e74fdb89069b752648cc1c5fedd81c31` (code and the 1000 profiles; the commit after it only fills in this sheet)` |
| One command to run | `./run.sh companies.txt` (or `python -m signalpost run --input companies.txt`) |
| Input format | text file, one 9-digit Norwegian organisation number per line (`#` comments allowed) |
| Output | `profiles/<orgnr>.json` per company, `profiles/index.json`, `runs/<timestamp>.json` run report; web viewer `index.html` (`python -m signalpost serve`) |
| Company profiles included | `profiles/` — see `profiles/index.json` (1000 companies, sampled with `python -m signalpost discover --count 1000 --seed 20261005`, list in `data/companies-1000.txt`) |
| Runtime | Python 3.11+ (tested on 3.14), `pip install -r requirements.txt` |
| Model / API details | Data: Brreg Enhetsregisteret + Regnskapsregisteret (NLOD 2.0), GLEIF LEI API (CC0), Wikidata SPARQL + Wikipedia REST (CC0 / CC BY-SA), NAV public job board, DiBK central-approval register, Kartverket address API (CC BY 4.0), the company's registry-declared website (robots.txt honoured, home + up to 4 linked pages). LLM: **optional**, off by default; `--llm` uses Anthropic `claude-opus-5-5` (Messages API, structured JSON output, effort low, server-side refusal fallback) only to phrase the explanation; output is rejected if it contains any number not present in the facts. |
| Expected run cost (100 companies/day) | Outbound requests ≈ 1200 (measured mean 11.8 per company over 1000 companies; cap 2000). Wall time ≈ 6 min at `--concurrency 8` (cap 45 min). External API cost **$0.00** without `--llm`; ≈ **$2.00** with `--llm` (hard cap `--max-cost 8`). |
| Daily-limit enforcement | built in: `--max-requests 2000 --time-budget 2700 --max-cost 8` are the defaults; the run stops cleanly and reports what it skipped |
| Update behaviour | re-running merges into stored profiles (added / changed / retracted, history kept); `python -m signalpost refresh` uses Brreg's update feed to re-run only changed or stale companies |
| Built with JBOX | no |

## How the scoring criteria are addressed

* **Recall and coverage (50)** — eleven source adapters, 83.6 facts per company on average over the 1000 submitted profiles, across 19 information types: identity and registration, industry and purpose, addresses, map location, contact details, social media, leadership and roles, employees, sub-units, up to 3 years of filed accounts (company and group), share capital, group / ownership, **job postings** (NAV job board + schema.org JobPosting on the company's careers page), **news** (RSS/Atom feed or news page), **products and services**, website content, licences and approvals (DiBK), LEI, Wikipedia / Wikidata. For companies that declared no website, `<name>.no` is tried and kept only when the site prints the org number. Per-type company coverage is in `python -m signalpost stats` and in the README.
* **Precision and evidence (30)** — every fact carries `evidence[]` with URL, source date, retrieval time, confidence and a `match` type (`orgnr_exact`, `orgnr_on_page`, `registry_name_exact`, `name_on_page`, `address_lookup`, `wikidata_p2333`). Registry records must echo the org number; GLEIF and Wikidata are matched on exact identifiers; job ads are kept only when the employer name equals a registered unit name; website facts need the org number or the company name on the site and are otherwise withheld (`sources[].rejected`). Financial values are copied verbatim from filings. `python -m signalpost validate` enforces all of this over the store.
* **Synthesis (12)** — `explanation.sections`: what the company does (every sentence cited with its source), key numbers with year-over-year change, what changed in this run, what remains unknown (per information type, with the reason the source gave), and how each source was tied to the company. Optional Claude brief (`--llm`), accepted only if every number it mentions exists in the facts.
* **UX (8)** — `index.html` + `python -m signalpost serve`: searchable list, filters by information type and match level, profile pages grouped by type with one-click evidence links, side-by-side compare of up to four companies with evidence per cell, change log, responsive layout for phone and desktop. Also `show`, `validate`, `stats`, stable JSON keys.

## Verifying locally

```sh
python -m unittest discover -s tests          # offline unit tests
python -m signalpost run 923609016            # one company (~10 requests, ~5 s)
python -m signalpost show 923609016
python -m signalpost validate                 # integrity checks over profiles/
python -m signalpost stats
```
