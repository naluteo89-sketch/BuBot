"""전체 보유 기간의 지역별 하락 여부를 원본으로 집계합니다."""
import re
from .regions import region_catalog, ranking_regions
from .pricing import format_price
from .config import CSV_PATH


def declining_regions(question, corpus, unit):
    if not (re.search(r'전체|모든|전 기간',question) and '기간' in question and
            re.search(r'하락|떨어|내린',question) and re.search(r'지역|곳',question)):
        return None
    catalog=region_catalog(corpus)
    level='시도' if '시도' in question else '시군구'
    named=[r for r in catalog.values() if r['name']!='전국' and r['name'] in question]
    if named:
        return {'answer':'전국 시군구 전체 비교인지, 특정 지역 내 비교인지 확인해주세요. 전국 비교는 “모든 기간 데이터 중 하락한 지역들”로 질문해주세요.',
                'analysis':{'intent':'clarify'},'contexts':[],'facts':[],'sources':[]}
    ids=ranking_regions(catalog,level=level)
    start,end=corpus.months[0],corpus.months[-1]
    monthly={(d.metadata['region_id'],d.metadata['month']):d for d in corpus.documents if d.metadata['kind']=='monthly'}
    results=[];missing=0
    for rid in ids:
        first,last=monthly.get((rid,start)),monthly.get((rid,end))
        a=first.metadata.get('price_thousand_krw') if first else None
        b=last.metadata.get('price_thousand_krw') if last else None
        if a is None or b is None or a<=0:
            missing+=1;continue
        if b<a:results.append(((b/a-1)*100,rid,a,b,first,last))
    results.sort(key=lambda row:(row[0],row[1]))
    facts=[f"{catalog[rid]['label']}: {format_price(a,unit)} → {format_price(b,unit)}, {rate:+.2f}%" for rate,rid,a,b,_,_ in results]
    sources=[f"{CSV_PATH.name} · {catalog[rid]['label']} · {start} ~ {end} · 원본 {first.metadata['source_row']}행 · SHA-256 {first.metadata['source_sha256']}" for _,rid,_,_,first,_ in results]
    intro=(f'전체 보유 기간 {start} ~ {end}의 시작·종료 월평균 매매가격을 비교했습니다. '
           f'전국 {level} 중 비교 가능한 {len(ids)-missing}개에서 {len(results)}개 지역이 하락했습니다. '
           f'시작·종료값 결측 또는 시작값 0인 {missing}개 지역은 제외했습니다.\n'
           '변동률=(종료 가격/시작 가격-1)×100. 하락률이 큰 순서입니다. '
           '매월 계속 하락했다는 뜻이 아니며, 개별 실거래 가격이 아닙니다.')
    return {'answer':intro+'\n\n'+('\n'.join(facts) or '하락한 지역이 없습니다.'),
            'analysis':{'intent':'declining_regions','start_month':start,'end_month':end,'level':level,'count':len(results)},
            'contexts':[],'facts':facts,'sources':sources}
