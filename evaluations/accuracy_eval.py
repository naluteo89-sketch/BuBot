"""실제 API 평가: 기본 질문은 2012-01~2026-08 범위. --include-outside로 범위 밖 거부 평가 추가. 원본 CSV에서 독립 정답을 생성합니다."""
import argparse,csv,json,re,sys,time
from pathlib import Path
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor,as_completed
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bubot.rag_service import ask
ROOT=Path(__file__).resolve().parents[1]
with (ROOT/'data/new_prices.csv').open(encoding='cp949',newline='') as f: source=list(csv.reader(f))
months=[f'{int(re.search(r"\d{4}",s)[0])}-{int(re.search(r"(\d+)월",s)[1]):02}' for s in source[0][5:]]
EVALUATION_START = '2012-01'
EVALUATION_END = '2026-08'
if months[0] != EVALUATION_START or months[-1] != EVALUATION_END:
 raise ValueError('평가 데이터 범위가 2012-01~2026-08과 다릅니다. 평가 설정을 확인하세요.')
rows={r[0]:r for r in source[3:]}
def rid(name, parent=None):
 matches=[r[0] for r in rows.values() if r[4]==name and (parent is None or parent in r[1:5])]
 assert len(matches)==1,(name,matches)
 return matches[0]
def price(i,m):
 s=rows[i][5+months.index(m)].replace('"','').replace(',','').strip()
 return Decimal(s) if s else None
cases=[]
def case(q,kind,names=(),month='2026-08',start=None,end=None,scope=None,mode=None):
 ids=[rid(n,p) if isinstance(n,tuple) else rid(n) for n in names for p in ([] if not isinstance(n,tuple) else [])] if False else [rid(*n) if isinstance(n,tuple) else rid(n) for n in names]
 cases.append(dict(id=len(cases)+1,question=q,kind=kind,ids=ids,month=month,start=start,end=end,scope=scope,mode=mode))
for q,n,m in [
 ('서울 2026년 8월 평균매매가격은?',('서울',),'2026-08'),
 ('전국 2025년 11월 가격 알려줘',('전국',),'2025-11'),
 ('부산 2026년 7월 평균 가격',('부산',),'2026-07'),
 ('제주시 2026년 8월 평균 가격',('제주시',),'2026-08'),
 ('서귀포시 2026년 1월 평균매매가격',('서귀포시',),'2026-01'),
 ('경기도 2026년 6월 평균 가격',('경기',),'2026-06'),
 ('서울 중구 2026년 8월 가격',(('중구','서울'),),'2026-08'),
 ('광주광역시 동구 2026년 8월 가격',(('동구','광주'),),'2026-08'),
 ('세종 2026년 8월 가격',('세종',),'2026-08'),
 ('서울 가장 최근 평균매매가격 알려줘',('서울',),'2026-08')]:case(q,'lookup',n,m)
for q,n in [
 ('강남과 서초 2026년 8월 각각 가격과 차이',('강남구','서초구')),
 ('서울과 부산 2026년 8월 각각 가격과 차이',('서울','부산')),
 ('제주시와 서귀포시 2026년 8월 각각 가격과 차이',('제주시','서귀포시')),
 ('서울 중구와 광주 동구 2026년 8월 각각 가격과 차이',(('중구','서울'),('동구','광주')))]:case(q,'compare',n)
for q,n,s,e in [
 ('부산 최근 3개월 추이, 시작과 종료 가격 및 변동률',('부산',),'2026-06','2026-08'),
 ('서울 2026년 6월부터 8월까지 추이, 시작과 종료 가격 및 변동률',('서울',),'2026-06','2026-08'),
 ('전국 2025년 11월부터 2026년 8월까지 추이, 시작과 종료 가격 및 변동률',('전국',),'2025-11','2026-08'),
 ('서귀포시 2026년 1월부터 8월까지 추이, 시작과 종료 가격 및 변동률',('서귀포시',),'2026-01','2026-08')]:case(q,'trend',n,start=s,end=e)
for scope in ['서울','시도']:
 for mode,word in [('max','비싼'),('min','싼')]:case(f'2026년 8월 {"서울의 구" if scope=="서울" else "시도"} 중 가장 {word} 곳과 가격','rank',scope=scope,mode=mode)
case('경기 부천시 (구)원미구 2026년 8월 가격','missing',('(구)원미구',))
case('경기 화성시 만세구 2025년 11월 가격','missing',('만세구',),month='2025-11')
for q in ['동구 2026년 8월 가격','중구 2026년 8월 가격']:case(q,'ambiguous')
for q in ['서울 2027년 1월 가격','서울 2011년 1월 가격']:case(q,'outside')
for q in ['서울 2026년 8월 아파트 전세 가격','서울 내년 아파트 매매가격 예측해줘']:case(q,'unsupported')
# 2026-10-08 수정 확인 (기존 번호 유지를 위해 끝에 추가): 연도만 지정한 비교, '최근 10년', '작년 대비'
case('강남과 서초 2023년 각각 가격과 차이','compare',('강남구','서초구'),month='2023-12')
case('서울 최근 10년 추이, 시작과 종료 가격 및 변동률','trend',('서울',),start='2016-08',end='2026-08')
case('서울 작년 대비 얼마나 올랐어? 시작과 종료 가격 및 변동률','trend',('서울',),start='2025-08',end='2026-08')
PROVINCES={'서울','부산','대구','인천','광주','대전','울산','세종','경기','강원','충북','충남','전북','전남','경북','경남','제주'}
for c in cases:
 if c['kind']=='rank':
  candidates=[i for i,r in rows.items() if (r[4] in PROVINCES if c['scope']=='시도' else r[1]=='서울' and r[4].endswith('구')) and price(i,c['month']) is not None]
  winner=(max if c['mode']=='max' else min)(candidates,key=lambda i:price(i,c['month']))
  c['ids']=[winner];c['candidate_count']=len(candidates)
 c['expected_prices']=[str(price(i,m)) for i in c['ids'] for m in ([c['start'],c['end']] if c['kind']=='trend' else [c['month']])]
 if c['kind']=='compare':c['expected_difference']=str(abs(price(c['ids'][1],c['month'])-price(c['ids'][0],c['month'])))
 if c['kind']=='trend':c['expected_rate']=round(float((price(c['ids'][0],c['end'])/price(c['ids'][0],c['start'])-1)*100),2)

def evaluate(c):
 start=time.perf_counter();r={**c}
 try:
  result=ask(c['question'],unit='천원')
  r['result']=result
 except Exception as error:
  # 원문/자격증명을 포함할 수 있는 외부 예외는 출력하지 않습니다.
  r['error_type']=type(error).__name__
  if isinstance(error,ValueError):r['error']=str(error)
 r['elapsed_seconds']=round(time.perf_counter()-start,3)
 result=r.get('result',{});a=result.get('analysis',{});contexts=result.get('contexts',[])
 answer=result.get('answer','');flat=re.sub(r'[,\s*]','',answer)
 amounts={Decimal(v) for v in re.findall(r'([+-]?\d+(?:\.\d+)?)천원',flat)}
 r['answer_amounts']=[str(v) for v in sorted(amounts)]
 checks={}
 kind=c['kind']
 if kind in {'ambiguous','outside','unsupported'}:
  checks['no_numeric_result']=not contexts and not result.get('facts')
  checks['explicit_rejection']=a.get('intent')=='clarify' or r.get('error_type')=='ValueError'
 else:
  checks['completed']=bool(contexts) and bool(answer)
  checks['region']=set(a.get('resolved_region_ids',[]))==set(c['ids']) if kind!='rank' else bool(contexts) and contexts[0]['metadata']['region_id']==c['ids'][0]
  checks['period']= (a.get('resolved_start')==c['start'] and a.get('resolved_end')==c['end']) if kind=='trend' else a.get('resolved_month')==c['month']
  checks['source']=bool(result.get('sources')) and all('SHA-256' in s for s in result.get('sources',[]))
  if kind=='missing':
   checks['missing_not_zero']=any(w in answer for w in ['없','결측','제공되지','알 수']) and not amounts
  else:
   # 가격은 음수가 아니므로 절댓값으로 비교합니다 ('2016년 8월 - 558,017천원'의 구분 기호를 부호로 읽지 않도록)
   checks['answer_prices']=all(Decimal(v) in {abs(a) for a in amounts} for v in c['expected_prices'])
   if kind=='compare':checks['answer_difference']=Decimal(c['expected_difference']) in {abs(v) for v in amounts}
   if kind=='trend':
    rates=[float(v) for v in re.findall(r'([+-]?\d+(?:\.\d+)?)%',flat)]
    checks['answer_rate']=any(abs(v-c['expected_rate'])<=0.011 for v in rates)
   if kind=='rank':checks['all_candidates']=a.get('aggregation',{}).get('candidates_count')==c['candidate_count']
 r['checks']=checks;r['automatic_pass']=all(checks.values());return r

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--include-outside',action='store_true',help='범위 밖 기간 및 미래 예측 거부 평가도 포함')
 args=parser.parse_args()
 selected_cases=[c for c in cases if args.include_outside or (c['kind']!='outside' and '내년' not in c['question'])]
 for c in selected_cases:
  if c['kind'] not in {'outside','unsupported'}:
   assert all(EVALUATION_START <= m <= EVALUATION_END for m in (c['month'],c['start'],c['end']) if m), c['id']
 output=ROOT/'evaluations'/('accuracy_results_'+time.strftime('%Y%m%d_%H%M%S')+'.json')
 print(f'평가 기간: {EVALUATION_START} ~ {EVALUATION_END}, 문항: {len(selected_cases)}, 범위 밖 거부 평가: {args.include_outside}',flush=True)
 records=[]
 with ThreadPoolExecutor(max_workers=3) as pool:
  futures=[pool.submit(evaluate,c) for c in selected_cases]
  for future in as_completed(futures):
   r=future.result();records.append(r)
   output.write_text(json.dumps(sorted(records,key=lambda x:x['id']),ensure_ascii=False,indent=2),encoding='utf-8')
   print(f"{r['id']:02d} {r['kind']} pass={r['automatic_pass']} seconds={r['elapsed_seconds']} failed={[k for k,v in r['checks'].items() if not v]}",flush=True)
 print('TOTAL',len(records),'AUTO_PASS',sum(r['automatic_pass'] for r in records),flush=True)
