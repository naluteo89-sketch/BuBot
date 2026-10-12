function section(title, items) {
  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = title;
  const list = document.createElement('ul');
  for (const item of items) {
    const li = document.createElement('li');
    li.textContent = item;
    list.append(li);
  }
  details.append(summary, list);
  return details;
}
export function message(role, text, data = {}) {
  const card = document.createElement('article');
  card.className = `message ${role}`;
  const label = document.createElement('div');
  label.className = 'message-label';
  label.textContent = role === 'user' ? '나의 질문' : role === 'error' ? '잠깐 확인해 주세요' : 'BuBot의 답변';
  const body = document.createElement('div');
  body.className = 'message-body';
  body.textContent = text;
  card.append(label);
  if ((data.input_converted || data.followup) && data.standalone_question) {
    const note = document.createElement('div');
    note.className = 'message-route';
    note.textContent = `${data.input_converted ? '입력한 질문을 이렇게 이해했어요' : '이전 질문에 이어 확인했어요'}: ${data.standalone_question}`;
    card.append(note);
  }
  card.append(body);
  if (data.cached) {
    const note = document.createElement('div');
    note.className = 'message-route';
    note.textContent = '같은 질문에 대한 이전 답변을 그대로 보여드려요.';
    card.append(note);
  }
  if (data.facts?.length || data.sources?.length) {
    const extra = document.createElement('details');
    extra.className = 'answer-details';
    const summary = document.createElement('summary');
    summary.textContent = '계산 근거와 출처 확인';
    extra.append(summary);
    if (data.facts?.length) extra.append(section('계산에 사용한 데이터', data.facts));
    if (data.sources?.length) extra.append(section('데이터 출처', data.sources));
    card.append(extra);
  }
  return card;
}
