"""Unit tests for the offline parts: org-number checks, merge/update logic,
website URL normalisation, number grounding for LLM output. Run with
`python -m unittest discover -s tests` or `pytest`."""
import unittest

from signalpost.orgnr import is_valid, normalize, parse_many, page_pattern, spaced
from signalpost.merge import merge
from signalpost.models import Fact, fact
from signalpost.sources.website import normalize_url, _name_matches_domain
from signalpost.llm import Explainer


class OrgnrTests(unittest.TestCase):
    def test_valid(self):
        for o in ("923609016", "878703812", "974760673", "985 013 268"):
            self.assertTrue(is_valid(o), o)

    def test_invalid(self):
        for o in ("923609017", "12345678", "000000000", "abc"):
            self.assertFalse(is_valid(o), o)

    def test_normalize_and_parse(self):
        self.assertEqual(normalize("NO 923 609 016 MVA"), "923609016")
        self.assertEqual(parse_many("923609016\n# comment\n878703812, 923609016 bogus"), ["923609016", "878703812"])

    def test_page_pattern(self):
        rx = page_pattern("923609016")
        self.assertTrue(rx.search("Org.nr: 923 609 016 MVA"))
        self.assertTrue(rx.search("orgnr 923609016"))
        self.assertTrue(rx.search("923.609.016"))
        self.assertFalse(rx.search("1923609016"))
        self.assertEqual(spaced("923609016"), "923 609 016")


def _f(key, value, source="brreg_enheter"):
    return fact(key, value, source=source, url="https://x", retrieved_at="2026-01-01T00:00:00Z")


class MergeTests(unittest.TestCase):
    def test_first_run_adds(self):
        facts, changes = merge([], [_f("name", "A")], {"brreg_enheter": "ok"}, "2026-01-02T00:00:00Z")
        self.assertEqual([c["type"] for c in changes], ["added"])
        self.assertEqual(facts[0].first_seen, "2026-01-02T00:00:00Z")

    def test_change_keeps_history(self):
        old, _ = merge([], [_f("employees", 10)], {"brreg_enheter": "ok"}, "2026-01-01T00:00:00Z")
        new, changes = merge(old, [_f("employees", 12)], {"brreg_enheter": "ok"}, "2026-02-01T00:00:00Z")
        self.assertEqual(changes[0]["type"], "changed")
        self.assertEqual(new[0].value, 12)
        self.assertEqual(new[0].history[0]["value"], 10)
        self.assertEqual(new[0].first_seen, "2026-01-01T00:00:00Z")

    def test_retract_only_when_source_verified(self):
        old, _ = merge([], [_f("website", "x.no")], {"brreg_enheter": "ok"}, "2026-01-01T00:00:00Z")
        # source errored this run -> keep, mark stale
        kept, changes = merge(old, [], {"brreg_enheter": "error"}, "2026-02-01T00:00:00Z")
        self.assertEqual(changes, [])
        self.assertTrue(kept[0].stale)
        self.assertEqual(kept[0].status, "active")
        # source ok but fact gone -> retracted
        gone, changes = merge(old, [], {"brreg_enheter": "ok"}, "2026-03-01T00:00:00Z")
        self.assertEqual(changes[0]["type"], "retracted")
        self.assertEqual(gone[0].status, "retracted")
        # and it comes back
        back, changes = merge(gone, [_f("website", "x.no")], {"brreg_enheter": "ok"}, "2026-04-01T00:00:00Z")
        self.assertEqual(changes[0]["type"], "reactivated")
        self.assertEqual(back[0].status, "active")

    def test_roundtrip_dict(self):
        f = _f("name", "A")
        self.assertEqual(Fact.from_dict(f.to_dict()).to_dict(), f.to_dict())


class WebsiteUrlTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_url("www.equinor.com"), "https://www.equinor.com/")
        self.assertEqual(normalize_url("http://x.no/om-oss"), "http://x.no/om-oss")
        self.assertEqual(normalize_url("x.no, y.no"), "https://x.no/")
        self.assertIsNone(normalize_url("post@x.no"))
        self.assertIsNone(normalize_url("localhost"))
        self.assertIsNone(normalize_url(""))

    def test_name_matches_domain(self):
        self.assertTrue(_name_matches_domain("ADM AS", "adm.no"))
        self.assertTrue(_name_matches_domain("BILKOLLEKTIVET SA", "www.bilkollektivet.no"))
        self.assertTrue(_name_matches_domain("KJELL & CO NORWAY AS", "kjell.com"))
        self.assertTrue(_name_matches_domain("HAVILA SUBSEA CREWING AS", "havilasubsea.no"))
        self.assertFalse(_name_matches_domain("BLÅVEISEN BARNEHAGE SA", "blaaveisen.com"))  # 'aa' vs folded 'a': no guess
        self.assertFalse(_name_matches_domain("NORSK MEDISINALDEPOT AS", "vitusapotek.no"))
        self.assertFalse(_name_matches_domain("3 STEP IT AS", "stepit.com"))
        self.assertFalse(_name_matches_domain("A/S NORSKE SHELL", "shell.com"))  # 'norske' != 'shell'


class GroundingTests(unittest.TestCase):
    def test_numbers_must_exist_in_facts(self):
        allowed = {"107174000000", "2024", "21272", "923609016"}
        ok, bad = Explainer._grounded("Revenue was USD 107,174,000,000 in 2024 with 21,272 employees.", allowed)
        self.assertTrue(ok, bad)
        ok, bad = Explainer._grounded("Revenue was about USD 110,000,000,000.", allowed)
        self.assertFalse(ok)
        self.assertIn("110,000,000,000", bad[0])


if __name__ == "__main__":
    unittest.main()


class NewSourceTests(unittest.TestCase):
    def test_nav_name_matching(self):
        from signalpost.sources.nav_jobs import norm_name, search_term
        self.assertEqual(norm_name("H-FASADER AS AVD FREDRIKSTAD"), "h fasader as avd fredrikstad")
        self.assertEqual(norm_name("Kjell & Co Norway AS"), "kjell og co norway as")
        self.assertEqual(search_term("KJELDAAS AS"), "KJELDAAS")
        self.assertEqual(search_term("AS"), "AS")

    def test_guess_domains(self):
        from signalpost.sources.website import guess_domains
        self.assertEqual(guess_domains("KJELDAAS AS", "AS"), ["https://kjeldaas.no"])
        self.assertEqual(guess_domains("OLA NORDMANN", "ENK"), [])
        self.assertEqual(guess_domains("NORGE AS", "AS"), [])
        self.assertEqual(guess_domains("BJØRN BYGG AS", "AS"), ["https://bjornbygg.no"])

    def test_parse_feed(self):
        from signalpost.sources.website import parse_feed
        rss = """<?xml version="1.0"?><rss><channel><item><title>Ny kontrakt</title><link>/nyheter/kontrakt</link>
                 <pubDate>Mon, 06 Oct 2026 08:00:00 +0200</pubDate><description>&lt;p&gt;Vi har signert.&lt;/p&gt;</description></item></channel></rss>"""
        items = parse_feed(rss, "https://example.no/feed")
        self.assertEqual(items[0]["title"], "Ny kontrakt")
        self.assertEqual(items[0]["url"], "https://example.no/nyheter/kontrakt")
        self.assertEqual(items[0]["date"], "2026-10-06")
        self.assertEqual(items[0]["summary"], "Vi har signert.")
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Hello</title><link href="https://example.no/a"/><updated>2026-01-02T00:00:00Z</updated></entry></feed>"""
        self.assertEqual(parse_feed(atom, "https://example.no/")[0]["date"], "2026-01-02")

    def test_classify_links(self):
        from bs4 import BeautifulSoup
        from signalpost.sources.website import classify_links
        html = """<a href="/om-oss">Om oss</a><a href="/kontakt">Kontakt</a><a href="/karriere">Ledige stillinger</a>
                  <a href="https://other.example/news">Nyheter</a><a href="/aktuelt">Aktuelt</a><a href="/tjenester">Tjenester</a>"""
        picks = classify_links(BeautifulSoup(html, "html.parser"), "https://example.no/")
        self.assertEqual(picks["about"], "https://example.no/om-oss")
        self.assertEqual(picks["contact"], "https://example.no/kontakt")
        self.assertEqual(picks["careers"], "https://example.no/karriere")
        self.assertEqual(picks["news"], "https://example.no/aktuelt")   # external news link ignored
        self.assertEqual(picks["products"], "https://example.no/tjenester")

    def test_fact_types(self):
        from signalpost.facttypes import fact_type
        self.assertEqual(fact_type("jobs.posting.abc"), "jobs")
        self.assertEqual(fact_type("web.careers.listings"), "jobs")
        self.assertEqual(fact_type("news.item.x"), "news")
        self.assertEqual(fact_type("finance.2024.selskap.revenue"), "finance")
        self.assertEqual(fact_type("web.social"), "social")
        self.assertEqual(fact_type("name"), "identity")

    def test_explanation_sections(self):
        from signalpost.explain import template_explanation
        profile = {"orgnr": "923609016", "name": "X AS", "run_count": 2, "last_run_changes": [{"key": "employees", "type": "changed", "old": 1, "new": 2}],
                   "matching": {"summary": ["brreg_enheter: ok"]},
                   "sources": [{"source": "nav_jobs", "status": "not_found", "notes": ["no active ads"]}, {"source": "website", "status": "skipped", "notes": ["no usable website declared"]}],
                   "facts": [_f("name", "X AS").to_dict(), _f("employees", 2).to_dict(),
                             _f("finance.2024.selskap.revenue", {"amount": 200.0, "currency": "NOK"}, "brreg_regnskap").to_dict(),
                             _f("finance.2023.selskap.revenue", {"amount": 100.0, "currency": "NOK"}, "brreg_regnskap").to_dict(),
                             _f("finance.latest", {"year": "2024", "statement_type": "selskap"}, "brreg_regnskap").to_dict()]}
        ex = template_explanation(profile)
        self.assertIn("[brreg_enheter]", ex["sections"]["what_it_does"][0])
        self.assertTrue(any("+100% vs 2023" in s for s in ex["sections"]["key_numbers"]))
        self.assertTrue(any(s.startswith("No job postings") for s in ex["sections"]["unknown"]))
        self.assertIn("employees: 1 -> 2", ex["text"])
