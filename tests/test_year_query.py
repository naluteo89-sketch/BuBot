import unittest
from datetime import datetime
from bubot.documents import read_corpus as read_full_corpus
from dataclasses import replace

def read_corpus():
    c=read_full_corpus()
    return replace(c, months=tuple(m for m in c.months if m >= "2025-11"))

from bubot.year_query import last_year_answer

class LastYearTests(unittest.TestCase):
    def test_max_uses_previous_year_only(self):
        corpus=read_corpus()
        result=last_year_answer('가진 데이터에서 작년 최고 거래 데이터',corpus,'천원',today=datetime(2026,10,8))
        self.assertEqual(result['analysis']['available_months'],['2025-11','2025-12'])
        self.assertNotIn('2026-',result['answer'])
        self.assertIn('2,818,950',result['answer'])
        self.assertIn('2025-12',result['facts'][0])
        self.assertIn('연간 전체를 대표하지 않습니다',result['answer'])

    def test_unavailable_month_not_replaced(self):
        r=last_year_answer('작년 4월 서울 가격',read_corpus(),'천원',today=datetime(2026,10,8))
        self.assertIn('요청 월은 원본에 없습니다',r['answer'])
        self.assertFalse(r['facts'])

    def test_specific_region(self):
        r=last_year_answer('작년 서울 가격',read_corpus(),'천원',today=datetime(2026,10,8))
        self.assertEqual(len(r['facts']),2)
        self.assertIn('1,275,909',r['facts'][0])
        self.assertIn('1,289,426',r['facts'][1])

    def test_full_2025_now_available(self):
        result=last_year_answer('작년 서울 가격',read_full_corpus(),'천원',today=datetime(2026,10,8))
        self.assertEqual(len(result['facts']),12)
        self.assertNotIn('연간 전체를 대표하지 않습니다',result['answer'])
