"""일반 사용자 표현 회귀 테스트 (2026-10-12 실제 API 점검에서 발견, 실제 API 미호출).

오늘 날짜를 2026-10-12로 고정하고, 모델이 학습 시점(2023년) 기준으로 잘못 해석한 상황을 재현합니다.
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from rag_fixtures import fake_embed, knowledge_collection

from bubot.documents import read_corpus
from bubot.expressions import KST, rewrite
from bubot.rag_service import ask

TODAY = datetime(2026, 10, 12, tzinfo=KST)


def analysis(**changes):
    return {**dict(intent='lookup', region_names=['서울'], month=None, start_month=None, end_month=None,
                   display_unit=None, level='시군구', rewritten_query='질문', message=''), **changes}


class RewriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.months = read_corpus().months   # 2012-01 ~ 2026-08

    def check(self, question, expected, note=None):
        text, notes = rewrite(question, self.months, TODAY)
        self.assertEqual(text, expected, question)
        if note:
            self.assertTrue(any(note in n for n in notes), (question, notes))

    def test_relative_months_and_years(self):
        self.check('이번 달 서울 아파트 가격', '2026년 8월 서울 아파트 가격', '아직 없어')   # 2026-10 자료 없음
        self.check('지난달 서울 집값', '2026년 8월 서울 집값', '2026년 9월')
        self.check('지난달보다 서울 올랐어?', '2026년 7월부터 2026년 8월까지 서울 올랐어?')
        self.check('3년 전 강남구 가격', '2023년 8월 강남구 가격', '최신 보유 월')
        self.check('반년 전 서울 집값', '2026년 2월 서울 집값')
        self.check('두 달 전 대비 서울', '2026년 6월부터 2026년 8월까지 서울')
        self.check('세 달 전보다 부산', '2026년 5월부터 2026년 8월까지 부산')
        self.check('일 년 전 서울 가격', '2025년 8월 서울 가격')
        self.check('작년 이맘때 서울 집값', '2025년 8월 서울 집값')
        self.check('10년 사이 서울 상승률', '최근 10년 서울 상승률')
        self.check('1년 새 서울 얼마나 올랐어', '최근 1년 서울 얼마나 올랐어')

    def test_year_boundaries_and_quarters(self):
        self.check('작년 말 서울 집값', '2025년 12월 서울 집값')
        self.check('작년 연말 대비 올해 서울', '2025년 12월부터 2026년 8월까지 서울')
        self.check('올해 초 대비 서울 얼마나 올랐어', '2026년 1월부터 2026년 8월까지 서울 얼마나 올랐어')
        self.check('올해 들어 서울 얼마나 올랐어', '2026년 1월부터 2026년 8월까지 서울 얼마나 올랐어')
        self.check('올해 3월 대비 서울', '2026년 3월부터 2026년 8월까지 서울')
        self.check('2023년 연말 서울', '2023년 연말 서울')   # 연도가 있으면 기존 규칙
        self.check('지난 분기 서울 변동률', '2026년 7월부터 2026년 8월까지 서울 변동률', '2026년 8월까지만')
        self.check('어제 서울 집값', '2026년 8월 서울 집값', '주·일 단위')
        self.check('코로나 전보다 서울 얼마나 올랐어', '2019년 12월부터 2026년 8월까지 서울 얼마나 올랐어', '코로나19')
        # 두 시점을 직접 적은 비교는 그대로
        self.check('2025년 8월과 2026년 8월 서울 비교', '2025년 8월과 2026년 8월 서울 비교')

    def test_region_slang_and_area(self):
        self.check('마용성 집값', '마포구, 용산구, 성동구 집값')
        self.check('노도강 아파트 가격', '노원구, 도봉구, 강북구 아파트 가격')
        self.check('강남3구 집값 비교', '강남구, 서초구, 송파구 집값 비교')
        self.check('송도 아파트 얼마야', '인천 연수구 아파트 얼마야', '동 단위')
        self.check('강남이랑 강북 차이', '강남지역이랑 강북지역 차이', '한강 남북')
        self.check('강남구랑 강북구', '강남구랑 강북구')
        self.check('서울 평당 가격', '서울 가격', '면적 자료')
        self.check('강남 국평 얼마야', '강남 얼마야', '면적 자료')
        self.check('서울 평균 가격', '서울 평균 가격')   # '평균'은 면적 표현이 아님


class StaleModelTests(unittest.TestCase):
    """모델이 2023년을 현재로 착각하거나 되물어도 코드가 바로잡습니다."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = read_corpus()

    def run_ask(self, question, parsed=None):
        analyze = MagicMock(return_value=parsed or analysis())
        with patch('bubot.rag_service.open_index', return_value=(self.corpus, knowledge_collection())), \
             patch('bubot.expressions._today', return_value=TODAY), \
             patch('gpt_api.analyze_query', analyze), \
             patch('gpt_api.embed_texts', side_effect=fake_embed), \
             patch('gpt_api.generate_answer', return_value={'answer': '모의 답변'}):
            return ask(question), analyze

    def test_relative_period_overrides_model_year(self):
        result, _ = self.run_ask('지난달보다 서울 올랐어?', analysis(intent='trend', start_month='2023-08', end_month='2023-09'))
        a = result['analysis']
        self.assertEqual((a['resolved_start'], a['resolved_end']), ('2026-07', '2026-08'))
        result, _ = self.run_ask('3년 전 강남구 가격', analysis(region_names=['강남구'], month='2020-10'))
        self.assertEqual(result['analysis']['resolved_month'], '2023-08')
        self.assertIn("※ '3년 전' → 2023년 8월", result['answer'])

    def test_change_without_period_is_last_year(self):
        result, _ = self.run_ask('서울 집값 많이 올랐어?', analysis(intent='trend', start_month='2023-01', end_month='2023-09'))
        a = result['analysis']
        self.assertEqual((a['resolved_start'], a['resolved_end']), ('2025-08', '2026-08'))
        self.assertIn('최근 1년', result['facts'][0])

    def test_wrong_parent_path_falls_back_to_written_region(self):
        result, _ = self.run_ask('분당 집값', analysis(region_names=['경기 / 경부2권 / 성남시 / 분당구']))
        self.assertEqual(result['analysis']['resolved_region_ids'], ['64'])

    def test_needless_clarify_is_answered(self):
        result, _ = self.run_ask('세종 집값', analysis(intent='clarify', region_names=[], message='세종 데이터가 없습니다.'))
        self.assertEqual((result['route'], result['analysis']['resolved_region_ids']), ('numeric', ['170']))
        ranking, _ = self.run_ask('서울 구별 순위', analysis(intent='clarify', region_names=[], message='구체적으로'))
        self.assertEqual(ranking['analysis']['intent'], 'argmax')
        self.assertEqual(len([f for f in ranking['facts'] if '위 ' in f]), 25)   # 서울 25개 구 전체
        market, _ = self.run_ask('집값 바닥 찍었어?', analysis(intent='clarify', region_names=[], message='지역 필요'))
        self.assertEqual(market['route'], 'explain')
        self.assertEqual(market['analysis']['resolved_region_ids'], ['1'])   # 전국

    def test_out_of_scope_and_filter_skip_model(self):
        for question, words in (('전세가 얼마야', '전세·월세'), ('내년 서울 집값 오를까?', '예측'),
                                ('서울 거래량 알려줘', '거래량')):
            result, analyze = self.run_ask(question)
            analyze.assert_not_called()
            self.assertIn(words, result['answer'])
        result, analyze = self.run_ask('5억 이하 서울 구')
        analyze.assert_not_called()
        self.assertIn('0곳', result['answer'])
        self.assertIn('도봉구', result['answer'])   # 조건에 가장 가까운 곳
        result, _ = self.run_ask('10억 이상 서울 구')
        self.assertEqual(result['analysis']['intent'], 'filter')
        self.assertTrue(all(int(f.split('억')[0].split(': ')[-1]) >= 10 for f in result['facts']))
        result, analyze = self.run_ask('광교 집값')
        analyze.assert_not_called()
        self.assertIn('수원시 영통구', result['answer'])

    def test_area_question_is_not_labelled_per_pyeong(self):
        result, _ = self.run_ask('서울 평당 가격', analysis(month='2026-08'))
        self.assertNotIn('평당', result['standalone_question'])
        self.assertIn('평당·평형', result['answer'])

    def test_compare_fact_names_direction(self):
        result, _ = self.run_ask('서울이랑 부산 비교해줘', analysis(intent='compare', region_names=['서울', '부산']))
        self.assertTrue(any(f.startswith('부산 가격은 서울 가격의 0.27') for f in result['facts']))


if __name__ == '__main__':
    unittest.main()
