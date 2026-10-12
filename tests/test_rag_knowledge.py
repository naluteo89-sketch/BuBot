import csv
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from rag_fixtures import SPEC, fake_embed, knowledge_collection

from bubot.config import CSV_PATH, KNOWLEDGE_DIR
from bubot.documents import read_corpus
from bubot.knowledge import build_knowledge
from bubot.knowledge_index import build_knowledge_index, retrieve, validate_knowledge_index
from bubot.rag_service import apply_relative_period, ask


def raw_prices():
    """documents.py와 독립적으로 CSV를 직접 읽어 검증값을 만듭니다."""
    raw = CSV_PATH.read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp949')
    rows = list(csv.reader(io.StringIO(text)))
    months = [f"{v.split('년')[0].strip()}-{int(v.split('년')[1].replace('월', '').strip()):02d}" for v in rows[0][5:]]
    table = {}
    for row in rows[3:]:
        table[row[0]] = {m: Decimal(v.replace(',', '').replace('"', '')) for m, v in zip(months, row[5:]) if v.strip()}
    return months, table


def analysis(**changes):
    return {**dict(intent='explain', region_names=[], month=None, start_month=None, end_month=None,
                   display_unit=None, level='시군구', rewritten_query='질문', message=''), **changes}


class KnowledgeCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = read_corpus()
        cls.knowledge = build_knowledge(cls.corpus)
        cls.docs = {d.id: d for d in cls.knowledge.documents}
        cls.months, cls.table = raw_prices()

    def test_document_kinds_and_counts(self):
        kinds = {}
        for d in self.knowledge.documents:
            kinds[d.metadata['kind']] = kinds.get(d.metadata['kind'], 0) + 1
        self.assertEqual(kinds['region_profile'], 245)
        self.assertEqual(kinds['market_monthly'], len(self.corpus.months))
        self.assertEqual(kinds['market_annual'], len({m[:4] for m in self.corpus.months}))
        self.assertGreaterEqual(kinds['guide'], 10)
        self.assertTrue(all(d.metadata['source_sha256'] for d in self.knowledge.documents))

    def test_profile_numbers_match_raw_csv(self):
        latest = self.months[-1]
        for rid in ('8', '38', '170'):
            series = self.table[rid]
            text = self.docs[f'profile-{rid}'].text
            year_ago = f"{int(latest[:4]) - 1}{latest[4:]}"
            expected = (series[latest] / series[year_ago] - 1) * 100
            self.assertIn(f"1년 전 대비 {expected:+.2f}%", text)
            recent_start = f"{latest[:5]}{int(latest[5:]) - 2:02d}" if int(latest[5:]) > 2 else None
            if recent_start and recent_start in series:  # '최근 3개월'은 최신 월 포함 3개월 (수치 경로와 같은 정의)
                recent = (series[latest] / series[recent_start] - 1) * 100
                self.assertIn(f"최근 3개월({recent_start}~{latest}, 최신 월 포함 3개월) 변화: {recent:+.2f}%", text)
            high = max(series.values())
            high_month = min(m for m, v in series.items() if v == high)
            self.assertIn(f"역대 최고: {high_month}", text)

    def test_annual_and_monthly_movers_match_raw_csv(self):
        # 2021년 시군구 상승률 1위를 원본에서 직접 계산해 문서와 대조
        from bubot.regions import region_catalog
        catalog = region_catalog(self.corpus)
        pool = [r['id'] for r in catalog.values() if r['level'] == '시군구' and not r['name'].startswith('(구)')]
        rates = sorted(((self.table[r]['2021-12'] / self.table[r]['2020-12'] - 1) * 100, catalog[r]['label'])
                       for r in pool if '2020-12' in self.table[r] and '2021-12' in self.table[r] and self.table[r]['2020-12'] > 0)
        top_rate, top_label = rates[-1]
        line = next(l for l in self.docs['market-year-2021'].text.splitlines() if '상승률 상위' in l)
        self.assertTrue(line.split(': ', 1)[1].startswith(f"{top_label} {top_rate:+.2f}%"))
        down_line = next(l for l in self.docs['market-year-2021'].text.splitlines() if '하락률 상위' in l)
        self.assertTrue(all('-' in part.rsplit(' ', 1)[1] for part in down_line.split(': ', 1)[1].split(', ')))

    def test_partial_year_labelled(self):
        last_year = self.months[-1][:4]
        if not self.months[-1].endswith('-12'):
            self.assertIn('부분 연도', self.docs[f'market-year-{last_year}'].text)

    def test_guides_split_by_heading(self):
        guides = [d for d in self.knowledge.documents if d.id.startswith('guide-0')]
        self.assertTrue(all('\n' in d.text and '## ' not in d.text for d in guides))
        self.assertTrue(any('변동률(%)' in d.text for d in guides))


class KnowledgeIndexTests(unittest.TestCase):
    def test_build_reuse_validate_and_change_detection(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            guides = root / 'knowledge'
            guides.mkdir()
            for path in KNOWLEDGE_DIR.glob('*.md'):
                (guides / path.name).write_bytes(path.read_bytes())
            kwargs = dict(guide_dir=guides, db_path=root / 'vectors', manifest_path=root / 'k.json', spec=SPEC)
            first = build_knowledge_index(**kwargs, embedder=fake_embed, progress=lambda _: None)
            self.assertEqual(first['reused'], 0)
            never = MagicMock(side_effect=AssertionError('no API call expected'))
            second = build_knowledge_index(**kwargs, embedder=never, progress=lambda _: None)
            self.assertEqual(second['embedded'], 0)
            knowledge = build_knowledge(read_corpus(), guides)
            validate_knowledge_index(knowledge, root / 'vectors', root / 'k.json', SPEC)
            # 안내 문서를 고치면 색인 불일치 → 해당 문서만 다시 임베딩
            target = sorted(guides.glob('*.md'))[0]
            target.write_text(target.read_text(encoding='utf-8') + '\n## 추가\n새 안내 문장\n', encoding='utf-8')
            changed = build_knowledge(read_corpus(), guides)
            with self.assertRaises(ValueError):
                validate_knowledge_index(changed, root / 'vectors', root / 'k.json', SPEC)
            third = build_knowledge_index(**kwargs, embedder=fake_embed, progress=lambda _: None)
            self.assertEqual(third['embedded'], 1)  # 새로 추가된 안내 문서만 API 호출

    def test_retrieve_pools_and_threshold(self):
        collection = knowledge_collection()
        hits, diag = retrieve(collection, '강남구 흐름', region_ids=['38'], month='2026-08', year='2026',
                              general_k=3, embedder=fake_embed, max_distance=2.0)
        ids = [h['id'] for h in hits]
        self.assertEqual(ids[:3], ['profile-38', 'market-month-2026-08', 'market-year-2026'] if ids[1] == 'market-month-2026-08'
                         else ['profile-38', 'market-year-2026', 'market-month-2026-08'])
        strict, diag = retrieve(collection, '전혀 관계없는 질문', general_k=3, embedder=fake_embed, max_distance=0.0)
        self.assertEqual(strict, [])
        self.assertEqual(len(diag['rejected']), 3)


class RouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = read_corpus()

    def run_ask(self, parsed, question='질문'):
        with patch('bubot.rag_service.open_index', return_value=(self.corpus, knowledge_collection())), \
             patch('gpt_api.analyze_query', return_value=parsed), \
             patch('gpt_api.embed_texts', side_effect=fake_embed) as embed, \
             patch('gpt_api.generate_answer', return_value={'answer': '모의 답변'}) as generate:
            result = ask(question)
        return result, generate, embed

    def test_explain_uses_retrieved_knowledge_only(self):
        result, generate, embed = self.run_ask(analysis(region_names=['강남구']), '강남구 요즘 흐름 어때?')
        self.assertEqual(result['route'], 'explain')
        self.assertEqual(result['contexts'][0]['id'], 'profile-38')
        self.assertEqual(embed.call_count, 1)  # 질문 임베딩 1회
        contexts = generate.call_args.args[1]
        self.assertTrue(all(c['metadata']['kind'] not in {'monthly', 'timeline'} for c in contexts))
        self.assertTrue(all('지식 문서' in s for s in result['sources']))
        self.assertEqual(result['facts'], [])

    def test_explain_market_question_defaults_to_latest_month(self):
        result, _, _ = self.run_ask(analysis(), '많이 오른 곳 Top 5')
        ids = [c['id'] for c in result['contexts']]
        latest = self.corpus.months[-1]
        self.assertIn(f'market-month-{latest}', ids)
        self.assertIn(f'market-year-{latest[:4]}', ids)

    def test_explain_year_question(self):
        result, _, _ = self.run_ask(analysis(start_month='2021-01', end_month='2021-12'), '2021년에 가장 많이 오른 지역은?')
        self.assertIn('market-year-2021', [c['id'] for c in result['contexts']])

    def test_explain_without_evidence_skips_generation(self):
        with patch('bubot.rag_service.retrieve', return_value=([], {'used': [], 'rejected': []})):
            result, generate, _ = self.run_ask(analysis(), '오늘 점심 메뉴 추천')
        generate.assert_not_called()
        self.assertIn('근거 문서를 지식 색인에서 찾지 못했습니다', result['answer'])

    def test_numeric_lookup_direct_document_plus_profile(self):
        result, generate, _ = self.run_ask(analysis(intent='lookup', region_names=['서울']), '서울 얼마야')
        self.assertEqual(result['route'], 'numeric')
        ids = [c['id'] for c in result['contexts']]
        latest = self.corpus.months[-1]
        self.assertEqual(ids[0], f'region-8-month-{latest}')
        self.assertIn('profile-8', ids)
        self.assertTrue(result['facts'])

    def test_numeric_past_month_has_no_latest_profile(self):
        result, _, _ = self.run_ask(analysis(intent='lookup', region_names=['서울'], month='2015-03'), '서울 2015년 3월')
        self.assertNotIn('profile-8', [c['id'] for c in result['contexts']])

    def test_rank_adds_market_summary_of_same_month(self):
        result, _, _ = self.run_ask(analysis(intent='argmax', region_names=['서울'], month='2026-08'), '서울 가장 비싼 구')
        ids = [c['id'] for c in result['contexts']]
        self.assertIn('market-month-2026-08', ids)
        self.assertEqual(result['contexts'][0]['metadata']['region_id'], '38')

    def test_recent_months_fixed_by_code(self):
        months = self.corpus.months
        parsed = apply_relative_period('부산 최근 3개월 추이', analysis(intent='trend', start_month='2025-11', end_month='2026-01'), months)
        self.assertEqual((parsed['start_month'], parsed['end_month']), ('2026-06', '2026-08'))
        parsed = apply_relative_period('서울 최근 1년', analysis(intent='lookup'), months)
        self.assertEqual((parsed['intent'], parsed['start_month']), ('trend', '2025-08'))
        result, _, _ = self.run_ask(analysis(intent='trend', region_names=['부산'], start_month='2025-11', end_month='2026-01'),
                                    '부산 최근 3개월 추이')
        self.assertTrue(any('2026-06' in f and '2026-08' in f for f in result['facts']))

    def test_generate_answer_payload_marks_kinds(self):
        import gpt_api
        from types import SimpleNamespace
        client = MagicMock()
        client.__enter__.return_value = client
        client.responses.create.return_value = SimpleNamespace(status='completed', output_text='답변')
        knowledge = build_knowledge(self.corpus)
        doc = next(d for d in knowledge.documents if d.id == 'profile-8')
        with patch('gpt_api.create_client', return_value=client):
            gpt_api.generate_answer('서울 흐름', [{'id': doc.id, 'text': doc.text, 'metadata': doc.metadata}], {}, 'korean', [])
        payload = json.loads(client.responses.create.call_args.kwargs['input'][1]['content'])
        self.assertEqual(payload['contexts'][0]['kind'], 'region_profile')
        self.assertNotIn('display_price', payload['contexts'][0])


if __name__ == '__main__':
    unittest.main()
