import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rag_fixtures import fake_embed, knowledge_collection

import gpt_api
from bubot.conversation import ConversationMemory, chat, summarize_turn
from bubot.documents import read_corpus
from bubot.schemas import Condensed

SID = 'session-test-0001'


def analysis(**changes):
    return {**dict(intent='lookup', region_names=['서울'], month=None, start_month=None, end_month=None,
                   display_unit=None, level='시군구', rewritten_query='질문', message=''), **changes}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class MemoryStoreTests(unittest.TestCase):
    def test_keeps_at_least_three_recent_turns(self):
        memory = ConversationMemory(turns=3)
        for i in range(5):
            memory.add(SID, {'question': f'q{i}'})
        self.assertEqual([t['question'] for t in memory.history(SID)], ['q2', 'q3', 'q4'])
        with self.assertRaises(ValueError):
            ConversationMemory(turns=2)

    def test_configurable_more_turns(self):
        memory = ConversationMemory(turns=5)
        for i in range(6):
            memory.add(SID, {'question': f'q{i}'})
        self.assertEqual(len(memory.history(SID)), 5)

    def test_sessions_isolated_reset_and_ttl(self):
        clock = Clock()
        memory = ConversationMemory(turns=3, ttl=100, clock=clock)
        memory.add(SID, {'question': 'a'})
        memory.add('session-test-0002', {'question': 'b'})
        self.assertEqual(memory.history(SID)[0]['question'], 'a')
        memory.reset(SID)
        self.assertEqual(memory.history(SID), [])
        clock.now = 101
        self.assertEqual(memory.history('session-test-0002'), [])

    def test_session_limit_and_validation(self):
        memory = ConversationMemory(turns=3, max_sessions=2)
        for i in range(3):
            memory.add(f'session-test-000{i}', {'question': str(i)})
        self.assertEqual(memory.history('session-test-0000'), [])
        for bad in (None, 'short', '../../etc', 'a' * 65):
            with self.assertRaises(ValueError):
                memory.history(bad)

    def test_turn_summary_keeps_resolved_conditions_not_full_contexts(self):
        result = {'answer': '가' * 2000, 'facts': ['f'] * 10, 'standalone_question': '서울 2026년 8월 가격',
                  'analysis': {'intent': 'lookup', 'resolved_month': '2026-08'},
                  'contexts': [{'metadata': {'kind': 'monthly', 'region_path': '서울'}, 'text': '긴 본문'}]}
        turn = summarize_turn('서울 얼마', result)
        self.assertEqual(turn['resolved'], {'intent': 'lookup', 'resolved_month': '2026-08', 'regions': ['서울']})
        self.assertLess(len(turn['answer']), 800)
        self.assertEqual(len(turn['facts']), 6)
        self.assertNotIn('contexts', turn)


class ChatFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = read_corpus()

    def run_chat(self, memory, question, parsed=None, condensed=None):
        condense = MagicMock(return_value=condensed or {'is_followup': False, 'standalone_question': question})
        with patch('bubot.rag_service.open_index', return_value=(self.corpus, knowledge_collection())), \
             patch('gpt_api.analyze_query', return_value=parsed or analysis()) as analyze, \
             patch('gpt_api.embed_texts', side_effect=fake_embed), \
             patch('gpt_api.condense_question', condense), \
             patch('gpt_api.generate_answer', return_value={'answer': f'{question} 답변'}):
            result = chat(SID, question, memory=memory)
        return result, condense, analyze

    def test_first_question_skips_condense_and_followups_get_three_turns(self):
        memory = ConversationMemory(turns=3)
        _, condense, _ = self.run_chat(memory, '서울 2026년 8월 가격')
        condense.assert_not_called()
        for q in ('부산은?', '대구는?', '인천은?'):
            result, condense, _ = self.run_chat(memory, q)
        history = condense.call_args.args[1]
        self.assertEqual([t['question'] for t in history], ['서울 2026년 8월 가격', '부산은?', '대구는?'])
        self.assertEqual(result['memory_turns'], 3)
        self.assertEqual(result['memory_used'], 3)

    def test_history_window_contents(self):
        memory = ConversationMemory(turns=3)
        for q in ('q1 서울', 'q2 부산', 'q3 대구', 'q4 인천'):
            _, condense, _ = self.run_chat(memory, q)
        self.assertEqual([t['question'] for t in condense.call_args.args[1]], ['q1 서울', 'q2 부산', 'q3 대구'])
        self.assertEqual([t['question'] for t in memory.history(SID)], ['q2 부산', 'q3 대구', 'q4 인천'])
        self.assertTrue(all(t['answer'] for t in memory.history(SID)))

    def test_followup_question_is_rewritten_before_routing(self):
        memory = ConversationMemory(turns=3)
        self.run_chat(memory, '강남구 2026년 8월 가격', analysis(region_names=['강남구']))
        result, condense, analyze = self.run_chat(
            memory, '서초는?', analysis(region_names=['서초구']),
            {'is_followup': True, 'standalone_question': '서초구 2026년 8월 가격'})
        self.assertEqual(condense.call_args.args[1][0]['resolved']['regions'], ['서울 / 강남지역 / 동남권 / 강남구'])
        self.assertEqual(analyze.call_args.args[0], '서초구 2026년 8월 가격')
        self.assertTrue(result['followup'])
        self.assertEqual(result['standalone_question'], '서초구 2026년 8월 가격')

    def test_clarification_answer_is_connected_to_original_question(self):
        memory = ConversationMemory(turns=3)
        first, _, analyze = self.run_chat(memory, '광주 2026년 8월 가격')
        analyze.assert_not_called()  # 광주 모호성은 규칙으로 확인 질문
        self.assertTrue(memory.history(SID)[0]['clarification'])
        result, condense, analyze = self.run_chat(
            memory, '광주광역시', analysis(region_names=['광주광역시'], month='2026-08'),
            {'is_followup': True, 'standalone_question': '광주광역시 2026년 8월 가격'})
        self.assertTrue(condense.call_args.args[1][0]['clarification'])
        self.assertEqual(analyze.call_args.args[0], '광주광역시 2026년 8월 가격')
        self.assertIn('광주', result['contexts'][0]['metadata']['region_path'])

    def test_ambiguous_region_becomes_clarification_then_followup(self):
        memory = ConversationMemory(turns=3)
        first, _, _ = self.run_chat(memory, '동구 2026년 8월 가격', analysis(region_names=['동구'], month='2026-08'))
        self.assertIn('대구 / 동구', first['answer'])
        self.assertTrue(memory.history(SID)[0]['clarification'])
        result, condense, _ = self.run_chat(
            memory, '대구', analysis(region_names=['대구 동구'], month='2026-08'),
            {'is_followup': True, 'standalone_question': '대구 동구 2026년 8월 가격'})
        self.assertEqual(result['contexts'][0]['id'], 'region-' + result['analysis']['resolved_region_ids'][0] + '-month-2026-08')

    def test_errors_are_remembered(self):
        memory = ConversationMemory(turns=3)
        with self.assertRaises(ValueError):
            self.run_chat(memory, '없는시 가격', analysis(region_names=['없는시']))
        self.assertTrue(memory.history(SID)[0]['error'])


class CondenseApiTests(unittest.TestCase):
    def client(self, parsed):
        client = MagicMock()
        client.__enter__.return_value = client
        client.responses.parse.return_value = SimpleNamespace(status='completed', output_parsed=parsed)
        return client

    def test_no_history_no_api_call(self):
        with patch('gpt_api.create_client', side_effect=AssertionError('no call')):
            self.assertEqual(gpt_api.condense_question('서울', []), {'is_followup': False, 'standalone_question': '서울'})

    def test_payload_and_followup_result(self):
        client = self.client(Condensed(is_followup=True, standalone_question='서초구 2026년 8월 가격'))
        history = [summarize_turn('강남구 8월', {'answer': '답', 'analysis': {'intent': 'lookup'}})]
        with patch('gpt_api.create_client', return_value=client):
            result = gpt_api.condense_question('서초는?', history)
        self.assertEqual(result, {'is_followup': True, 'standalone_question': '서초구 2026년 8월 가격'})
        payload = json.loads(client.responses.parse.call_args.kwargs['input'][1]['content'])
        self.assertEqual(payload['question'], '서초는?')
        self.assertEqual(payload['history'][0]['question'], '강남구 8월')

    def test_not_followup_keeps_original(self):
        client = self.client(Condensed(is_followup=False, standalone_question='바뀐 질문'))
        with patch('gpt_api.create_client', return_value=client):
            result = gpt_api.condense_question('부산 2026년 8월', [{'question': 'x'}])
        self.assertEqual(result['standalone_question'], '부산 2026년 8월')


class WebServiceTests(unittest.TestCase):
    def test_session_required_and_reset(self):
        from bubot.web import service
        from bubot.conversation import MEMORY
        with self.assertRaises(ValueError):
            service.answer({'question': '서울'})
        MEMORY.add(SID, {'question': 'x'})
        service.reset({'session_id': SID})
        self.assertEqual(MEMORY.history(SID), [])


if __name__ == '__main__':
    unittest.main()
