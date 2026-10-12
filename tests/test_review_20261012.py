"""2026-10-12 코드 재검토에서 발견한 오류의 회귀 테스트 (실제 API 미호출)."""
import unittest
from unittest.mock import patch

from rag_fixtures import fake_embed, knowledge_collection

from bubot.documents import read_corpus
from bubot.period_queries import aggregate_query, period
from bubot.rag_service import ask
from bubot.regions import region_catalog
from bubot.year_query import named_region


def analysis(**changes):
    return {**dict(intent='lookup', region_names=[], month=None, start_month=None, end_month=None,
                   display_unit='천원', level='시군구', rewritten_query='q', message=''), **changes}


class ReviewFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = read_corpus()
        cls.catalog = region_catalog(cls.corpus)

    def run_ask(self, question, parsed, history=None, condensed=None):
        with patch('bubot.rag_service.open_index', return_value=(self.corpus, knowledge_collection())), \
             patch('gpt_api.analyze_query', return_value=parsed), \
             patch('gpt_api.embed_texts', side_effect=fake_embed), \
             patch('gpt_api.condense_question', return_value=condensed or {'is_followup': False, 'standalone_question': question}), \
             patch('gpt_api.generate_answer', return_value={'answer': '모의 답변'}):
            return ask(question, history=history)

    def label(self, question):
        region, ambiguous = named_region(question, self.catalog)
        return (region or {}).get('label'), ambiguous

    def test_named_region_abbreviation_qualifier_and_ambiguity(self):
        self.assertEqual(self.label('강남 2025년 평균 가격'), ('서울 / 강남지역 / 동남권 / 강남구', False))
        self.assertEqual(self.label('포항 남구 2025년 평균'), ('경북 / 포항시 / 남구', False))
        self.assertEqual(self.label('경기 광주 2025년 평균')[0], '경기 / 동부1권 / 광주시')
        self.assertEqual(self.label('서울 중구 2025년 평균')[0], '서울 / 강북지역 / 도심권 / 중구')
        # 여러 곳에 있는 이름은 임의로 고르지 않음 (이전: 서울 중구·광주광역시를 말없이 선택)
        self.assertEqual(self.label('중구 2025년 평균'), (None, True))
        self.assertEqual(self.label('광주 2025년 평균 가격'), (None, True))
        self.assertEqual(self.label('제주 2025년 평균')[0], '제주')
        self.assertEqual(self.label('2025년 평균 가격'), (None, False))
        self.assertEqual(named_region('서울', {}), (None, False))

    def test_yearly_average_with_abbreviated_region(self):
        result = aggregate_query('강남 2025년 평균 가격', self.corpus, '천원')
        self.assertEqual(result['analysis']['intent'], 'period_mean')
        self.assertEqual(result['analysis']['resolved_region_ids'], ['38'])
        ambiguous = aggregate_query('중구 2025년 평균 가격', self.corpus, '천원')
        self.assertEqual(ambiguous['analysis']['intent'], 'clarify')

    def test_recent_years_average_uses_n_times_12_months(self):
        months = self.corpus.months
        self.assertEqual(period('서울 최근 3년 평균 가격', months, inclusive=True), ('2023-09', '2026-08'))
        result = aggregate_query('서울 최근 3년 평균 가격', self.corpus, '천원')
        self.assertEqual(result['analysis']['valid_months'], 36)
        # 하락 지역(변동률 비교)은 N년 전 같은 달 기준 유지
        decline = aggregate_query('4년간 데이터 중 변동율이 음수로 가는 지역', self.corpus, '천원')
        self.assertEqual((decline['analysis']['resolved_start'], decline['analysis']['resolved_end']), ('2022-08', '2026-08'))

    def test_parent_written_without_suffix_is_not_guess(self):
        result = self.run_ask('포항 남구 2026년 8월 가격', analysis(region_names=['경북 포항시 남구'], month='2026-08'))
        self.assertEqual(result['analysis']['intent'], 'lookup')
        self.assertIn('포항시 / 남구', result['facts'][0])
        still = self.run_ask('남구 2026년 8월 가격', analysis(region_names=['남구'], month='2026-08'))
        self.assertEqual(still['analysis']['intent'], 'clarify')

    def test_followup_nationwide_ranking_does_not_inherit_previous_region(self):
        history = [{'question': '서울 2026년 8월 가격', 'standalone_question': '서울 2026년 8월 가격', 'answer': '..',
                    'resolved': {'intent': 'lookup', 'regions': ['서울']}, 'clarification': False, 'error': False}]
        result = self.run_ask('시도 중 가장 싼 곳은?', analysis(intent='argmin', month='2026-08', level='시도'), history,
                              {'is_followup': True, 'standalone_question': '2026년 8월 시도 중 가장 싼 곳은?'})
        self.assertNotIn('서울', result['standalone_question'])
        self.assertIn('후보 17개', result['facts'][0])
        self.assertIn('경북', result['facts'][1])
        # 지역이 생략된 일반 후속 질문은 이전 지역을 계속 이어받음
        followup = self.run_ask('2025년 1월은?', analysis(region_names=['서울'], month='2025-01'), history,
                                {'is_followup': True, 'standalone_question': '2025년 1월 가격은?'})
        self.assertTrue(followup['standalone_question'].startswith('서울'))

    def test_years_ago_is_fixed_by_code_not_model(self):
        # 테스트 기록 2d597dec: 모델이 explain + 2013-10을 골라 '10년 전'과 다른 해의 문서로 답함
        result = self.run_ask('10년전 서울 집값', analysis(intent='explain', region_names=['서울'], month='2013-10'))
        self.assertEqual(result['route'], 'numeric')
        self.assertEqual((result['analysis']['intent'], result['analysis']['resolved_month']), ('lookup', '2016-08'))
        self.assertIn('2016-08', result['facts'][0])
        self.assertTrue(any(f.startswith('2016-08 서울') for f in result['facts']))
        months = self.run_ask('서울 3개월 전 가격', analysis(region_names=['서울']))
        self.assertEqual(months['analysis']['resolved_month'], '2026-05')

    def test_years_ago_comparison_is_range_to_latest(self):
        result = self.run_ask('서울 10년 전보다 얼마나 올랐어?', analysis(intent='lookup', region_names=['서울'], month='2013-10'))
        a = result['analysis']
        self.assertEqual((a['intent'], a['resolved_start'], a['resolved_end']), ('trend', '2016-08', '2026-08'))
        clamped = self.run_ask('서울 20년 전부터 추이', analysis(intent='trend', region_names=['서울']))
        self.assertEqual(clamped['analysis']['resolved_start'], '2012-01')
        self.assertIn('2012-01부터 계산', clamped['facts'][0])
        with self.assertRaisesRegex(ValueError, '2006-08'):  # 보유 범위 밖 시점은 다른 월로 바꾸지 않음
            self.run_ask('20년 전 서울 집값', analysis(intent='lookup', region_names=['서울']))


if __name__ == '__main__':
    unittest.main()
