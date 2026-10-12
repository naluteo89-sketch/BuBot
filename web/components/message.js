function section(title, items) {
  const details = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = title; details.append(summary);
  const list = document.createElement('ul');
  for (const item of items) { const li = document.createElement('li'); li.textContent = item; list.append(li); }
  details.append(list); return details;
}
function timing(data) {
  const stages = Object.entries(data.timings || {}).map(([stage, seconds]) => `${stage} ${seconds}초`);
  if (data.elapsed_seconds == null && !stages.length) return '';
  const total = data.elapsed_seconds == null ? '처리 시간' : `처리 시간 ${data.elapsed_seconds}초`;
  return stages.length ? `${total} (${stages.join(' · ')})` : total;
}
export function message(role, text, data = {}) {
  const card = document.createElement('article'); card.className = `message ${role}`;
  const label = document.createElement('div'); label.className = 'message-label'; label.textContent = role === 'user' ? '나의 질문' : role === 'error' ? '처리 안내' : 'BuBot';
  const body = document.createElement('div'); body.className = 'message-body'; body.textContent = text;
  card.append(label);
  if (data.input_converted && data.standalone_question) { const note = document.createElement('div'); note.className = 'message-route'; note.textContent = `한/영 전환 없이 입력된 질문을 한글로 바꿔 해석했습니다: ${data.standalone_question}`; card.append(note); }
  else if (data.followup && data.standalone_question) { const note = document.createElement('div'); note.className = 'message-route'; note.textContent = `이전 대화를 반영해 해석한 질문: ${data.standalone_question}`; card.append(note); }
  card.append(body);
  const extra=document.createElement('details');extra.className='answer-details';
  const summary=document.createElement('summary');summary.textContent='답변 상세 보기';extra.append(summary);
  const routes = {numeric: '처리 경로: 원본 수치 계산 + 지식 검색 보강', explain: '처리 경로: 지식 문서 검색(RAG) 기반 답변',
    rule: '처리 경로: 원본 직접 집계·안내 (AI 답변 생성 없음)', clarify: '처리 경로: 확인 질문·범위 안내'};
  if (data.memory_turns) { const memo = document.createElement('div'); memo.className = 'message-route'; memo.textContent = `대화 메모리: 최근 ${data.memory_turns}개 질문·답변 기억 중`; extra.append(memo); }
  if (routes[data.route]) { const route = document.createElement('div'); route.className = 'message-route'; route.textContent = routes[data.route]; extra.append(route); }
  const elapsed = role === 'assistant' ? timing(data) : '';
  if (elapsed) { const time = document.createElement('div'); time.className = 'message-route'; time.textContent = elapsed; extra.append(time); }
  if (data.retrieved?.length) extra.append(section(`검색된 지식 문서 ${data.retrieved.length}개`, data.retrieved));
  if (data.facts?.length) extra.append(section('원본 데이터 계산', data.facts));
  if (data.sources?.length) extra.append(section(`출처 ${data.sources.length}개 확인`, data.sources));
  if(extra.children.length>1)card.append(extra);
  return card;
}
