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
