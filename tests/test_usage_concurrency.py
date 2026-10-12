"""API 비용 한도·질문 빈도 제한·동시 처리 테스트 (실제 API와 실제 사용량 파일을 쓰지 않음)."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import gpt_api
from bubot import usage
from bubot.web import server, service


def response(prompt, output, cached=0):
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=prompt, output_tokens=output,
                                                 input_tokens_details=SimpleNamespace(cached_tokens=cached)))


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / 'api_usage.json'

    def test_cost_uses_cached_and_output_prices(self):
        # gpt-4o-mini: 입력 0.15, 캐시 0.075, 출력 0.60 USD / 1M 토큰
        self.assertAlmostEqual(usage.cost_usd('gpt-4o-mini', 1_000_000, 0, 0), 0.15)
        self.assertAlmostEqual(usage.cost_usd('gpt-4o-mini', 1_000_000, 1_000_000, 1_000_000), 0.075 + 0.60)
        self.assertGreater(usage.cost_usd('unknown-model', 1000, 0, 0), usage.cost_usd('gpt-4o-mini', 1000, 0, 0))

    def test_record_accumulates_per_day_and_month(self):
        usage.record('gpt-4o-mini', response(5300, 80, 5120), path=self.path, day='2026-10-12')
        usage.record('text-embedding-3-small', SimpleNamespace(usage=SimpleNamespace(prompt_tokens=20)),
                     path=self.path, day='2026-10-12')
        usage.record('gpt-4o', response(300, 40), path=self.path, day='2026-10-01')
        today = usage.totals(path=self.path, day='2026-10-12')
        self.assertEqual(today['today_calls'], 2)
        self.assertGreater(today['month_usd'], today['today_usd'])
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data['days']['2026-10-12']['models']['gpt-4o-mini']['cached'], 5120)

    def test_mock_responses_are_not_recorded(self):
        self.assertEqual(usage.record('gpt-4o-mini', MagicMock(), path=self.path), 0.0)
        self.assertFalse(self.path.exists())

    def test_budget_blocks_api_calls(self):
        usage.record('gpt-4o', response(1_000_000, 0), path=self.path, day='2026-10-12')  # $2.5
        with patch.object(usage, 'DAILY_BUDGET_USD', 2.0):
            with self.assertRaisesRegex(usage.BudgetExceeded, '오늘 API 사용 한도'):
                usage.check(path=self.path, day='2026-10-12')
            usage.check(path=self.path, day='2026-10-13')  # 다음 날은 통과
        with patch.object(usage, 'DAILY_BUDGET_USD', 0), patch.object(usage, 'MONTHLY_BUDGET_USD', 2.0):
            with self.assertRaisesRegex(usage.BudgetExceeded, '이번 달'):
                usage.check(path=self.path, day='2026-10-20')

    def test_gpt_calls_are_metered_and_blocked_over_budget(self):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(status='completed', output_text='답변',
                                                               usage=response(1200, 90).usage)
        context = [{'id': 'd', 'text': '근거', 'metadata': {'kind': 'guide'}}]
        with patch('gpt_api.shared_client', return_value=client), patch.object(usage, 'USAGE_PATH', self.path):
            gpt_api.generate_answer('질문', context)
            self.assertEqual(usage.totals(path=self.path)['today_calls'], 1)
            with patch.object(usage, 'check', side_effect=usage.BudgetExceeded('한도')):
                with self.assertRaises(usage.BudgetExceeded):
                    gpt_api.generate_answer('질문', context)
        self.assertEqual(client.responses.create.call_count, 1)  # 한도 초과 시 호출하지 않음


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        service._recent.clear()
        service._recent_all.clear()

    def test_session_limit_per_minute(self):
        with patch.object(service, 'SESSION_PER_MINUTE', 3), patch.object(service, 'GLOBAL_PER_MINUTE', 0):
            for i in range(3):
                service.check_rate({'session_id': 'a'}, now=100 + i)
            with self.assertRaises(service.RateLimited):
                service.check_rate({'session_id': 'a'}, now=104)
            service.check_rate({'session_id': 'b'}, now=104)  # 다른 대화는 영향 없음
            service.check_rate({'session_id': 'a'}, now=161)  # 1분 지나면 다시 가능

    def test_global_limit_per_minute(self):
        with patch.object(service, 'SESSION_PER_MINUTE', 0), patch.object(service, 'GLOBAL_PER_MINUTE', 2):
            service.check_rate({'session_id': 'a'}, now=1)
            service.check_rate({'session_id': 'b'}, now=2)
            with self.assertRaises(service.RateLimited):
                service.check_rate({'session_id': 'c'}, now=3)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        service._recent.clear()
        service._recent_all.clear()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.allow_lan = False
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, session):
        port = self.httpd.server_port
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        connection.request('POST', '/api/chat', json.dumps({'question': '서울 가격', 'session_id': session}),
                           {'Content-Type': 'application/json', 'Host': f'127.0.0.1:{port}'})
        reply = connection.getresponse()
        return reply.status, json.loads(reply.read())

    def run_parallel(self, sessions, delay=0.4):
        def slow_answer(payload):
            time.sleep(delay)
            return {'answer': 'ok', 'route': 'rule'}
        statuses = []
        with patch('bubot.web.service.answer', side_effect=slow_answer), patch('bubot.web.history.save', return_value='t'):
            threads = [threading.Thread(target=lambda s=s: statuses.append(self.post(s)[0])) for s in sessions]
            started = time.perf_counter()
            for t in threads:
                t.start()
                time.sleep(0.02)
            for t in threads:
                t.join()
        return sorted(statuses), time.perf_counter() - started

    def test_different_sessions_run_at_the_same_time(self):
        statuses, elapsed = self.run_parallel(['session-aaaa-0001', 'session-bbbb-0002', 'session-cccc-0003'])
        self.assertEqual(statuses, [200, 200, 200])
        self.assertLess(elapsed, 1.0)  # 한 질문씩 처리했다면 1.2초 이상

    def test_same_session_overlap_is_rejected(self):
        statuses, _ = self.run_parallel(['session-aaaa-0001', 'session-aaaa-0001'])
        self.assertEqual(statuses, [200, 429])

    def test_requests_wait_for_a_free_slot(self):
        with patch.object(server, 'SLOTS', threading.BoundedSemaphore(1)), patch.object(server, 'QUEUE_WAIT_SECONDS', 5):
            statuses, elapsed = self.run_parallel(['session-aaaa-0001', 'session-bbbb-0002'], delay=0.3)
        self.assertEqual(statuses, [200, 200])  # 거절하지 않고 차례를 기다림
        self.assertGreaterEqual(elapsed, 0.55)

    def test_full_queue_returns_503(self):
        with patch.object(server, 'SLOTS', threading.BoundedSemaphore(1)), patch.object(server, 'QUEUE_WAIT_SECONDS', 0.1):
            statuses, _ = self.run_parallel(['session-aaaa-0001', 'session-bbbb-0002'], delay=0.5)
        self.assertEqual(statuses, [200, 503])


if __name__ == '__main__':
    unittest.main()
