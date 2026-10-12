"""2026-10-12 코드 재검토에서 발견한 오류의 회귀 테스트 (실제 API 미호출)."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rag_fixtures import fake_embed, knowledge_collection

import gpt_api
from bubot.answer_cache import ANSWER_CACHE
from bubot.config import MAX_REGIONS
from bubot.documents import read_corpus
from bubot.knowledge_index import retrieve
from bubot.period_queries import aggregate_query, period
from bubot.rag_service import _fit_budget, _limit, ask
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


class LastYearHeadlineTests(unittest.TestCase):
    """10/12 모바일 테스트: '작년 기준 서울에서 가장 싼 지역'이 결론 없이 설명부터 나오던 문제."""
    @classmethod
    def setUpClass(cls):
        from datetime import datetime
        from bubot.year_query import last_year_answer
        corpus = read_corpus()
        cls.ask = staticmethod(lambda q: last_year_answer(q, corpus, 'korean', today=datetime(2026, 10, 12)))

    def test_cheapest_region_answer_starts_with_conclusion(self):
        answer = self.ask('작년 기준 서울에서 가장 싼 지역')['answer']
        self.assertTrue(answer.startswith('2025년 서울에서 월평균 매매가격이 가장 낮은 지역은 도봉구입니다'), answer)
        self.assertIn('2025-01 ~ 2025-12, 12개월', answer)
        self.assertIn('비교 범위: 서울 / 시군구', answer)  # 기준 설명은 결론 뒤에 유지

    def test_headline_wording_by_scope(self):
        self.assertTrue(self.ask('작년 가장 비싼 곳')['answer'].startswith('2025년 전국에서 월평균 매매가격이 가장 높은 지역은 서울 강남구입니다'))
        self.assertTrue(self.ask('작년 시도 중 가장 싼 곳')['answer'].startswith('2025년 전국에서 월평균 매매가격이 가장 낮은 시도는 경북입니다'))
        self.assertTrue(self.ask('작년 강남구 최고가')['answer'].startswith('2025년 서울 강남구의 월평균 매매가격이 가장 높은 달은 2025-12'))
        self.assertTrue(self.ask('작년 서울 가격')['answer'].startswith('2025년 서울의 월평균 매매가격은 다음과 같습니다.'))


class ConsistencyAndFallbackTests(unittest.TestCase):
    """10/12: 동일 질문 답변 일관성, 반드시 답변(대체 처리), 검색 범위 확대, 공통 호출 간격."""
    @classmethod
    def setUpClass(cls):
        cls.corpus = read_corpus()

    def setUp(self):
        ANSWER_CACHE.clear()

    def patches(self, parsed, generate=None, analyze=None):
        return (patch('bubot.rag_service.open_index', return_value=(self.corpus, knowledge_collection())),
                patch('gpt_api.analyze_query', side_effect=analyze, return_value=parsed),
                patch('gpt_api.embed_texts', side_effect=fake_embed),
                patch('gpt_api.generate_answer', **(generate or {'return_value': {'answer': '모의 답변'}})))

    def test_same_question_returns_same_answer_from_cache(self):
        parsed = analysis(region_names=['서울'], month='2026-08')
        a, b, c, generate_patch = self.patches(parsed, {'side_effect': [{'answer': '첫 답변'}, {'answer': '다른 답변'}]})
        with a, b, c, generate_patch as generate:
            first = ask('서울 2026년 8월 가격')
            second = ask('서울  2026년 8월 가격')  # 공백만 다른 같은 질문
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(second['answer'], first['answer'])
        self.assertTrue(second['cached'])
        self.assertNotIn('cached', first)

    def test_unverified_answer_is_not_cached(self):
        parsed = analysis(region_names=['서울'], month='2026-08')
        a, b, c, generate_patch = self.patches(parsed, {'return_value': {'answer': '서울은 99.9% 올랐습니다.'}})
        with a, b, c, generate_patch as generate:
            first = ask('서울 2026년 8월 가격')
            ask('서울 2026년 8월 가격')
        self.assertEqual(first['analysis']['answer_check'], 'warning')
        self.assertEqual(generate.call_count, 4)  # 질문마다 생성 + 재생성, 캐시하지 않음

    def test_generation_failure_answers_with_facts(self):
        parsed = analysis(region_names=['서울'], month='2026-08')
        a, b, c, d = self.patches(parsed, {'side_effect': RuntimeError('rate limit')})
        with a, b, c, d:
            result = ask('서울 2026년 8월 가격')
        self.assertEqual(result['analysis']['answer_check'], 'fallback')
        self.assertIn('원본 데이터로 계산한 결과입니다', result['answer'])
        self.assertIn('2026-08', result['answer'])
        self.assertTrue(result['sources'])

    def test_analysis_failure_falls_back_to_code_parsing(self):
        a, b, c, d = self.patches(None, analyze=RuntimeError('timeout'))
        with a, b, c, d:
            result = ask('서울 2026년 8월 가격')
        self.assertEqual(result['route'], 'numeric')
        self.assertEqual(result['analysis']['resolved_month'], '2026-08')
        self.assertEqual(result['analysis']['analysis_fallback'], 'RuntimeError')

    def test_many_regions_are_limited_without_error(self):
        notes = {}
        ids = [str(i) for i in range(25)]
        self.assertEqual(_limit(ids, notes), ids[:MAX_REGIONS])
        self.assertIn('25곳', notes['region_note'])
        self.assertEqual(_limit(ids[:5], {}), ids[:5])

    def test_retrieve_uses_nearest_documents_when_nothing_passes_threshold(self):
        hits, diag = retrieve(knowledge_collection(), '전혀 관계없는 질문', general_k=3, embedder=fake_embed,
                              max_distance=0.0, fallback_k=2)
        self.assertEqual([h['pool'] for h in hits], ['nearest', 'nearest'])
        self.assertTrue(diag['fallback'])
        strict, _ = retrieve(knowledge_collection(), '전혀 관계없는 질문', general_k=3, embedder=fake_embed, max_distance=0.0)
        self.assertEqual(strict, [])

    def test_context_budget_keeps_exact_documents_first(self):
        exact = [{'id': f'e{i}', 'text': 'x' * 6000} for i in range(3)]
        support = [{'id': 's', 'text': 'y' * 1000, 'pool': 'general'}]
        trimmed = _fit_budget(exact + support, budget=9000)
        self.assertEqual([c['id'] for c in trimmed], ['e0', 'e1', 'e2'])
        self.assertLessEqual(sum(len(c['text']) for c in trimmed), 9000 + 3 * 60)
        small = [{'id': 'a', 'text': 'z'}]
        self.assertIs(_fit_budget(small, budget=9000), small)

    def test_persona_and_format_reach_answer_prompt(self):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(status='completed', output_text='답변')
        context = [{'id': 'd', 'text': '근거', 'metadata': {'kind': 'guide'}}]
        with patch('gpt_api.shared_client', return_value=client):
            gpt_api.generate_answer('질문', context)
        system = client.responses.create.call_args.kwargs['input'][0]['content']
        self.assertTrue(system.startswith(gpt_api.PERSONA))
        self.assertIn(gpt_api.ANSWER_FORMAT, system)

    def test_api_calls_share_minimum_interval(self):
        clock = iter([100.0, 100.0, 100.1, 100.3])
        with patch.object(gpt_api, 'API_MIN_INTERVAL', 0.3), patch.dict(gpt_api._pace, {'last': 0.0}), \
             patch('gpt_api.time.monotonic', side_effect=lambda: next(clock)), patch('gpt_api.time.sleep') as sleep:
            gpt_api._wait_turn()
            gpt_api._wait_turn()
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.2)

    def test_time_notes_are_not_added_to_out_of_scope_answers(self):
        from bubot.rag_service import _with_notes
        off = {'answer': '범위 밖입니다.', 'analysis': {'intent': 'clarify'}}
        self.assertEqual(_with_notes(off, ['오늘 → 2026-08'])['answer'], '범위 밖입니다.')
        normal = {'answer': '답', 'analysis': {'intent': 'lookup'}}
        self.assertIn('※ 오늘 → 2026-08', _with_notes(normal, ['오늘 → 2026-08'])['answer'])

    def test_web_shows_guidance_as_answer(self):
        from bubot.web import service
        with patch('bubot.web.service.chat', side_effect=ValueError('조회할 지역명을 입력해주세요.')):
            result = service.answer({'question': '가격 알려줘', 'session_id': 'session-test-0001'})
        self.assertEqual((result['answer'], result['route']), ('조회할 지역명을 입력해주세요.', 'clarify'))


if __name__ == '__main__':
    unittest.main()
