"""RAG 지식 문서 생성.

가격 표(CSV)만으로는 의미 검색할 서술 자료가 없으므로 다음 지식 문서를 만듭니다.
- guide: knowledge/*.md 안내 문서 (## 제목 단위로 분할)
- guide_regions: 지역명·계층 안내 (CSV에서 자동 생성)
- region_profile: 지역별 프로필 (최신 가격, 기간별 변화, 역대 최고·최저, 순위, 연말 가격)
- market_monthly: 월별 전국 시장 요약 (전월·전년 동월 대비 상승/하락 지역)
- market_annual: 연도별 시장 요약 (전년 12월 대비 상승/하락 지역)
모든 수치는 Python이 원본에서 계산합니다. LLM이 만든 문장은 없습니다.
"""
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..config import KNOWLEDGE_DIR, KNOWLEDGE_VERSION
from ..dates import shift
from .documents import Corpus, Document, read_corpus
from ..pricing import format_price
from ..regions import region_catalog

# 흐름 표현 기준 (knowledge/02_용어와_계산방법.md 와 동일하게 유지)
TREND_3M = 1.0
TREND_12M = 3.0
FLAT_MOM = 0.05
PROFILE_SPANS = ((1, '1개월'), (3, '3개월'), (6, '6개월'), (12, '1년'), (36, '3년'), (60, '5년'), (120, '10년'))


@dataclass(frozen=True)
class KnowledgeCorpus:
    documents: list
    source_hash: str        # 원본 CSV + 안내 문서 전체 해시
    csv_hash: str
    latest_month: str


def _rate(a, b):
    if a is None or b is None or a <= 0:
        return None
    return (b / a - 1) * 100


def _pct(value):
    return '계산 불가(결측)' if value is None else f"{value:+.2f}%"


def _label_3m(rate):
    return '자료 부족' if rate is None else '상승' if rate >= TREND_3M else '하락' if rate <= -TREND_3M else '보합'


def _label_12m(rate):
    return '자료 부족' if rate is None else '상승' if rate >= TREND_12M else '하락' if rate <= -TREND_12M else '보합'


def _price_table(corpus: Corpus):
    table = defaultdict(dict)
    rows = {}
    for d in corpus.documents:
        m = d.metadata
        if m['kind'] != 'monthly':
            continue
        rows[m['region_id']] = m['source_row']
        if not m['missing']:
            table[m['region_id']][m['month']] = m['price_thousand_krw']
    return table, rows


def _rank_pool(catalog, region):
    """같은 시도 안의 같은 계층(시군구) 또는 전국 시도 목록. (구) 지역 제외."""
    if region['level'] == '시도':
        return [r for r in catalog.values() if r['level'] == '시도'], '전국 시도'
    if region['level'] == '시군구':
        top = region['path'][0]
        return ([r for r in catalog.values() if r['level'] == '시군구' and r['path'][0] == top
                 and not r['name'].startswith('(구)')], f"{top} 시군구(시와 산하 구 포함)")
    return [], ''


def _rank_of(rid, pool, key):
    values = [(key(r['id']), r['id']) for r in pool]
    values = [(v, i) for v, i in values if v is not None]
    if rid not in {i for _, i in values}:
        return None, len(values)
    values.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in values].index(rid) + 1, len(values)


def _source(name, row, sha):
    return f"{name} 원본 {row}행 · SHA-256 {sha}"


def region_profiles(corpus: Corpus, catalog=None):
    catalog = catalog or region_catalog(corpus)
    table, rows = _price_table(corpus)
    latest = corpus.months[-1]
    source = corpus.documents[0].metadata['source']
    docs = []
    for rid, region in catalog.items():
        series = table.get(rid, {})
        base = {'kind': 'region_profile', 'region_id': rid, 'region_path': region['label'],
                'region_name': region['name'], 'level': region['level'], 'source': source,
                'source_row': rows[rid], 'source_sha256': corpus.source_hash,
                'knowledge_version': KNOWLEDGE_VERSION}
        title = f"지역 프로필: {region['label']}"
        alias = ' '.join(dict.fromkeys(region['path']))
        lines = [title, f"지역명: {alias} (계층: {region['level']})"]
        if not series:
            lines.append(f"보유 기간 {corpus.months[0]} ~ {latest} 동안 모든 월이 결측입니다. 가격 정보가 없습니다.")
        else:
            valid = sorted(series)
            last = valid[-1]
            price = series[last]
            lines.append(f"자료가 있는 월: {valid[0]} ~ {last} 중 {len(valid)}개월, 결측 {len(corpus.months) - len(valid)}개월")
            if last != latest:
                lines.append(f"주의: 데이터 최신 월({latest})의 값이 결측이어서 아래 최신 가격은 {last} 기준입니다.")
            lines.append(f"최신 가격({last}): {format_price(price)} ({format_price(price, '천원')})")
            changes = {}
            parts = []
            for months, label in PROFILE_SPANS:
                rate = _rate(series.get(shift(last, -months)), price)
                changes[months] = rate
                parts.append(f"{label} 전 대비 {_pct(rate)}")
            lines.append("기간별 변화(최신 월과 N개월·N년 전 같은 달 비교): " + ', '.join(parts))
            # '최근 N개월'은 최신 월 포함 N개월입니다 (수치 경로·안내 문서 02와 같은 정의). 3개월 전 대비와 다릅니다.
            recent_start = shift(last, -2)
            recent = _rate(series.get(recent_start), price)
            lines.append(f"최근 3개월({recent_start}~{last}, 최신 월 포함 3개월) 변화: {_pct(recent)}")
            lines.append(f"흐름 요약(규칙 기반): 최근 3개월 {_label_3m(recent)}, 최근 1년 {_label_12m(changes[12])}")
            top, bottom = max(series.values()), min(series.values())
            high_month = min(m for m in valid if series[m] == top)
            low_month = min(m for m in valid if series[m] == bottom)
            lines.append(f"역대 최고: {high_month} {format_price(series[high_month])}, 역대 최저: {low_month} {format_price(series[low_month])}")
            lines.append(f"역대 최고가 대비 최신 가격 수준: {_pct(_rate(series[high_month], price))}")
            pool, pool_name = _rank_pool(catalog, region)
            if pool and last == latest:
                rank, size = _rank_of(rid, pool, lambda i: table.get(i, {}).get(latest))
                if rank:
                    lines.append(f"{pool_name} {size}곳 중 {latest} 가격 {rank}위")
                rank, size = _rank_of(rid, pool, lambda i: _rate(table.get(i, {}).get(shift(latest, -12)), table.get(i, {}).get(latest)))
                if rank:
                    lines.append(f"{pool_name} {size}곳 중 최근 1년 상승률 {rank}위")
            year_end = [f"{y}년 {format_price(series[f'{y}-12'])}" for y in range(int(valid[0][:4]), int(last[:4]) + 1)
                        if f"{y}-12" in series]
            if year_end:
                lines.append("연말(12월) 가격: " + ', '.join(year_end))
            if not last.endswith('-12'):
                lines.append(f"{last[:4]}년 최신({last}) 가격: {format_price(price)}")
            base.update(latest_month=last, latest_price_thousand_krw=price)
        lines.append("출처: " + _source(source, rows[rid], corpus.source_hash))
        docs.append(Document(f"profile-{rid}", '\n'.join(lines), base))
    return docs


def _movers(table, ids, catalog, start, end, n=5):
    moves = []
    for rid in ids:
        rate = _rate(table.get(rid, {}).get(start), table.get(rid, {}).get(end))
        if rate is not None:
            moves.append((rate, catalog[rid]['label'], rid))
    moves.sort(key=lambda x: (-x[0], x[1]))
    return moves


def _ups(moves, n):
    return [m for m in moves if m[0] > 0][:n]


def _downs(moves, n):
    return sorted((m for m in moves if m[0] < 0), key=lambda x: (x[0], x[1]))[:n]


def _fmt_moves(moves):
    return ', '.join(f"{label} {rate:+.2f}%" for rate, label, _ in moves) or '해당 없음'


def market_summaries(corpus: Corpus, catalog=None):
    catalog = catalog or region_catalog(corpus)
    table, rows = _price_table(corpus)
    source = corpus.documents[0].metadata['source']
    sigungu = [r['id'] for r in catalog.values() if r['level'] == '시군구' and not r['name'].startswith('(구)')]
    sido = [r['id'] for r in catalog.values() if r['level'] == '시도']
    nation = next(r['id'] for r in catalog.values() if r['level'] == '전국')
    docs = []
    for month in corpus.months:
        prev, year_ago = shift(month, -1), shift(month, -12)
        meta = {'kind': 'market_monthly', 'month': month, 'year': month[:4], 'source': source,
                'source_sha256': corpus.source_hash, 'knowledge_version': KNOWLEDGE_VERSION}
        lines = [f"전국 아파트 시장 월간 요약: {month}", f"기준 월: {month} (전월 {prev}, 전년 동월 {year_ago} 대비)"]
        nat = table.get(nation, {})
        if month in nat:
            lines.append(f"전국 평균매매가격: {format_price(nat[month])}, 전월 대비 {_pct(_rate(nat.get(prev), nat[month]))}, "
                         f"전년 동월 대비 {_pct(_rate(nat.get(year_ago), nat[month]))}")
        mom = _movers(table, sigungu, catalog, prev, month)
        if mom:
            up = sum(r > FLAT_MOM for r, _, _ in mom)
            down = sum(r < -FLAT_MOM for r, _, _ in mom)
            lines.append(f"전월 대비 시군구 {len(mom)}곳 중 상승 {up}곳, 하락 {down}곳, 보합 {len(mom) - up - down}곳")
            lines.append("전월 대비 상승률 상위 시군구: " + _fmt_moves(_ups(mom, 5)))
            lines.append("전월 대비 하락률 상위 시군구: " + _fmt_moves(_downs(mom, 5)))
        else:
            lines.append("전월 자료가 없어 전월 대비 변화를 계산할 수 없습니다.")
        yoy = _movers(table, sigungu, catalog, year_ago, month)
        if yoy:
            lines.append("전년 동월 대비 상승률 상위 시군구: " + _fmt_moves(_ups(yoy, 5)))
            lines.append("전년 동월 대비 하락률 상위 시군구: " + _fmt_moves(_downs(yoy, 5)))
        sido_mom = _movers(table, sido, catalog, prev, month, n=len(sido))
        if sido_mom:
            lines.append("시도별 전월 대비: " + _fmt_moves(sido_mom))
        priced = sorted(((table[r][month], catalog[r]['label']) for r in sigungu if month in table.get(r, {})), reverse=True)[:5]
        if priced:
            lines.append("가격 상위 시군구: " + ', '.join(f"{label} {format_price(p)}" for p, label in priced))
        lines.append(f"계산 범위: 시군구는 시와 산하 구를 함께 포함, 결측과 (구) 지역 제외. 출처: {source} 전체 행 · SHA-256 {corpus.source_hash}")
        docs.append(Document(f"market-month-{month}", '\n'.join(lines), meta))

    years = sorted({m[:4] for m in corpus.months})
    for year in years:
        in_year = [m for m in corpus.months if m.startswith(year)]
        start = f"{int(year) - 1}-12" if f"{int(year) - 1}-12" in corpus.months else in_year[0]
        end = in_year[-1]
        partial = not end.endswith('-12')
        meta = {'kind': 'market_annual', 'year': year, 'start_month': start, 'end_month': end, 'source': source,
                'source_sha256': corpus.source_hash, 'knowledge_version': KNOWLEDGE_VERSION}
        title = f"{year}년 연간 아파트 시장 요약" + (" (부분 연도, 연초 대비)" if partial else "")
        lines = [title, f"비교 기간: {start} → {end}" + (" (12월 자료가 아직 없어 최신 월까지만 비교)" if partial else "")]
        nat = table.get(nation, {})
        lines.append(f"전국: {format_price(nat.get(start))} → {format_price(nat.get(end))}, {_pct(_rate(nat.get(start), nat.get(end)))}")
        moves = _movers(table, sigungu, catalog, start, end)
        if moves:
            up = sum(r > 0 for r, _, _ in moves)
            lines.append(f"시군구 {len(moves)}곳 중 상승 {up}곳, 하락 {sum(r < 0 for r, _, _ in moves)}곳")
            lines.append(f"{year}년 상승률 상위 10개 시군구: " + _fmt_moves(_ups(moves, 10)))
            lines.append(f"{year}년 하락률 상위 10개 시군구: " + _fmt_moves(_downs(moves, 10)))
        sido_moves = _movers(table, sido, catalog, start, end)
        if sido_moves:
            lines.append(f"{year}년 시도별 변동률: " + _fmt_moves(sido_moves))
        lines.append(f"계산 범위: 시군구는 시와 산하 구를 함께 포함, 결측과 (구) 지역 제외. 출처: {source} 전체 행 · SHA-256 {corpus.source_hash}")
        docs.append(Document(f"market-year-{year}", '\n'.join(lines), meta))
    return docs


def region_guide(corpus: Corpus, catalog=None):
    catalog = catalog or region_catalog(corpus)
    source = corpus.documents[0].metadata['source']
    names = defaultdict(list)
    for r in catalog.values():
        names[r['name']].append(r['label'])
    duplicate = [f"{name}: {' | '.join(labels)}" for name, labels in names.items() if len(labels) > 1]
    groups = [r['label'] for r in catalog.values() if r['level'] == '권역']
    old = [r['label'] for r in catalog.values() if r['name'].startswith('(구)')]
    text = '\n'.join([
        "지역명 안내: 이름이 같은 지역과 권역 구성",
        f"전체 지역 {len(catalog)}개 (전국 1, 권역 {len(groups)}, 시도 {sum(r['level']=='시도' for r in catalog.values())}, "
        f"시군구 {sum(r['level']=='시군구' for r in catalog.values())})",
        "같은 이름이 여러 지역에 있어 시도를 함께 적어야 하는 지역명:",
        *duplicate,
        "광주는 광주광역시(전남광주 / 광주)와 경기도 광주시가 모두 있으므로 어느 쪽인지 적어야 합니다.",
        "권역 목록: " + ', '.join(groups),
        "폐지·통합 등으로 과거 기간만 값이 있는 (구) 지역: " + (', '.join(old) or '없음'),
        f"출처: {source} 지역 열 · SHA-256 {corpus.source_hash}"])
    meta = {'kind': 'guide', 'title': '지역명 안내', 'source': source, 'source_sha256': corpus.source_hash,
            'knowledge_version': KNOWLEDGE_VERSION}
    return [Document('guide-regions', text, meta)]


def guide_documents(directory: Path = KNOWLEDGE_DIR):
    docs = []
    for path in sorted(Path(directory).glob('*.md')):
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        text = raw.decode('utf-8-sig').replace('\r\n', '\n')  # 편집기 줄바꿈 차이로 재임베딩되지 않도록
        doc_title = next((l[2:].strip() for l in text.splitlines() if l.startswith('# ')), path.stem)
        for i, chunk in enumerate(re.split(r'^## ', text, flags=re.M)[1:], start=1):
            heading, _, body = chunk.partition('\n')
            body = body.strip()
            if not body:
                continue
            docs.append(Document(f"guide-{path.stem}-{i:02d}", f"{doc_title} - {heading.strip()}\n{body}",
                                 {'kind': 'guide', 'title': f"{doc_title} - {heading.strip()}", 'source': path.name,
                                  'source_sha256': sha, 'knowledge_version': KNOWLEDGE_VERSION}))
    return docs


def build_knowledge(corpus: Corpus | None = None, guide_dir: Path = KNOWLEDGE_DIR) -> KnowledgeCorpus:
    corpus = corpus or read_corpus()
    catalog = region_catalog(corpus)
    documents = [*guide_documents(guide_dir), *region_guide(corpus, catalog),
                 *region_profiles(corpus, catalog), *market_summaries(corpus, catalog)]
    ids = [d.id for d in documents]
    if len(ids) != len(set(ids)):
        raise ValueError('지식 문서 ID가 중복되었습니다.')
    digest = hashlib.sha256(corpus.source_hash.encode())
    for d in documents:
        if d.metadata['kind'] == 'guide':
            digest.update(d.metadata['source_sha256'].encode())
    return KnowledgeCorpus(documents, digest.hexdigest(), corpus.source_hash, corpus.months[-1])
