"""웹 서버·응답 변환 테스트. 실제 API와 테스트기록 폴더를 사용하지 않습니다."""
import base64
import contextlib
import hashlib
import http.client
import io
import json
import re
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from bubot.config import PROJECT_NAME
from bubot.documents import Document
from bubot.web import history, server, service


def corpus(series):
    """series: {지역ID: [월별 가격 또는 None]}"""
    months = ('2026-06', '2026-07', '2026-08')
    docs = [Document(f'{rid}-{m}', 'price', {'kind': 'monthly', 'region_id': rid, 'region_path': f'지역{rid}',
                                            'month': m, 'missing': v is None})
            for rid, values in series.items() for m, v in zip(months, values)]
    return SimpleNamespace(documents=docs, months=months, source_hash=str(sorted(series.items())))


class ServiceTests(unittest.TestCase):
    def test_route_names_for_rule_and_clarify_answers(self):
        self.assertEqual(service.route_of({'route': 'numeric'}), 'numeric')
        self.assertEqual(service.route_of({'analysis': {'intent': 'clarify'}}), 'clarify')
        self.assertEqual(service.route_of({'analysis': {'intent': 'year_query'}}), 'rule')
        with patch('bubot.web.service.chat', return_value={'answer': '2025년 보유 월…', 'analysis': {'intent': 'year_query'},
                                                           'timings': {}}):
            result = service.answer({'question': '작년 서울 가격', 'session_id': 'session-test-0001'})
        self.assertEqual(result['route'], 'rule')

    def test_coverage_note_from_data(self):
        note = service.coverage_note(corpus({'1': [1, 2, 3], '2': [None, 2, 3], '3': [None, None, 3], '4': [None] * 3}))
        self.assertIn('4개 지역 중 3곳', note)
        self.assertIn('지역3 1곳은 2026-08 ~ 2026-08(1개월)만', note)
        self.assertEqual(service.coverage_note(corpus({'1': [1, 2, 3]})), '모든 지역에 전체 기간 값이 있습니다.')

    def test_status_reports_period_even_when_index_is_stale(self):
        data = corpus({'1': [1, 2, 3]})
        with patch('gpt_api.check_configuration', return_value={'키 인식': True}), \
             patch('bubot.data_cache.load_corpus', return_value=data), \
             patch('bubot.rag_service.open_index', side_effect=ValueError('embed 필요')):
            info = service.status()
        self.assertEqual((info['project'], info['period'], info['index_ready']), (PROJECT_NAME, '2026-06 ~ 2026-08', False))
        self.assertEqual(info['index_message'], 'embed 필요')
        self.assertGreaterEqual(info['memory_turns'], 3)


class HistoryTests(unittest.TestCase):
    def test_route_label_and_project_in_view(self):
        record = {'created_at': '2026-10-08T15:00:00+09:00', 'id': 'x', 'status': 'success', 'unit': None,
                  'elapsed_seconds': 0.1, 'question': '작년 서울 가격', 'result': {'answer': '답', 'route': 'rule'}}
        self.assertIn('rule (원본 직접 집계·안내 (AI 답변 생성 없음))', history.readable(record))
        self.assertIn(PROJECT_NAME, history.render([record]))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, method, path, payload=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=10)
        body = json.dumps(payload).encode() if payload is not None else None
        connection.request(method, path, body, {'Content-Type': 'application/json'} if body else {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def test_history_page_style_allowed_by_csp(self):
        with patch.object(history, 'html_view', return_value=history.render([])):
            response, body = self.call('GET', '/history')
        style = re.search(r'<style>(.*?)</style>', body.decode('utf-8'), re.S)[1]
        digest = base64.b64encode(hashlib.sha256(style.encode('utf-8')).digest()).decode()
        self.assertIn(f"style-src 'sha256-{digest}'", response.getheader('Content-Security-Policy'))
        self.assertNotIn('script-src', response.getheader('Content-Security-Policy'))

    def test_static_page_keeps_default_csp(self):
        response, body = self.call('GET', '/')
        self.assertEqual(response.getheader('Content-Security-Policy'), server.CSP)
        self.assertIn('id="period"', body.decode('utf-8'))

    def test_api_failure_logged_with_type_and_elapsed(self):
        stderr = io.StringIO()
        with patch.object(service, 'answer', side_effect=RuntimeError('network down')), \
             patch.object(history, 'save', return_value='record-test') as save, \
             contextlib.redirect_stderr(stderr):
            response, body = self.call('POST', '/api/chat', {'question': '서울', 'session_id': 'session-test-0001'})
        result = json.loads(body)
        self.assertEqual(response.status, 502)
        self.assertEqual(result['error_type'], 'RuntimeError')
        self.assertIn('elapsed_seconds', result)
        self.assertEqual(save.call_args.args[1]['error_type'], 'RuntimeError')  # 테스트 기록에도 오류 종류 저장
        self.assertIn('RuntimeError', stderr.getvalue())
        self.assertNotIn('network down', result['error'])  # 화면에는 상세 미노출

    def test_foreign_host_rejected(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=10)
        connection.request('GET', '/api/status', headers={'Host': 'evil.example'})
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()


if __name__ == '__main__':
    unittest.main()
