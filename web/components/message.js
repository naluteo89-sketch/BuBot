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

// 금액(억·만·천원)·비율(%)·배수를 굵게 표시합니다. innerHTML 없이 텍스트 노드로만 만듭니다.
const FIGURE = /([+-]?\d[\d,]*억(?:\s?\d[\d,]*만)?(?:\s?\d[\d,]*)?원|[+-]?\d[\d,]*만(?:\s?\d[\d,]*)?원|[+-]?\d[\d,]*천원|[+-]?\d+(?:\.\d+)?%|\d+(?:\.\d+)?배)/g;
const WARNING_MARK = '[수치 확인 필요]';

function emphasize(parent, text) {
  let last = 0;
  for (const match of text.matchAll(FIGURE)) {
    if (match.index > last) parent.append(text.slice(last, match.index));
    const strong = document.createElement('strong');
    strong.className = 'figure';
    strong.textContent = match[0];
    parent.append(strong);
    last = match.index + match[0].length;
  }
  if (last < text.length) parent.append(text.slice(last));
}

// '서울 / 강남지역 / 동남권 / 강남구' → '서울 강남구'. 같은 이름 지역(중구 등)이 섞이지 않게 시도는 남깁니다.
const PATH = /([^\s/:]+)(?: \/ [^\s/:]+)* \/ ([^\s/:]+)/g;
const shortPath = text => text.replace(PATH, '$1 $2');

// 근거 수치 목록 한 줄: "지역·월: 값" 이면 이름과 값을 나눠 값이 먼저 눈에 들어오게 합니다.
function factItem(line) {
  const li = document.createElement('li');
  li.title = line;
  line = shortPath(line);
  const split = line.match(/^(.+?):\s*([^:]*\d[^:]*)$/);
  if (split) {
    const label = document.createElement('span');
    label.className = 'fact-label';
    label.textContent = split[1];
    const value = document.createElement('span');
    value.className = 'fact-value';
    emphasize(value, split[2]);
    li.append(label, value);
  } else {
    li.className = 'fact-note';
    emphasize(li, line);
  }
  return li;
}

// 답변 형식(요약 문단 → '- ' 근거 목록 → 보충 문단)을 위계가 보이는 문서로 바꿉니다.
// 답변 속 '- ' 목록이 '계산 근거'와 같은 내용이고 짧으면(5줄 이하) 본문에서 뺍니다. 순위처럼 긴 목록은 남깁니다.
const SHORT_LIST = 5;
const sameAsFacts = (lines, facts) => facts.length > 0 && lines.length <= SHORT_LIST
  && lines.every(line => facts.includes(line.slice(2).trim()));

function richBody(text, facts = []) {
  const body = document.createElement('div');
  body.className = 'message-body rich';
  const paragraphs = text.trim().split(/\n\s*\n/).filter(Boolean);
  paragraphs.forEach((paragraph, index) => {
    const lines = paragraph.split('\n').map(line => line.trim()).filter(Boolean);
    if (lines.length && lines.every(line => line.startsWith('- '))) {
      if (sameAsFacts(lines, facts)) return;
      const list = document.createElement('ul');
      list.className = 'facts';
      lines.forEach(line => list.append(factItem(line.slice(2))));
      body.append(list);
      return;
    }
    const p = document.createElement('p');
    if (index === 0) p.className = 'lead';
    lines.forEach((line, i) => {
      if (i) p.append(document.createElement('br'));
      emphasize(p, line);
    });
    body.append(p);
  });
  return body;
}

// 서버가 붙인 "[수치 확인 필요] ... 근거와 일치하지 않습니다: 99.41% (근거에 없는 값) ..." 를 안내 상자로 바꿉니다.
function checkNotice(raw) {
  const box = document.createElement('div');
  box.className = 'answer-check';
  box.setAttribute('role', 'note');
  const title = document.createElement('strong');
  title.textContent = '일부 수치는 한 번 더 확인해 주세요';
  const text = document.createElement('p');
  text.textContent = "답변 속 수치 중 계산 근거와 다르게 표현된 값이 있을 수 있어요. 아래 '계산 근거와 출처 확인'의 원본 값을 기준으로 봐 주세요.";
  box.append(title, text);
  const figures = (raw.match(/(?:확인할 표현|일치하지 않습니다):\s*(.+?)\.\s/) || [])[1];
  if (figures) {
    const detail = document.createElement('small');
    detail.textContent = `확인이 필요한 표현: ${figures}`;
    box.append(detail);
  }
  return box;
}

export function message(role, text, data = {}) {
  const card = document.createElement('article');
  card.className = `message ${role}`;
  const label = document.createElement('div');
  label.className = 'message-label';
  label.textContent = role === 'user' ? '나의 질문' : role === 'error' ? '잠깐 확인해 주세요' : 'BuBot의 답변';
  card.append(label);
  if ((data.input_converted || data.followup) && data.standalone_question) {
    const note = document.createElement('div');
    note.className = 'message-route';
    note.textContent = `${data.input_converted ? '입력한 질문을 이렇게 이해했어요' : '이전 질문에 이어 확인했어요'}: ${data.standalone_question}`;
    card.append(note);
  }
  let notice = null;
  if (role === 'assistant') {
    const at = text.indexOf(WARNING_MARK);
    const main = at >= 0 ? text.slice(0, at) : text;
    if (at >= 0) notice = checkNotice(text.slice(at));
    card.append(richBody(main, (data.facts || []).map(fact => String(fact).trim())));
  } else {
    const body = document.createElement('div');
    body.className = 'message-body';
    body.textContent = text;
    card.append(body);
  }
  if (notice) card.append(notice);
  if (data.cached) {
    const note = document.createElement('div');
    note.className = 'message-route';
    note.textContent = '같은 질문에 대한 이전 답변을 그대로 보여드려요.';
    card.append(note);
  }
  // 답변마다 같은 형식의 한 줄 출처 (서버 source_note). 자세한 행·해시는 아래 '계산 근거와 출처 확인'에 있습니다.
  if (role === 'assistant' && data.source_note) {
    const source = document.createElement('p');
    source.className = 'source-note';
    source.textContent = data.source_note;
    card.append(source);
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
