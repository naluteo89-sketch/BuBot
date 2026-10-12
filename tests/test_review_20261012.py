"""2026-10-12 코드 재검토에서 발견한 오류의 회귀 테스트 (실제 API 미호출)."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rag_fixtures import NOT_LOCATED, fake_embed, knowledge_collection

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


class WholePeriodMeanTests(unittest.TestCase):
    """10/12 테스트: '지금까지 강남구의 평균 매매가격'이 최신 월 가격 하나로 답하던 문제."""
    @classmethod
    def setUpClass(cls):
        cls.corpus = read_corpus()

    def bounds(self, question):
        result = aggregate_query(question, self.corpus, '천원')
        return result and (result['analysis']['intent'], result['analysis']['resolved_start'], result['analysis']['resolved_end'])

    def test_until_now_means_whole_period(self):
        first, latest = self.corpus.months[0], self.corpus.months[-1]
        for question in ('지금까지 강남구의 평균 매매가격', '강남구 역대 평균 가격', '처음부터 지금까지 부산 평균'):
            self.assertEqual(self.bounds(question), ('period_mean', first, latest), question)
        result = aggregate_query('지금까지 강남구의 평균 매매가격', self.corpus, '천원')
        self.assertEqual(result['analysis']['valid_months'], len(self.corpus.months))

    def test_from_year_until_now(self):
        self.assertEqual(self.bounds('2020년부터 지금까지 서울 평균 가격'), ('period_mean', '2020-01', self.corpus.months[-1]))

    def test_self_contained_question_skips_condense(self):
        # 테스트(10/12): '지금까지 강남구 평균' 다음 '서울 2026년 8월 가격'이 '서울 … 강남구'로 합쳐짐
        from bubot.rag_service import _self_contained
        for question in ('서울 2026년 8월 가격', '부산 최근 3개월 추이', '강남구 작년 12월 가격'):
            self.assertTrue(_self_contained(question), question)
        for question in ('2020년 기준으로', '대구는?', '서초구랑 비교하면?', '지금까지 평균은?', '그 지역 2025년 가격',
                         '중구 2025년 가격'):
            self.assertFalse(_self_contained(question), question)
        history = [{'question': '지금까지 강남구의 평균 매매가격', 'standalone_question': '지금까지 강남구의 평균 매매가격',
                    'answer': '..', 'resolved': {'intent': 'period_mean'}, 'clarification': False, 'error': False}]
        with patch('bubot.rag_service.open_index', return_value=(self.corpus, knowledge_collection())), \
             patch('gpt_api.condense_question') as condense, \
             patch('gpt_api.analyze_query', return_value=analysis(region_names=['서울'], month='2026-08')), \
             patch('gpt_api.embed_texts', side_effect=fake_embed), \
             patch('gpt_api.generate_answer', return_value={'answer': '모의 답변'}):
            result = ask('서울 2026년 8월 가격', history=history)
        condense.assert_not_called()
        self.assertEqual((result['standalone_question'], result['followup']), ('서울 2026년 8월 가격', False))

    def test_other_questions_keep_their_route(self):
        self.assertIsNone(aggregate_query('지금까지 강남구 가격 추이', self.corpus, '천원'))
        self.assertIsNone(aggregate_query('강남구 역대 최고가', self.corpus, '천원'))
        self.assertEqual(self.bounds('서울 최근 3년 평균 가격')[1:], ('2023-09', self.corpus.months[-1]))


class PlaceLocationTests(unittest.TestCase):
    """10/12 테스트: '5년전 챔피언스 필드 근처 집값'이 지역을 못 찾아 되묻기만 하던 문제 (실제 API 미호출)."""
    @classmethod
    def setUpClass(cls):
        cls.corpus = read_corpus()

    def run_place(self, question, located, parsed=None):
        with patch('bubot.rag_service.open_index', return_value=(self.corpus, knowledge_collection())), \
             patch('gpt_api.locate_place', return_value=located) as locate, \
             patch('gpt_api.analyze_query', side_effect=lambda q, *_: parsed or analysis(
                 region_names=['광주 북구'] if '광주 북구' in q else [], month='2021-08',
                 intent='lookup' if '광주 북구' in q else 'clarify', message='' if '광주 북구' in q else '지역?')) as analyze, \
             patch('gpt_api.embed_texts', side_effect=fake_embed), \
             patch('gpt_api.generate_answer', return_value={'answer': '모의 답변'}):
            ANSWER_CACHE.clear()
            return ask(question), locate, analyze

    def test_landmark_is_located_before_analysis(self):
        found = {'found': True, 'place': '챔피언스 필드', 'address': '광주광역시 북구', 'candidates': []}
        result, locate, analyze = self.run_place('5년전 챔피언스 필드 근처 집값', found)
        locate.assert_called_once()
        self.assertIn('광주 북구', analyze.call_args.args[0])
        self.assertNotIn('챔피언스', analyze.call_args.args[0])
        self.assertEqual(result['analysis']['resolved_month'], '2021-08')
        self.assertIn('전남광주 / 광주 / 북구', result['facts'][0])
        self.assertIn("'챔피언스 필드'은(는) 일반적으로 알려진 소재지(광주광역시 북구)", result['answer'])

    def test_unknown_place_asks_for_district_instead_of_guessing(self):
        result, _, analyze = self.run_place('우리집 근처 집값', {'found': False, 'place': '우리집', 'address': '', 'candidates': []})
        analyze.assert_not_called()  # 모델이 전국 등 다른 지역으로 대신 답하지 않도록 분석 전에 되묻기
        self.assertEqual(result['analysis']['reason'], 'unknown_place')
        self.assertIn('시군구 이름으로', result['answer'])

    def test_place_spanning_districts_lists_candidates(self):
        located = {'found': False, 'place': '한강공원', 'address': '',
                   'candidates': ['서울특별시 영등포구', '서울특별시 마포구', '없는시 없는구']}
        result, _, _ = self.run_place('한강공원 근처 집값', located)
        self.assertEqual(result['analysis']['reason'], 'ambiguous_place')
        self.assertIn('영등포구', result['answer'])
        self.assertIn('마포구', result['answer'])
        self.assertNotIn('없는시', result['answer'])  # 지역 목록에 없는 주소는 후보에서 제외

    def test_invalid_address_from_model_is_not_used(self):
        located = {'found': True, 'place': '챔피언스 필드', 'address': '메릴랜드주 볼티모어', 'candidates': []}
        result, _, _ = self.run_place('챔피언스 필드 근처 집값', located)
        self.assertEqual(result['analysis']['reason'], 'unknown_place')

    def test_questions_with_written_region_never_call_locate(self):
        for question in ('서울 2026년 8월 가격', '전국에서 가장 싼 지역', '요즘 집값 어때'):
            result, locate, _ = self.run_place(question, NOT_LOCATED, analysis(intent='explain', region_names=['전국']))
            locate.assert_not_called()


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

    def test_one_month_trend_becomes_comparison(self):
        # 테스트 기록(16:2x): '작년 12월은?' 후속 → trend 2025-12~2025-12로 분석되어 '변동률 +0.00%'가 근거로 쓰임
        parsed = analysis(intent='trend', region_names=['강남구', '서초구'], start_month='2025-12', end_month='2025-12')
        a, b, c, d = self.patches(parsed)
        with a, b, c, d:
            result = ask('2025년 12월 강남구와 서초구 가격 비교')
        self.assertEqual((result['analysis']['intent'], result['analysis']['resolved_month']), ('compare', '2025-12'))
        self.assertFalse(any('변동률 +0.00%' in f for f in result['facts']))
        self.assertTrue(all('2025-12' in f for f in result['facts'][:2]))

    def test_short_followup_rule_in_condense_prompt(self):
        client = MagicMock()
        client.responses.parse.return_value = SimpleNamespace(status='incomplete', output_parsed=None)
        with patch('gpt_api.shared_client', return_value=client):
            gpt_api.condense_question('2020년 기준으로', [{'question': '서울 상위권 10개 지역 시세들 알려줘'}])
        system = client.responses.parse.call_args.kwargs['input'][0]['content']
        self.assertIn("'2020년 기준 서울 상위권 10개 지역 시세들 알려줘'", system)

    def test_web_shows_guidance_as_answer(self):
        from bubot.web import service
        with patch('bubot.web.service.chat', side_effect=ValueError('조회할 지역명을 입력해주세요.')):
            result = service.answer({'question': '가격 알려줘', 'session_id': 'session-test-0001'})
        self.assertEqual((result['answer'], result['route']), ('조회할 지역명을 입력해주세요.', 'clarify'))


if __name__ == '__main__':
    unittest.main()
