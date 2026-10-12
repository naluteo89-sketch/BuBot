import csv
import unittest
from decimal import Decimal
from unittest.mock import patch
from bubot.config import CSV_PATH
from bubot.data_cache import load_corpus
from bubot.period_queries import aggregate_query, negative_concept, period
from bubot.conversation import chat,ConversationMemory

class PeriodQueriesTests(unittest.TestCase):
    def test_concept_is_narrow(self):
        self.assertTrue(negative_concept('변동율이 음수일수있어?'))
        self.assertFalse(negative_concept('4년간 데이터 중 변동율이 음수로 가는 지역'))
        self.assertFalse(negative_concept('서울 변동률은 음수인가요?'))

    def test_seoul_mean_from_raw_csv(self):
        with CSV_PATH.open(encoding='cp949',newline='') as f:rows=list(csv.reader(f))
        row=next(r for r in rows[3:] if r[4]=='서울')
        for year in (2017,2025):
            values=[Decimal(row[i].replace(',','').replace('"','')) for i,m in enumerate(rows[0]) if m.startswith(str(year)+'년')]
            r=aggregate_query(f'서울 {year}년 평균 매매가격',load_corpus(),'천원')
            self.assertEqual(Decimal(r['analysis']['mean_thousand']),sum(values)/12)
            self.assertNotEqual(Decimal(r['analysis']['mean_thousand']),values[-1])
            self.assertEqual(len(r['contexts']),12)

    def test_negative_population_independently(self):
        with CSV_PATH.open(encoding='cp949',newline='') as f:rows=list(csv.reader(f))
        a,b=rows[0].index('2022년 8월'),rows[0].index('2026년 8월')
        negatives=0;valid=0;missing=0
        for row in rows[3:]:
            if not row[4].endswith(('시','군','구')) or (row[4].startswith('(구)') or row[4].endswith('광역시') or row[4] in {'서울','부산','대구','인천','광주','대전','울산','세종','경기','강원','충북','충남','전북','전남','경북','경남','제주'}):continue
            x,y=[row[i].replace(',','').replace('"','').strip() for i in (a,b)]
            if not x or not y or Decimal(x)<=0:missing+=1;continue
            valid+=1;negatives+=Decimal(y)<Decimal(x)
        r=aggregate_query('4년간 데이터 중 변동율이 음수로 가는 지역',load_corpus(),'천원')
        self.assertEqual((r['analysis']['count'],r['analysis']['candidates'],r['analysis']['excluded']),(negatives,valid,missing))
        self.assertEqual(period('4년간 데이터',load_corpus().months),('2022-08','2026-08'))

    def test_followup_keeps_whole_list(self):
        memory=ConversationMemory()
        r=chat('period-test-list','4년간 데이터 중 변동율이 음수로 가는 지역',memory=memory)
        with patch('gpt_api.condense_question') as model:
            second=chat('period-test-list','그 지역은?',memory=memory)
        model.assert_not_called()
        self.assertEqual(second['facts'],r['facts'])
        self.assertGreater(len(second['facts']),6)

    def test_correction_recomputes_annual_mean(self):
        memory=ConversationMemory()
        chat('period-test-mean','서울 2017년 평균 매매가',memory=memory)
        r=chat('period-test-mean','그럼 왜 12월 기준 데이터를 준거야?',memory=memory)
        self.assertEqual(r['analysis']['valid_months'],12)
        self.assertIn('잘못된 처리',r['answer'])

    def test_partial_year_not_claimed_complete(self):
        r=aggregate_query('서울 2026년 평균 가격',load_corpus(),'천원')
        self.assertEqual(r['analysis']['valid_months'],8)
        self.assertIn('일부 월이 없어',r['answer'])
