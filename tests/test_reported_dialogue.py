import unittest
from unittest.mock import patch
from bubot.conversation import chat, ConversationMemory
from bubot.rag_service import ask
from bubot.data_cache import load_corpus
from bubot.regions import region_catalog, ranking_regions

class ReportedDialogueTests(unittest.TestCase):
    def test_concept_does_not_inherit_positive_rate(self):
        with patch('gpt_api.condense_question') as condense:
            result=ask('변동율이 음수일수있어?',history=[{'answer':'+0.60%'}])
        condense.assert_not_called()
        self.assertIn('-10%',result['answer'])
        self.assertIn('음수가 됩니다',result['answer'])

    def test_annual_minimum_and_followup(self):
        memory=ConversationMemory()
        result=chat('regression-2020', '20년도 최저가 거래 지역',memory=memory)
        corpus=load_corpus();ids=ranking_regions(region_catalog(corpus),level='시군구')
        candidates=[d for d in corpus.documents if d.metadata['kind']=='monthly' and d.metadata['month'].startswith('2020-') and d.metadata['region_id'] in ids and d.metadata.get('price_thousand_krw') is not None]
        expected=min(candidates,key=lambda d:d.metadata['price_thousand_krw'])
        self.assertEqual(result['contexts'][0]['id'],expected.id)
        self.assertIn('태백시',result['answer'])
        self.assertIn('7,793만 8,000원',result['answer'])
        for q in ['거래금이 얼마인데?', '아니 그 지역 거래 금액']:
            reply=chat('regression-2020',q,memory=memory)
            self.assertEqual(reply['facts'],result['facts'])
            self.assertEqual(reply['sources'],result['sources'])
