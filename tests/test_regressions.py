import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import gpt_api
from bubot.config import CSV_PATH, BASE_DIR
from bubot.documents import read_corpus, Document, Corpus
from bubot.pricing import format_price
from bubot.regions import region_catalog, resolve_region
from bubot.retrieval import argmin_price, argmax_price
from bubot.rag_service import ask
from bubot.schemas import Analysis


def analysis(**changes):
    return {**dict(intent='lookup',region_names=['서울'],month=None,start_month=None,
        end_month=None,display_unit=None,level='시군구',rewritten_query='서울 가격',message=''),**changes}


class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus=read_corpus()
        cls.catalog=region_catalog(cls.corpus)

    def test_default_csv_and_working_directory(self):
        self.assertEqual(CSV_PATH,BASE_DIR/'data'/'new_prices.csv')
        self.assertEqual(self.corpus.region_count,245)
        with tempfile.TemporaryDirectory() as directory:
            code=f"import sys; sys.path.insert(0,{str(BASE_DIR)!r}); from bubot.documents import read_corpus; assert read_corpus().region_count==245"
            result=subprocess.run([sys.executable,'-c',code],cwd=directory,capture_output=True)
            self.assertEqual(result.returncode,0)

    def test_prices_retain_small_remainders_and_zero(self):
        for value,unit,expected in [(1761071,'korean','17억 6,107만 1,000원'),
            (493881,'만원','49,388.1만원'),(493881,'억원','4.93881억원'),
            (0,'korean','0원'),(100000,'korean','1억원'),(-1,'korean','-1,000원'),
            (None,'korean','데이터 없음'),(1.5,'천원','1.5천원')]:
            with self.subTest(value=value,unit=unit):
                self.assertEqual(format_price(value,unit),expected)

    def test_region_alias_and_ambiguity(self):
        self.assertEqual(resolve_region('서울특별시 중구',self.catalog),'12')
        self.assertEqual(resolve_region('강남',self.catalog),'38')
        with self.assertRaises(ValueError): resolve_region('동구',self.catalog)
        with self.assertRaises(ValueError): resolve_region('없는시',self.catalog)

    def test_rank_full_population_excludes_missing_and_other_levels(self):
        docs=[]
        for i in range(65):
            price=None if i==0 else i
            meta={'region_id':str(i),'region_name':f'테스트{i}구','region_path':f'서울 / 테스트{i}구',
                'region_1':'서울','region_2':f'테스트{i}구','region_3':f'테스트{i}구','region_4':f'테스트{i}구',
                'month':'2026-08','kind':'monthly','missing':price is None}
            if price is not None: meta['price_thousand_krw']=price
            docs.append(Document(str(i),'price',meta))
        fixture=Corpus(docs,'hash',65,('2026-08',),1)
        with patch('bubot.retrieval.load_corpus',return_value=fixture):
            high=argmax_price({'month':'2026-08'})
            low=argmin_price({'month':'2026-08'})
        self.assertEqual(high['winner']['id'],'64')
        self.assertEqual(low['winner']['id'],'1')
        self.assertEqual(high['candidates_count'],64)

    def test_real_rank_scope(self):
        result=argmax_price({'region_name':'서울','month':'2026-08'})
        self.assertEqual(result['candidates_count'],25)
        self.assertEqual(result['winner']['metadata']['region_id'],'38')

    def run_ask(self, parsed, **options):
        from rag_fixtures import knowledge_collection, fake_embed
        with patch('bubot.rag_service.open_index',return_value=(self.corpus,knowledge_collection())), \
             patch('gpt_api.analyze_query',return_value=parsed), \
             patch('gpt_api.embed_texts',side_effect=fake_embed), \
             patch('gpt_api.generate_answer',return_value={'answer':'모의 답변'}) as generate:
            result=ask('검증 질문',**options)
        return result,generate

    def test_compare_has_both_regions_computed_values_and_sources(self):
        result,_=self.run_ask(analysis(intent='compare',region_names=['강남','서초']))
        prices=[c for c in result['contexts'] if c['metadata']['kind']=='monthly']
        self.assertEqual(len(prices),2)
        self.assertEqual(len(result['sources']),len(result['contexts']))
        self.assertTrue(any('차이:' in fact for fact in result['facts']))
        self.assertTrue(all('SHA-256' in source for source in result['sources']))

    def test_trend_range_filters_context_and_uses_cli_unit(self):
        result,_=self.run_ask(analysis(intent='trend',start_month='2026-06',end_month='2026-08',display_unit='억원'),unit='천원')
        self.assertEqual(result['analysis']['display_unit'],'천원')
        self.assertNotIn('2025-11:',result['contexts'][0]['text'])
        self.assertEqual(result['contexts'][0]['metadata']['kind'],'timeline')
        self.assertTrue(any('변동률' in fact for fact in result['facts']))

    def test_missing_and_invalid_periods_do_not_get_invented(self):
        result,_=self.run_ask(analysis(region_names=['경기 부천시 (구)원미구']))
        self.assertTrue(any('데이터 없음' in fact for fact in result['facts']))
        with self.assertRaises(ValueError): self.run_ask(analysis(month='2027-01'))
        ambiguous,generate=self.run_ask(analysis(region_names=['동구']))
        self.assertEqual(ambiguous['analysis']['intent'],'clarify')
        self.assertIn('대구 / 동구',ambiguous['answer'])
        generate.assert_not_called()
        with self.assertRaises(ValueError): self.run_ask(analysis(region_names=['없는시']))

    def test_clarification_skips_generation(self):
        result,generate=self.run_ask(analysis(intent='clarify',region_names=[],message='지역을 지정하세요.'))
        self.assertEqual(result['answer'],'지역을 지정하세요.')
        generate.assert_not_called()

    def test_stale_index_checked_before_api(self):
        with patch('bubot.rag_service.validate_knowledge_index',side_effect=ValueError('stale')), patch('gpt_api.analyze_query') as api:
            with self.assertRaises(ValueError): ask('서울 가격')
            api.assert_not_called()

    def test_external_key_file_optional_and_diagnostics_do_not_reveal_key(self):
        with tempfile.TemporaryDirectory() as directory:
            keyfile=Path(directory)/'gpt_key.py'
            keyfile.write_text('open_Aikey="not-a-real-key"',encoding='utf-8')
            with patch.object(gpt_api,'KEY_FILE',keyfile),patch.object(gpt_api,'API_KEY',''),patch.dict('os.environ',{},clear=True):
                self.assertEqual(gpt_api.resolve_api_key()[0],'not-a-real-key')
                self.assertNotIn('not-a-real-key',json.dumps(gpt_api.check_configuration()))

    def test_sdk_routes_analysis_and_context_to_real_api_methods(self):
        client=MagicMock()
        client.__enter__.return_value=client
        client.responses.parse.return_value=SimpleNamespace(status='completed',output_parsed=Analysis(**analysis(message='불필요한 설명')))
        client.responses.create.return_value=SimpleNamespace(status='completed',output_text='답변')
        doc=self.corpus.documents[0]
        with patch('gpt_api.create_client',return_value=client):
            parsed=gpt_api.analyze_query('서울',self.catalog,self.corpus.months)
            result=gpt_api.generate_answer('서울',[{'id':doc.id,'text':doc.text,'metadata':doc.metadata}],parsed,'korean',['확정값'])
        self.assertEqual(result['answer'],'답변')
        self.assertEqual(parsed['message'],'')
        payload=json.loads(client.responses.create.call_args.kwargs['input'][1]['content'])
        self.assertEqual(payload['verified_facts'],['확정값'])
        self.assertEqual(payload['contexts'][0]['document'],doc.text)


if __name__=='__main__':
    unittest.main()
