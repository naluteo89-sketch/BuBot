"""2026-10-08 현황판 이슈(H1~H5, M1~M3, L3, L4, 평가 실패 #7·#19) 회귀 테스트. 실제 API를 호출하지 않습니다."""
import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError
from rag_fixtures import fake_embed, knowledge_collection

from bubot.answer_check import number_issues
from bubot.coverage import explicit_years, year_coverage
from bubot.documents import read_corpus
from bubot.keyboard import keyboard_to_hangul
from bubot.knowledge import build_knowledge
from bubot.rag_service import apply_relative_period, ask
from bubot.schemas import Analysis
from bubot.year_query import last_year_answer, resolve_relative_years


def analysis(**changes):
    return {**dict(intent='lookup', region_names=['서울'], month=None, start_month=None, end_month=None,
                   display_unit=None, level='시군구', rewritten_query='질문', message=''), **changes}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = read_corpus()
        cls.latest = cls.corpus.months[-1]

    def run_ask(self, question, parsed=None, answers=('모의 답변',), **options):
        generate = MagicMock(side_effect=[{'answer': a} for a in answers])
        with patch('bubot.rag_service.open_index', return_value=(self.corpus, knowledge_collection())), \
             patch('gpt_api.analyze_query', return_value=parsed or analysis()) as analyze, \
             patch('gpt_api.embed_texts', side_effect=fake_embed), \
             patch('gpt_api.generate_answer', generate), \
             patch('bubot.year_query._this_year', return_value=2026):
            result = ask(question, **options)
        return result, analyze, generate

    def price(self, rid, month):
        return next(d.metadata['price_thousand_krw'] for d in self.corpus.documents
                    if d.id == f'region-{rid}-month-{month}')


class YearOnlyTests(Base):
    """H1: 연도만 묻고 월이 없으면 최신 월이 아니라 그 해의 마지막 보유 월을 씁니다."""

    def test_compare_with_year_only_uses_that_year(self):
        result, _, _ = self.run_ask('강남구와 서초구 2023년 데이터는 어떻게 되나요?',
                                    analysis(intent='compare', region_names=['강남구', '서초구']))
        self.assertEqual(result['analysis']['resolved_month'], '2023-12')
        self.assertIn('2023년의 마지막 보유 월(2023-12)', result['facts'][0])
        self.assertTrue(all(c['id'].endswith('2023-12') for c in result['contexts'] if c['metadata']['kind'] == 'monthly'))

    def test_year_range_from_model_used_for_lookup(self):
        result, _, _ = self.run_ask('서울 그 해 가격', analysis(start_month='2021-01', end_month='2021-12'))
        self.assertEqual(result['analysis']['resolved_month'], '2021-12')

    def test_trend_with_year_only_compares_previous_december(self):
        result, _, _ = self.run_ask('서울 2023년 추이', analysis(intent='trend'))
        self.assertEqual((result['analysis']['resolved_start'], result['analysis']['resolved_end']), ('2022-12', '2023-12'))

    def test_missing_year_is_reported(self):
        result, analyze, _ = self.run_ask('서울 2011년 가격')  # 질문에 적힌 연도는 규칙이 먼저 안내
        analyze.assert_not_called()
        self.assertIn('2011년에 해당하는 데이터는 현재 원본 CSV에 없습니다', result['answer'])
        with self.assertRaisesRegex(ValueError, '2011년 데이터는 없습니다'):  # 모델이 고른 연도가 범위 밖
            self.run_ask('서울 그 해 가격', analysis(start_month='2011-01', end_month='2011-12'))


class NowWordsTests(Base):
    """H2: '요즘·현재'는 모델이 고른 과거 월 대신 데이터 최신 월입니다."""

    def test_now_word_overrides_model_month(self):
        parsed = apply_relative_period('강남구 요즘 흐름 어때?', analysis(intent='explain', month='2023-10'), self.corpus.months)
        self.assertEqual(parsed['month'], self.latest)

    def test_now_word_shifts_range_to_latest(self):
        parsed = apply_relative_period('부산 최근 흐름', analysis(intent='trend', start_month='2023-08', end_month='2023-10'),
                                       self.corpus.months)
        self.assertEqual((parsed['start_month'], parsed['end_month']), ('2026-06', self.latest))

    def test_explicit_year_or_month_wins(self):
        parsed = apply_relative_period('2023년 현재 서울', analysis(month='2023-10'), self.corpus.months)
        self.assertEqual(parsed['month'], '2023-10')

    def test_explain_retrieves_latest_period(self):
        result, _, _ = self.run_ask('강남구 요즘 흐름 어때?', analysis(intent='explain', region_names=['강남구'], month='2023-10'))
        ids = [c['id'] for c in result['contexts']]
        self.assertIn(f'market-month-{self.latest}', ids)
        self.assertNotIn('market-month-2023-10', ids)


class RecentMonthsDefinitionTests(Base):
    """H3: 프로필의 '최근 3개월'과 수치 경로의 '최근 3개월'이 같은 기간입니다."""

    def test_profile_and_numeric_path_agree(self):
        parsed = apply_relative_period('강남구 최근 3개월 추이', analysis(intent='trend'), self.corpus.months)
        start, end = parsed['start_month'], parsed['end_month']
        rate = (self.price('38', end) / self.price('38', start) - 1) * 100
        profile = next(d.text for d in build_knowledge(self.corpus).documents if d.id == 'profile-38')
        self.assertIn(f'최근 3개월({start}~{end}, 최신 월 포함 3개월) 변화: {rate:+.2f}%', profile)
        self.assertIn('3개월 전 대비', profile)


class RulePrefilterTests(Base):
    """H4: 규칙 선처리가 정상 질문을 가로채지 않습니다."""

    def test_duration_is_not_a_year(self):
        self.assertEqual(explicit_years('서울 최근 10년 추이'), set())
        self.assertEqual(explicit_years('지난 10년간 강남구'), set())
        self.assertEqual(explicit_years('10년 전 가격'), set())
        self.assertEqual(explicit_years('그 지역들 23년 데이터'), {2023})
        self.assertEqual(explicit_years('2021년 동안 가장 많이 오른'), {2021})
        self.assertIsNone(year_coverage('서울 최근 10년 추이', self.corpus.months))

    def test_recent_ten_years_reaches_model_and_fixes_range(self):
        result, analyze, _ = self.run_ask('서울 최근 10년 추이', analysis(intent='trend'))
        analyze.assert_called_once()
        self.assertEqual((result['analysis']['resolved_start'], result['analysis']['resolved_end']), ('2016-08', self.latest))

    def test_last_year_change_questions_go_to_router(self):
        _, analyze, _ = self.run_ask('작년에 가장 많이 오른 지역은?', analysis(intent='explain', region_names=[]))
        self.assertEqual(analyze.call_args.args[0], '2025년에 가장 많이 오른 지역은?')
        result, analyze, _ = self.run_ask('서울 작년 대비 얼마나 올랐어?', analysis(intent='lookup'))
        self.assertIn('최근 1년', analyze.call_args.args[0])
        self.assertEqual((result['analysis']['resolved_start'], result['analysis']['resolved_end']), ('2025-08', self.latest))

    def test_last_year_lookup_with_province_and_district(self):
        for question, path in (('작년 서울 중구 가격', '서울'), ('작년 부산 동구 가격', '부산')):
            result = last_year_answer(question, self.corpus, '천원', today=__import__('datetime').datetime(2026, 10, 8))
            self.assertEqual(len(result['facts']), 12, question)
            self.assertTrue(all(f' {path} / ' in fact or f' {path} ' in fact for fact in result['facts']), result['facts'][0])
        ambiguous = last_year_answer('작년 동구 가격', self.corpus, '천원', today=__import__('datetime').datetime(2026, 10, 8))
        self.assertIn('시도와 시군구까지', ambiguous['answer'])

    def test_relative_year_words(self):
        self.assertEqual(resolve_relative_years('재작년과 올해', today=__import__('datetime').datetime(2026, 1, 1)),
                         '2024년과 2026년')


class AnswerCheckTests(Base):
    """H5: 답변 속 변동률을 근거와 대조합니다."""

    def test_number_issues(self):
        evidence = ['역대 최고가 대비 최신 가격 수준: -0.88%', '변동률 +1.51%']
        self.assertIn('부호 반대', number_issues('최고가 대비 +0.88% 수준', evidence)[0])
        self.assertIn('부호 반대', number_issues('1.51% 하락했습니다', evidence)[0])
        self.assertEqual(number_issues('1.51% 상승, 최고가보다 0.88% 낮은 수준, 약 1.5%', evidence), [])
        self.assertIn('근거에 없는 값', number_issues('3.20% 올랐습니다', evidence)[0])
        self.assertEqual(number_issues('5% 이상 오른 곳', ['5% 이상 오른 곳 알려줘']), [])

    def test_wrong_sign_is_regenerated_once(self):
        latest_rate = None
        profile = next(d.text for d in build_knowledge(self.corpus).documents if d.id == 'profile-38')
        latest_rate = profile.split('역대 최고가 대비 최신 가격 수준: ')[1].split('\n')[0]   # 예: -0.88%
        wrong = latest_rate.replace('-', '+') if latest_rate.startswith('-') else '-' + latest_rate.lstrip('+')
        parsed = analysis(intent='explain', region_names=['강남구'])
        result, _, generate = self.run_ask('강남구 흐름', parsed, answers=(f'최고가 대비 {wrong}', f'최고가 대비 {latest_rate}'))
        self.assertEqual(generate.call_count, 2)
        self.assertTrue(generate.call_args.kwargs['correction'])
        self.assertEqual(result['answer'], f'최고가 대비 {latest_rate}')
        self.assertEqual(result['analysis']['answer_check'], 'corrected')

    def test_still_wrong_gets_warning(self):
        result, _, generate = self.run_ask('강남구 흐름', analysis(intent='explain', region_names=['강남구']),
                                           answers=('99.99% 상승', '99.99% 상승'))
        self.assertEqual(generate.call_count, 2)
        self.assertIn('[수치 확인 필요]', result['answer'])
        self.assertEqual(result['analysis']['answer_check'], 'warning')


class RetrievalAndSchemaTests(Base):
    def test_explain_general_pool_excludes_other_periods(self):
        """M1: 지역·기간이 정해진 서술 질문은 다른 월 시장 요약을 의미 검색으로 끌어오지 않습니다."""
        result, _, _ = self.run_ask('강남구 흐름', analysis(intent='explain', region_names=['강남구']), top_k=7)
        general = [c for c in result['contexts'] if c.get('pool') == 'general']
        self.assertTrue(all(c['metadata']['kind'] == 'guide' for c in general))
        self.assertFalse(any(c['metadata']['kind'] == 'market_monthly' for c in result['contexts']))

    def test_top_k_controls_general_pool(self):
        """L3: --top-k가 서술형 의미 검색 개수에 적용됩니다."""
        with patch('bubot.rag_service.retrieve', return_value=([], {'used': [], 'rejected': []})) as retrieve:
            self.run_ask('변동률 계산 방법', analysis(intent='explain', region_names=[]), top_k=7)
        self.assertEqual(retrieve.call_args.kwargs['general_k'], 7)

    def test_month_format_validated(self):
        """M2: 월은 YYYY-MM만 허용합니다 (OpenAI 스키마에도 pattern으로 전달)."""
        for bad in ('2023', '2023-13', '23-01'):
            with self.assertRaises(ValidationError):
                Analysis.model_validate(analysis(month=bad))
        Analysis.model_validate(analysis(month='2023-05'))
        self.assertIn('pattern', str(Analysis.model_json_schema()['properties']['month']))

    def test_faq_mentions_memory(self):
        """M3: FAQ가 대화 메모리 기능과 모순되지 않습니다."""
        guide = ' '.join(d.text for d in build_knowledge(self.corpus).documents if d.metadata['kind'] == 'guide')
        self.assertNotIn('각 질문은 독립적으로 처리됩니다', guide)
        self.assertIn('이어서 물어볼 수 있습니다', guide)


class RegionNarrowingTests(Base):
    """평가 실패 #7·#19: 모델이 상위 지역까지 나눠 넣은 경우."""

    def test_lookup_keeps_most_specific_region(self):
        result, _, _ = self.run_ask('서울 중구 2026년 8월 가격',
                                    analysis(region_names=['서울 중구', '서울', '강북지역'], month='2026-08'))
        self.assertEqual(result['analysis']['intent'], 'lookup')
        self.assertEqual(len(result['analysis']['resolved_region_ids']), 1)
        self.assertTrue(result['contexts'][0]['metadata']['region_path'].endswith('중구'))

    def test_rank_uses_widest_scope(self):
        result, _, _ = self.run_ask('2026년 8월 서울의 구 중 가장 비싼 곳과 가격',
                                    analysis(intent='argmax', region_names=['서울', '강남지역'], month='2026-08'))
        self.assertEqual(result['analysis']['aggregation']['candidates_count'], 25)

    def test_rank_scope_from_listed_subregions(self):
        """실제 GPT 응답(서울 권역만 4~7개 나열)에서도 '서울' 전체 25개 구를 비교합니다."""
        names = ['서울', '서울 / 강북지역', '서울 / 강남지역', '서울 / 강북지역 / 도심권', '서울 / 강북지역 / 동북권',
                 '서울 / 강남지역 / 서남권', '서울 / 강남지역 / 동남권']
        for listed in (names, names[3:]):
            result, _, _ = self.run_ask('2026년 8월 서울의 구 중 가장 싼 곳과 가격',
                                        analysis(intent='argmin', region_names=listed, month='2026-08'))
            self.assertEqual(result['analysis']['aggregation']['candidates_count'], 25)

    def test_written_alias_beats_region_group(self):
        """평가 #11·#31 실제 GPT 응답: '강남'을 권역 강남지역으로, 질문에 없는 '서울'까지 넣은 경우."""
        result, _, _ = self.run_ask('강남과 서초 2026년 8월 각각 가격과 차이',
                                    analysis(intent='compare', region_names=['서울', '서울 / 강남지역', '서울 / 서초구'],
                                             month='2026-08'))
        self.assertEqual(result['analysis']['resolved_region_ids'], ['38', '37'])

    def test_rank_listing_every_province_means_nationwide(self):
        """평가 #21 실제 GPT 응답: '시도 중 가장 비싼 곳'에 시도를 전부 나열."""
        from bubot.regions import region_catalog
        provinces = [r['label'] for r in region_catalog(self.corpus).values() if r['level'] == '시도']
        result, _, _ = self.run_ask('2026년 8월 시도 중 가장 비싼 곳과 가격',
                                    analysis(intent='argmax', region_names=provinces, month='2026-08', level='시도'))
        self.assertEqual(result['analysis']['aggregation']['candidates_count'], 17)
        self.assertEqual(result['contexts'][0]['metadata']['region_name'], '서울')
        result, _, _ = self.run_ask('2026년 8월 시도 중 가장 비싼 곳과 가격',  # 같은 질문의 다른 실제 응답: ['시도']
                                    analysis(intent='argmax', region_names=['시도'], month='2026-08', level='시도'))
        self.assertEqual(result['analysis']['aggregation']['candidates_count'], 17)

    def test_parent_only_name_prefers_province(self):
        """평가 #4: '제주'는 시도 제주로 해석하고, '제주시'와 함께 오면 제주시 조회입니다."""
        result, _, _ = self.run_ask('제주시 2026년 8월 평균 가격', analysis(region_names=['제주', '제주시'], month='2026-08'))
        self.assertTrue(result['contexts'][0]['metadata']['region_path'].endswith('제주시'))
        self.assertEqual(result['analysis']['intent'], 'lookup')

    def test_guessed_parent_becomes_clarification(self):
        """평가 #26: 질문에 시도가 없는 '중구'를 모델이 서울 중구로 추측하면 후보를 보여 주고 되묻습니다."""
        result, _, generate = self.run_ask('중구 2026년 8월 가격',
                                           analysis(region_names=['서울 / 강북지역 / 도심권 / 중구'], month='2026-08'))
        self.assertEqual(result['analysis']['intent'], 'clarify')
        self.assertIn('부산', result['answer'])
        generate.assert_not_called()

    def test_gwangju_district_is_metropolitan_city(self):
        """평가 #14: '광주 동구'는 광주광역시입니다 (경기 광주시에는 구가 없음)."""
        result, analyze, _ = self.run_ask('서울 중구와 광주 동구 2026년 8월 각각 가격과 차이',
                                          analysis(intent='compare', region_names=['서울 중구', '광주 동구'], month='2026-08'))
        analyze.assert_called_once()
        self.assertEqual(len(result['analysis']['resolved_region_ids']), 2)

    def test_invented_month_ignored(self):
        """평가 #31: 질문에 월이 없는데 모델이 2023-08을 고르면 연도 기준(2023-12)으로 바꿉니다."""
        result, _, _ = self.run_ask('강남과 서초 2023년 각각 가격과 차이',
                                    analysis(intent='compare', region_names=['강남구', '서초구'], month='2023-08'))
        self.assertEqual(result['analysis']['resolved_month'], '2023-12')
        result, _, _ = self.run_ask('강남구 2023년 8월 가격', analysis(region_names=['강남구'], month='2023-08'))
        self.assertEqual(result['analysis']['resolved_month'], '2023-08')

    def test_explicit_comparison_with_parent_is_kept(self):
        result, _, _ = self.run_ask('강남구와 서울 2026년 8월 가격 비교',
                                    analysis(intent='compare', region_names=['강남구', '서울'], month='2026-08'))
        self.assertEqual(len(result['analysis']['resolved_region_ids']), 2)


class ModelOutputVarianceTests(Base):
    """같은 질문에 대한 실제 GPT 분석 결과의 변형들 (평가 #5, #13, #21, #22)."""

    def test_province_suffix_and_particles(self):
        result, _, _ = self.run_ask('서귀포시 2026년 1월 평균매매가격',
                                    analysis(region_names=['제주도', '서귀포시'], month='2026-01'))
        self.assertTrue(result['contexts'][0]['metadata']['region_path'].endswith('서귀포시'))
        result, _, _ = self.run_ask('제주시와 서귀포시 2026년 8월 각각 가격과 차이',
                                    analysis(intent='compare', region_names=['제주 / 제주시는', '제주 / 서귀포시'], month='2026-08'))
        self.assertEqual(len(result['analysis']['resolved_region_ids']), 2)

    def test_unresolvable_extra_name_is_skipped(self):
        """평가 #5 실제 응답: 정상 이름과 함께 해석 불가 이름('제주 / 제주 시')을 덧붙인 경우."""
        result, _, _ = self.run_ask('서귀포시 2026년 1월 평균매매가격',
                                    analysis(region_names=['서귀포시', '제주 / 제주 시'], month='2026-01'))
        self.assertTrue(result['contexts'][0]['metadata']['region_path'].endswith('서귀포시'))
        with self.assertRaises(ValueError):  # 질문에 적힌 없는 지역은 그대로 오류
            self.run_ask('없는시 가격', analysis(region_names=['없는시']))

    def test_unwritten_regions_added_by_model_are_dropped(self):
        """평가 #8 실제 응답: '광주광역시 동구'에 전국·전남광주·광주까지 붙여 비교로 처리."""
        result, _, _ = self.run_ask('광주광역시 동구 2026년 8월 가격',
                                    analysis(intent='compare', month='2026-08',
                                             region_names=['전국', '전남광주', '전남광주 / 광주', '전남광주 / 광주 / 동구']))
        self.assertEqual(result['analysis']['intent'], 'lookup')
        self.assertEqual(result['analysis']['resolved_region_ids'], ['50'])

    def test_rank_ignores_listed_candidates_and_dates(self):
        listed = ['서울', '부산', '대구', '인천', '광주', '대전', '울산', '세종', '경기']
        for names in (listed, ['2026년 8월'], ['2026년 8월', '시도']):
            result, _, _ = self.run_ask('2026년 8월 시도 중 가장 싼 곳과 가격',
                                        analysis(intent='argmin', region_names=names, month='2026-08', level='시도'))
            self.assertEqual(result['analysis']['aggregation']['candidates_count'], 17, names)


class KeyboardTests(Base):
    """L4: 한/영 전환 없이 친 질문."""

    def test_conversion(self):
        self.assertEqual(keyboard_to_hangul('tjdnfrhk qntksdml 26sus epdlxj'), '서울과 부산의 26년 데이터')
        self.assertEqual(keyboard_to_hangul('dkvkxm rkrur'), '아파트 가격')
        for text in ('seoul price', 'GPT', '서울 가격', 'a', '2026-08'):
            self.assertIsNone(keyboard_to_hangul(text), text)

    def test_ask_uses_converted_question(self):
        result, analyze, _ = self.run_ask('tjdnf 2026sus 8dnjf rkrur', analysis(month='2026-08'))
        self.assertEqual(analyze.call_args.args[0], '서울 2026년 8월 가격')
        self.assertTrue(result['input_converted'])
        self.assertEqual(result['standalone_question'], '서울 2026년 8월 가격')


if __name__ == '__main__':
    unittest.main()
