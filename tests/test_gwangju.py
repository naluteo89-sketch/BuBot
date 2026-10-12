import unittest
from bubot.regions import gwangju_clarification, resolve_region, region_catalog
from bubot.documents import read_corpus

class GwangjuTests(unittest.TestCase):
    def test_ambiguous_variants(self):
        for text in ['광주', '광주시', '전남 광주시', '전라남도 광주']:
            self.assertIn('광주광역시와 경기도 광주시', gwangju_clarification(text))

    def test_explicit_regions(self):
        catalog = region_catalog(read_corpus())
        metro = resolve_region('광주광역시', catalog)
        city = resolve_region('경기도 광주시', catalog)
        self.assertNotEqual(metro, city)
        for text in ['광주광역시 가격', '경기도 광주시 가격']:
            self.assertIsNone(gwangju_clarification(text))
