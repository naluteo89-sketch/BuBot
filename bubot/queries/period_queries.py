"""원본 기반 기간 평균·하락 지역 계산 (N년·지정 기간·전체 보유 기간). 데이터 존재 여부는 원본에서 확인합니다."""
import re
from decimal import Decimal, ROUND_HALF_UP
from ..filters.period_parser import explicit_years
from ..regions import find_regions, named_region, region_catalog, region_text, ranking_regions, scope_regions
from .year_query import resolve_relative_years
from ..pricing import format_price
from ..answers.answer_format import rule_answer
from ..cache.data_cache import monthly_doc, monthly_docs
from ..config import CSV_PATH, MAX_REGIONS
from ..dates import months_between, months_in_year, shift


def negative_concept(question):
    return bool(
        re.search(r'변동[률율]|상승[률율]|증감[률율]', question)
        and re.search(r'음수|마이너스', question)
        and re.search(r'될\s*수|일\s*수|가능|의미|뜻', question)
        and not re.search(r'지역|어디|곳|\d+\s*(년|개월|월)|찾아|검색|목록', question))


# '지금까지 강남구 평균'처럼 보유 자료 전체(첫 월 ~ 최신 월)를 뜻하는 표현
WHOLE_PERIOD = re.compile(r'지금까지|현재까지|최근까지|여태(?:까지|껏)?|역대|처음부터|통틀어|누적|전\s*기간|전체\s*기간|보유\s*기간|가진\s*데이터\s*(?:전체|전부|모두)')
UNTIL_NOW = re.compile(r'지금까지|현재까지|최근까지|최신\s*(?:월)?까지|여태(?:까지)?')


def period(question, months, inclusive=False):
    """inclusive=True: 기간 평균용. 'N년'을 최신 월 포함 N×12개월로 봅니다 (False면 N년 전 같은 달부터, 변동률 비교용).

    '지금까지·역대·처음부터'는 보유 첫 월부터 최신 월까지, '2020년부터 지금까지'는 2020-01부터 최신 월까지입니다.
    """
    question=resolve_relative_years(question)
    years=sorted(explicit_years(question))
    if len(years)==1 and UNTIL_NOW.search(question) and re.search(r'부터|이후|~',question):
        start=max(f'{years[0]}-01',months[0])
        return (start,months[-1]) if start<=months[-1] else None
    if len(years)==1 and not re.search(r'\d\s*월',question):
        # '2013년 이전'은 보유 첫 월 ~ 2012-12, '2013년까지'는 ~ 2013-12, '2020년 이후·부터'는 2020-01 ~ 최신 월
        y=years[0]
        if re.search(rf'{y}\s*년\s*(?:이전|전(?!국|체|세|라|남|북|주|역))',question):
            return (months[0],f'{y-1}-12') if f'{y-1}-12'>=months[0] else None
        if re.search(rf'{y}\s*년\s*까지',question) and not re.search('부터|이후',question):
            return months[0],min(f'{y}-12',months[-1])
        if re.search(rf'{y}\s*년\s*(?:이후|부터)',question) and not re.search('까지',question):
            return max(f'{y}-01',months[0]),months[-1]
    if len(years)==1:
        year=years[0]; ms=[int(v) for v in re.findall(r'(\d{1,2})\s*월',question)]
        start=f'{year}-{min(ms):02}' if ms else f'{year}-01'
        end=f'{year}-{max(ms):02}' if ms else f'{year}-12'
        return start,end
    if len(years)==2 and re.search(r'부터|까지|~',question):
        # '2021년 8월부터 2026년 8월까지'는 월까지 씁니다 (연도만 보면 2021-01 ~ 2026-12가 됨)
        dated=re.findall(r'(\d{4})\s*년\s*(\d{1,2})\s*월',question)
        if len(dated)==2:
            return f'{dated[0][0]}-{int(dated[0][1]):02}',f'{dated[1][0]}-{int(dated[1][1]):02}'
        return f'{years[0]}-01',f'{years[1]}-12'
    # 평균에서 '3개월 전·10년 전'은 기간이 아니라 한 시점입니다 (rag_service.apply_relative_period가 처리).
    ago=r'(?!\s*(?:전|이전))' if inclusive else ''
    duration=re.search(r'(?:최근\s*|지난\s*)?(?<!\d)(\d+)\s*(년|개월|달)(?:간|동안|치)?'+ago,question)
    if duration and not years:
        n=int(duration[1]);end=months[-1]
        if n<1:return None
        span=(n*12-1 if inclusive else n*12) if duration[2]=='년' else n-1
        return shift(end,-span),end
    if re.search('전체|모든',question) and '기간' in question:return months[0],months[-1]
    if not years and WHOLE_PERIOD.search(question):return months[0],months[-1]
    return None


def aggregate_query(question, corpus, unit):
    negative=bool(re.search('하락|음수|마이너스|내린|떨어',question) and re.search('지역|곳|어디',question))
    bounds=period(question,corpus.months,inclusive=not negative)
    # '평균'을 물을 때만 기간 평균입니다. '2020년 1월부터 2023년 12월까지 알려줘'는 평균 하나가 아니라 월별 값(추이)입니다.
    # 변화('몇 퍼센트 변했어')·극값('제일 낮았던 달')·가격 조건('5억 이하인 구')·순위는 평균 하나로 답하지 않습니다.
    # 대화 처리(conversation)가 이 함수를 다른 규칙보다 먼저 부르므로 여기서 걸러야 합니다 (2026-10-19 평가 #10·#15·#16).
    mean=bool(re.search(r'평균',question) and not re.search(
        r'변동|상승|하락|최고|최저|비교|차이|각각|추이|연말|12월 기준|변했|변화|퍼센트|%|올랐|내렸|오른|내린|증감|대비|보다|'
        r'가장|제일|높았|낮았|비싼|싼|비쌌|쌌|이하|이상|미만|초과|넘는|넘은|순위|언제|몇\s*월',question))
    if not bounds or not (negative or mean):return None
    start,end=bounds
    # 단일 월 가격은 기존 정확 조회 경로로 보냅니다.
    if start==end:return None
    catalog=region_catalog(corpus);scope,ambiguous=named_region(question,catalog)
    if ambiguous:return rule_answer('같은 이름의 지역이 있습니다. 시도와 시군구를 함께 지정해주세요.',intent='clarify')
    if re.search('전세|월세|거래량|거래 건수|개별|실거래 기록',question):return None
    if negative:
        level='시도' if '시도' in question else '시군구'
        allowed=scope_regions(catalog,scope,level)
        if start not in corpus.months or end not in corpus.months:
            return rule_answer(f'요청 비교 기간은 {start} ~ {end}입니다. 보유 기간 {corpus.months[0]} ~ {corpus.months[-1]} 밖의 시작·종료값이 있어 변동률을 계산할 수 없습니다.',intent='clarify')
        rows,facts,missing=_declines(corpus,catalog,allowed,start,end,unit)
        title=f'데이터 최신 월을 기준으로 {start} ~ {end}의 시작·종료 월평균 가격을 비교했습니다. 비교 가능한 {len(allowed)-missing}개 중 {len(rows)}개가 하락했고, 결측·시작값 0인 {missing}개는 제외했습니다. 비교 범위: {scope["label"] if scope else "전국"} {level}. 매월 계속 하락했다는 뜻은 아닙니다.'
        return rule_answer(title+'\n\n'+('\n'.join(facts) or '비교 가능한 지역 중 하락한 지역은 없습니다.'),facts,[d for _,_,a,b in rows for d in (a,b)],intent='period_decline',resolved_start=start,resolved_end=end,count=len(rows),excluded=missing,candidates=len(allowed)-missing)
    present=[m for m in corpus.months if start<=m<=end]
    if not present:return rule_answer(f'{start[:4]}년에 해당하는 데이터는 현재 원본 CSV에 없습니다. 요청 기간: {start} ~ {end}. 보유 기간: {corpus.months[0]} ~ {corpus.months[-1]}.',intent='clarify')
    # 2026년처럼 진행 중인 연도는 12개월을 채우지 않고 보유한 월(1월 ~ 최신 월)까지의 평균으로 계산합니다.
    requested_end=end
    start,end=max(start,present[0]),min(end,present[-1])
    clipped=end<requested_end
    one_year=start[:4]==end[:4]==requested_end[:4]
    if not scope:
        basis=f'{start[:4]}년 평균은 {int(end[5:])}월까지 보유한 {len(present)}개월 값의 합 ÷ {len(present)}로 계산합니다. ' if one_year and clipped else ''
        return rule_answer(f'{start} ~ {end} 중 {len(present)}개월 자료가 있습니다. 보유 월: '+ '·'.join(str(int(m[5:]))+'월' for m in present) + ('만 있습니다. ' if len(present)<12 else '입니다. ') + basis + '어느 지역의 평균매매가격을 조회할까요? 개별 거래 기록이 아닌 월평균 가격 자료입니다.',intent='clarify')
    selected=sorted(monthly_docs(corpus,{scope['id']},present),key=lambda d:d.metadata['month'])
    valid=[d for d in selected if d.metadata.get('price_thousand_krw') is not None]
    if not valid:return rule_answer(f'{scope["label"]}의 요청 기간 값은 모두 결측입니다.',docs=selected,intent='clarify')
    total=sum((Decimal(str(d.metadata['price_thousand_krw'])) for d in valid),Decimal(0));average=total/len(valid)
    rounded=average.quantize(Decimal('0.001'),rounding=ROUND_HALF_UP)
    expected=months_between(start,end)+1
    facts=[f"{d.metadata['month']}: {format_price(d.metadata['price_thousand_krw'],unit)}" for d in valid]
    facts.append(f'월평균 가격 합계 {format_price(total,unit)} ÷ {len(valid)}개월 = {format_price(rounded,unit)} (원 단위 반올림)')
    if clipped and one_year:
        answer=(f'{scope["label"]} {start[:4]}년 평균 매매가격은 {int(end[5:])}월까지의 자료({start} ~ {end}, {expected}개월) 기준으로 {format_price(rounded,unit)}입니다. '
                f'보유한 {len(valid)}개월의 월평균 가격 합계를 {len(valid)}로 나눈 단순 평균입니다. ')
    else:
        until=f' (보유 최신 월 {end}까지)' if clipped else ''
        answer=f'{scope["label"]} {start} ~ {end}{until}의 월평균 매매가격을 단순 평균하면 {format_price(rounded,unit)}입니다. {expected}개월 중 유효값 {len(valid)}개월을 사용했습니다. '
    if len(valid)<expected:answer+='일부 월 값이 비어 있어 유효한 월만으로 나눈 평균입니다. '
    answer+='12월 한 달의 값이 아니며, 거래 건수로 가중한 실거래 평균도 아닙니다.'
    return rule_answer(answer,facts,valid,intent='period_mean',resolved_region_ids=[scope['id']],resolved_start=start,resolved_end=end,mean_thousand=str(average),valid_months=len(valid))


def _mean_of(corpus, rid, start, end):
    """지역 하나의 start ~ end 보유 월 단순 평균. (평균 천원 Decimal, 유효 월 문서, 보유 월 목록) — 값이 없으면 평균 None."""
    present=[m for m in corpus.months if start<=m<=end]
    valid=[d for d in monthly_docs(corpus,{rid},present) if d.metadata.get('price_thousand_krw') is not None]
    if not valid:return None,valid,present
    return sum((Decimal(str(d.metadata['price_thousand_krw'])) for d in valid),Decimal(0))/len(valid),valid,present


COMPARE_WORDS = re.compile(r'비교|차이|대비|보다|vs|VS|어디가|어느\s*(?:쪽|곳)이')


def mean_comparison(question, corpus, unit):
    """연평균·기간 평균끼리 비교: 여러 지역의 같은 기간('2024년 평균으로 부산이랑 대구, 울산 비교'),
    또는 한 지역의 여러 연도('경남 2015년 연평균이랑 2025년 연평균', '작년 서울 평균이랑 올해 평균').
    이전에는 12월 한 달 값으로 대신하거나 '확인할 수 없다'고 답했습니다 (2026-10-19 평가 #6·#24·#28). 해당 없으면 None."""
    if '평균' not in question or not COMPARE_WORDS.search(question):
        return None
    q=resolve_relative_years(question)
    if re.search(r'변동|상승률|하락률|추이|흐름|(?<!\d)\d{1,2}\s*월',q):
        return None  # 변화율·월 단위 비교는 수치 경로
    catalog=region_catalog(corpus)
    mentions=[m for m in find_regions(q,catalog) if not m.qualifier]
    if not mentions or any(len(m.candidates)!=1 for m in mentions):
        return None  # 지역이 없거나 같은 이름 지역이면 다른 경로가 확인 질문
    ids=list(dict.fromkeys(m.candidates[0] for m in mentions))[:MAX_REGIONS]
    years=sorted(explicit_years(q))
    if years and not re.search(r'부터|까지|~',q):
        periods=[(f'{y}-01',f'{y}-12') for y in years]
    else:
        bounds=period(q,corpus.months,inclusive=True)
        periods=[bounds] if bounds else []
    if not ((len(ids)>=2 and len(periods)==1) or (len(ids)==1 and len(periods)>=2)):
        return None
    rows,docs,notes=[],[],[]
    for rid in ids:
        name=region_text(catalog[rid],catalog) or catalog[rid]['label']
        for start,end in periods:
            avg,valid,present=_mean_of(corpus,rid,start,end)
            one_year=start[:4]==end[:4] and start.endswith('-01') and end.endswith('-12')
            label=f'{start[:4]}년' if one_year else f'{start} ~ {end}'
            if not present:
                notes.append(f'{name} {label} 자료는 원본에 없습니다 (보유 기간 {corpus.months[0]} ~ {corpus.months[-1]}).')
                continue
            if avg is None:
                notes.append(f'{name} {label} 값은 모두 결측입니다.')
                continue
            if one_year and len(present)<12:
                notes.append(f'{start[:4]}년은 {int(present[-1][5:])}월까지 보유한 {len(present)}개월의 평균입니다.')
            if len(valid)<len(present):
                notes.append(f'{name} {label}은 결측 {len(present)-len(valid)}개월을 빼고 평균했습니다.')
            rows.append((name,label,avg,valid))
            docs.extend(valid)
    if len(rows)<2:
        return rule_answer(' '.join(notes) or '비교할 값이 없습니다.',intent='clarify',reason='mean_comparison')
    price=lambda v:format_price(v.quantize(Decimal('0.001'),rounding=ROUND_HALF_UP),unit)
    if len(ids)>=2:
        ranked=sorted(rows,key=lambda r:r[2],reverse=True)
        lead=(f'{rows[0][1]} 평균 아파트 매매가격은 '+', '.join(f'{n} {price(a)}' for n,_,a,_ in ranked)
              +f'으로 {ranked[0][0]}이(가) 가장 높습니다.')
        if len(ranked)==2:
            diff=ranked[0][2]-ranked[1][2]
            lead+=f' 차이는 {price(diff)}입니다.'
    else:
        first,last=rows[0],rows[-1]
        diff=last[2]-first[2];rate=(last[2]/first[2]-1)*100
        lead=(f'{first[0]}의 평균 아파트 매매가격은 '+', '.join(f'{l} {price(a)}' for _,l,a,_ in rows)
              +f'입니다. {last[1]}은 {first[1]}보다 {price(abs(diff))}({rate:+.2f}%) {"높" if diff>=0 else "낮"}습니다.')
    facts=[f'{n} {l} 평균: {price(a)} (월평균 {len(v)}개월의 단순 평균)' for n,l,a,v in rows]
    tail=' '.join(dict.fromkeys(notes))+(' ' if notes else '')+'평균은 월평균 매매가격의 단순 평균이며, 거래 건수로 가중한 실거래 평균이 아닙니다.'
    return rule_answer(lead+'\n\n'+'\n'.join('- '+f for f in facts)+'\n\n'+tail,facts,docs,intent='mean_comparison',
                       resolved_region_ids=ids,resolved_start=min(p[0] for p in periods),resolved_end=max(p[1] for p in periods))


def _declines(corpus, catalog, ids, start, end, unit):
    """ids 지역 중 start → end 월평균 가격이 내린 곳 (하락률 큰 순, 결측·시작값 0은 제외).

    반환: (행 목록 [(변동률, 지역 ID, 시작 문서, 종료 문서)], 근거 문장 목록, 제외한 지역 수)
    """
    rows=[];missing=0
    for rid in ids:
        a,b=monthly_doc(corpus,rid,start),monthly_doc(corpus,rid,end)
        av=a.metadata.get('price_thousand_krw') if a else None;bv=b.metadata.get('price_thousand_krw') if b else None
        if av is None or bv is None or av<=0:missing+=1;continue
        rate=(Decimal(str(bv))/Decimal(str(av))-1)*100
        if rate<0:rows.append((rate,rid,a,b))
    rows.sort(key=lambda x:(x[0],x[1]))
    facts=[f"{catalog[rid]['label']}: {format_price(a.metadata['price_thousand_krw'],unit)} → {format_price(b.metadata['price_thousand_krw'],unit)}, {rate:+.2f}%" for rate,rid,a,b in rows]
    return rows,facts,missing


def year_mean(scope, year, corpus, unit):
    """지역 하나의 연간 평균 (그 해 보유 월의 단순 평균). aggregate_query와 같은 답변 형식."""
    return aggregate_query(f"{scope['label']} {year}년 평균 매매가격",corpus,unit)


def declining_regions(question, corpus, unit):
    """'전체 기간 하락 지역' 질문: 보유 첫 월 대비 최신 월에 하락한 지역 목록 (하락률 큰 순). 해당 없으면 None."""
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
    results,facts,missing=_declines(corpus,catalog,ids,start,end,unit)
    sources=[f"{CSV_PATH.name} · {catalog[rid]['label']} · {start} ~ {end} · 원본 {first.metadata['source_row']}행 · SHA-256 {first.metadata['source_sha256']}" for _,rid,first,_ in results]
    intro=(f'전체 보유 기간 {start} ~ {end}의 시작·종료 월평균 매매가격을 비교했습니다. '
           f'전국 {level} 중 비교 가능한 {len(ids)-missing}개에서 {len(results)}개 지역이 하락했습니다. '
           f'시작·종료값 결측 또는 시작값 0인 {missing}개 지역은 제외했습니다.\n'
           '변동률=(종료 가격/시작 가격-1)×100. 하락률이 큰 순서입니다. '
           '매월 계속 하락했다는 뜻이 아니며, 개별 실거래 가격이 아닙니다.')
    return {'answer':intro+'\n\n'+('\n'.join(facts) or '하락한 지역이 없습니다.'),
            'analysis':{'intent':'declining_regions','start_month':start,'end_month':end,'level':level,'count':len(results)},
            'contexts':[],'facts':facts,'sources':sources}


def correction_query(question, history, corpus, unit):
    """사용자의 모순 지적은 원본으로 확인하며 이전 답변의 이유를 추측하지 않습니다."""
    availability = bool(re.search('없다고|없다|없었|확보|제공되지|기록이 없|데이터가 없|보유|가진 데이터',question) and re.search('왜|하지만|맞|있|명시|했|없',question))
    december = bool(re.search('왜|다시|평균',question) and re.search('12월 기준|12월.*준|그 년도|그년도',question))
    if not (availability or december):return None
    catalog=region_catalog(corpus)
    scope,ambiguous=named_region(question,catalog)
    years=explicit_years(question)
    for turn in reversed(history or []):
        previous=turn.get('standalone_question') or turn.get('question','')
        resolved=turn.get('resolved') or {}
        if not years:
            if resolved.get('resolved_start') and resolved.get('resolved_end') and resolved['resolved_start'][:4]==resolved['resolved_end'][:4]:
                years={int(resolved['resolved_start'][:4])}
            else:
                candidate=explicit_years(previous)
                if len(candidate)==1:years=candidate
        if scope is None and not ambiguous:scope,ambiguous=named_region(previous,catalog)
        if years and scope:break
    if len(years)!=1:return None
    year=next(iter(years));months=months_in_year(corpus.months,year)
    if availability:
        if not months:return rule_answer(f'{year}년 자료는 원본에 없습니다. 현재 보유 범위는 {corpus.months[0]} ~ {corpus.months[-1]}입니다.',intent='coverage_check')
        text=f'원본을 확인하면 {year}년은 {", ".join(months)} 자료가 있습니다. 해당 연도 자료가 없다는 이전 안내는 잘못된 설명입니다. 제공 체계나 수집 지연을 원인으로 추측해서는 안 됩니다. 개별 실거래 기록과 월평균 매매가격은 별도입니다.'
        if scope:
            r=year_mean(scope,year,corpus,unit)
            if r:r['answer']=text+'\n\n'+r['answer'];return r
        return rule_answer(text,intent='coverage_check')
    if scope:
        r=year_mean(scope,year,corpus,unit)
        if r:
            r['answer']='연간 평균 요청에 12월 한 달 가격을 대신 제시한 것은 잘못된 처리입니다. 연말값과 기간 평균은 다릅니다.\n\n'+r['answer']
            return r
    return None


def annual_mean_concept(question):
    return bool('평균' in question and re.search(r'연간|년을 기준|연을 기준|1년\s*동안',question)
                and re.search('의미|뜻|아니야|맞아|인가|말하|하면',question)
                and not explicit_years(question) and not re.search('지역|어디|계산해|검색해',question))
