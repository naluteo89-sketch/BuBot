"""화면의 '이전 대화 기억 만료' 안내가 서버 기억이 살아 있을 때 잘못 뜨지 않는지 확인합니다."""
import unittest
from unittest.mock import patch

from bubot.conversation import MEMORY
from bubot.web import service


class GuidanceMemoryTests(unittest.TestCase):
    SESSION = 'memory-notice-0001'

    def setUp(self):
        MEMORY.reset(self.SESSION)

    def tearDown(self):
        MEMORY.reset(self.SESSION)

    def test_guidance_answer_reports_memory_use(self):
        MEMORY.add(self.SESSION, {'question': '서울 2026년 8월 가격'})

        def guidance(session_id, question, **_):
            MEMORY.add(session_id, {'question': question})
            raise ValueError('조회할 지역을 알려 주세요.')

        with patch.object(service, 'chat', guidance):
            response = service.answer({'question': '근처는?', 'session_id': self.SESSION})
        self.assertEqual(response['answer'], '조회할 지역을 알려 주세요.')
        self.assertEqual(response['memory_used'], 1)   # 이전 대화 1개를 기억하고 있었음 → 만료 아님
        self.assertEqual(response['memory_turns'], 2)

    def test_first_question_reports_nothing_used(self):
        def guidance(session_id, question, **_):
            MEMORY.add(session_id, {'question': question})
            raise ValueError('조회할 지역을 알려 주세요.')

        with patch.object(service, 'chat', guidance):
            response = service.answer({'question': '근처는?', 'session_id': self.SESSION})
        self.assertEqual(response['memory_used'], 0)
        self.assertEqual(response['memory_turns'], 1)


if __name__ == '__main__':
    unittest.main()
