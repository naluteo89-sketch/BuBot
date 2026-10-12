import unittest
from types import SimpleNamespace
from unittest.mock import patch
from bubot.declines import declining_regions

class DeclineTests(unittest.TestCase):
    def test_endpoints_missing_and_order(self):
        docs=[]
        for rid,values in [('a',(100,80)),('b',(100,90)),('c',(100,110)),('d',(None,80))]:
            for month,value in zip(['2025-11','2026-08'],values):
                docs.append(SimpleNamespace(metadata=dict(kind='monthly',region_id=rid,month=month,price_thousand_krw=value,source_row=4,source_sha256='test')))
        corpus=SimpleNamespace(months=['2025-11','2026-08'],documents=docs)
        cat={x:{'name':x,'label':x} for x in 'abcd'}
        with patch('bubot.declines.region_catalog',return_value=cat),patch('bubot.declines.ranking_regions',return_value=set(cat)):
            r=declining_regions('모든 기간 데이터중 하락한 지역들',corpus,'천원')
        self.assertEqual(r['analysis']['count'],2)
        self.assertTrue(r['facts'][0].startswith('a:'))
        self.assertIn('-20.00%',r['facts'][0])
        self.assertIn('1개 지역은 제외',r['answer'])
