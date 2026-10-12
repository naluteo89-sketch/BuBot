import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from bubot.coverage import year_coverage
from rag_fixtures import NOT_LOCATED

from bubot.rag_service import ask

MONTHS = ['2025-11', '2025-12'] + [f'2026-{m:02}' for m in range(1, 9)]

class CoverageTests(unittest.TestCase):
    def test_partial_year(self):
        r = year_coverage('25년 가진 데이터 평균 거래 금액', MONTHS)
        self.assertIn('11월·12월만', r['answer'])
        self.assertIn('원본 CSV에 포함되어 있지 않습니다', r['answer'])
        self.assertEqual(r['analysis']['coverage'][0]['missing_months'], list(range(1,11)))
        self.assertIn('개별 거래', r['answer'])

    def test_absent_year(self):
        r = year_coverage('2024년 평균', MONTHS)
        self.assertIn('2024년에 해당하는 데이터는 현재 원본 CSV에 없습니다', r['answer'])

    def test_relative_year(self):
        r = year_coverage('작년 평균', MONTHS, today=datetime(2026,10,8))
        self.assertEqual(r['analysis']['coverage'][0]['year'],2025)

    def test_explicit_month_unchanged(self):
        self.assertIsNone(year_coverage('25년 11월 가격', MONTHS))
        self.assertIsNone(year_coverage('2026년 6월부터 8월 추이', MONTHS))

    def test_complete_or_partial_year_question_goes_to_rag(self):
        full = [f'{y}-{m:02}' for y in (2020, 2021) for m in range(1, 13)] + ['2022-01']
        self.assertIsNone(year_coverage('2021년에 가장 많이 오른 지역은?', full))
        self.assertIsNone(year_coverage('2022년 많이 오른 곳', full))
        self.assertIsNotNone(year_coverage('2022년 전체 평균', full))
        self.assertIsNotNone(year_coverage('2019년 많이 오른 곳', full))

    def test_no_invented_months(self):
        r = year_coverage('25년 자료', ['2025-11'])
        self.assertEqual(r['analysis']['coverage'][0]['available_months'], [11])

    def test_precedes_model_rejection(self):
        with patch('bubot.rag_service.open_index', return_value=(SimpleNamespace(months=MONTHS, documents=[]), None)), patch('bubot.rag_service.gpt_api.analyze_query') as model, \
                patch('gpt_api.locate_place', return_value=NOT_LOCATED):
            r = ask('25년 가진 데이터 평균 거래 금액')
            model.assert_not_called()
            self.assertIn('11월·12월만', r['answer'])
