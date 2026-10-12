// 대화 화면: 질문과 답변이 위에서 아래로 쌓이고, 입력창은 항상 화면 아래에 고정됩니다.
import {request} from './api.js';
import {message} from './components/message.js';
import {composer} from './components/composer.js';
import {status} from './components/status.js';
import {loadChat, saveChat, clearChat, showChatCount} from './components/chat-store.js';

const log = document.querySelector('#messages');
const empty = document.querySelector('#chat-empty');
const reset = document.querySelector('#reset');
const feedback = document.querySelector('#query-feedback');
const announcement = document.querySelector('#announcement');
const motion = () => matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';

// '새 대화'를 누르기 전까지 다른 페이지로 갔다 와도 대화가 유지됩니다 (components/chat-store.js).
let state = loadChat();
let pendingRequest = false;
// 답변을 기다리는 중에 페이지를 떠나면 요청이 끊깁니다. 그때는 오류로 저장하지 않고 '대기 중'으로 남깁니다.
let leaving = false;
window.addEventListener('pagehide', () => { leaving = true; });

function scrollToEnd(behavior = motion()) {
  requestAnimationFrame(() => window.scrollTo({top: document.documentElement.scrollHeight, behavior}));
}
function updateEmpty() {
  empty.hidden = log.children.length > 0;
}
function turnElement(question, unit = '') {
  const turn = document.createElement('div');
  turn.className = 'chat-turn';
  turn.setAttribute('role', 'group');
  turn.setAttribute('aria-label', `질문 ${log.children.length + 1}`);
  turn.append(message('user', question), questionActions(question, unit));
  log.append(turn);
  updateEmpty();
  return turn;
}
const LEFT_EARLY = "답변을 받기 전에 페이지를 옮겨 이 답변을 표시하지 못했어요. '지난 질문'에서 확인하거나 다시 질문해 주세요.";
const WAITING = '다른 화면에서 보낸 질문의 답변을 기다리고 있어요. 잠시 후 새로고침해 주세요.';
const STALE_MS = 120000;  // 답변 대기가 2분을 넘으면 페이지를 떠난 것으로 봅니다
const stale = turn => turn.pending && !(Date.now() - (turn.at || 0) < STALE_MS);
const MEMORY_RESET = '이전 대화 기억이 만료되어(2시간 경과 또는 서버 재시작) 이 질문은 앞 대화와 연결하지 않고 해석했어요.';

function renderTurn(record) {
  const {question, answer, data, error, warning, pending, note, at} = record;
  const turn = turnElement(question, record.unit || '');
  if (pending) turn.append(message('error', stale({pending, at}) ? LEFT_EARLY : WAITING));
  else turn.append(error ? message('error', error) : message('assistant', answer, data || {}));
  if (note) turn.append(message('error', note));
  if (warning) turn.append(message('error', warning));
  if (!pending && !error) turn.append(answerActions(record));
}

// ── 질문·답변 복사와 다시 질문 ─────────────────────────────
const CHECK_MARK = '[수치 확인 필요]';
function plainAnswer(text = '') {
  const at = text.indexOf(CHECK_MARK);
  if (at < 0) return text.trim();
  return text.slice(0, at).trim() + '\n\n※ 일부 수치는 계산 근거와 다를 수 있어요. 원본 값을 확인해 주세요.';
}
// 휴대폰에서 http://192.168.x.x 로 접속하면 보안 연결이 아니라 Clipboard API를 쓸 수 없어 예전 방식으로 복사합니다.
async function copyToClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* 아래 방식으로 다시 시도 */ }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.className = 'clipboard-proxy';
  document.body.append(area);
  area.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch { copied = false; }
  area.remove();
  return copied;
}
function actionButton(icon, label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'turn-action';
  button.innerHTML = `<span aria-hidden="true">${icon}</span><span class="turn-action-label"></span>`;
  button.querySelector('.turn-action-label').textContent = label;
  button.addEventListener('click', () => onClick(button));
  return button;
}
function flash(button, text) {
  const label = button.querySelector('.turn-action-label');
  const original = label.dataset.original || label.textContent;
  label.dataset.original = original;
  label.textContent = text;
  announcement.textContent = text;
  clearTimeout(button.flashTimer);
  button.flashTimer = setTimeout(() => { label.textContent = original; }, 1600);
}
// 내 질문 아래: 질문 복사 · 같은 질문 다시 보내기
function questionActions(question, unit) {
  const bar = document.createElement('div');
  bar.className = 'turn-actions question-actions';
  bar.append(actionButton('⧉', '복사', async button => {
    flash(button, await copyToClipboard(question) ? '복사했어요' : '복사하지 못했어요');
  }));
  bar.append(actionButton('↻', '다시 질문', () => {
    if (pendingRequest) return;
    send(question, unit);
  }));
  return bar;
}
// 답변 아래: 답변 복사
function answerActions(record) {
  const bar = document.createElement('div');
  bar.className = 'turn-actions answer-actions';
  bar.append(actionButton('⧉', '답변 복사', async button => {
    flash(button, await copyToClipboard(plainAnswer(record.answer)) ? '복사했어요' : '복사하지 못했어요');
  }));
  return bar;
}
// 다른 탭에서 같은 대화에 질문했을 수 있으므로 저장 직전의 최신 기록에 이어 붙입니다.
function commit(update) {
  const latest = loadChat();
  if (latest.sessionId === state.sessionId) state.turns = latest.turns;
  update(state.turns);
  saveChat(state);
}

async function send(question, unit) {
  pendingRequest = true;
  input.busy(true);
  reset.disabled = true;
  const turn = turnElement(question, unit);
  const hadHistory = state.turns.length > 0;
  // 답변 전에 페이지를 옮겨도 질문이 남도록 '대기 중'으로 먼저 저장합니다.
  const record = {question, unit, pending: true, at: Date.now()};
  commit(turns => turns.push(record));
  const pending = message('assistant', '데이터를 확인하고 있어요. 잠시만 기다려 주세요.');
  pending.classList.add('pending');
  turn.append(pending);
  log.setAttribute('aria-busy', 'true');
  feedback.textContent = '답변을 준비하고 있어요.';
  scrollToEnd();
  try {
    const data = await request('api/chat', {question, unit, session_id: state.sessionId});
    pending.replaceWith(message('assistant', data.answer, data));
    record.answer = data.answer;
    record.data = data;
    if (hadHistory && data.memory_used === 0) {
      record.note = MEMORY_RESET;
      turn.append(message('error', MEMORY_RESET));
    }
    if (data.warning) {
      record.warning = '답변은 생성되었지만 기록을 저장하지 못했어요. 필요한 내용을 복사해 주세요.';
      turn.append(message('error', record.warning));
    }
    input.clear();
    feedback.textContent = '이전 질문에 이어서 물어볼 수 있어요.';
  } catch (error) {
    if (leaving) return;
    pending.replaceWith(message('error', error.message));
    record.error = error.message;
    feedback.textContent = '답변을 불러오지 못했어요. 위 안내를 확인하고 다시 시도해 주세요.';
  } finally {
    if (leaving) return;  // eslint-disable-line no-unsafe-finally
    if (!record.error) turn.append(answerActions(record));
    delete record.pending;
    delete record.at;
    commit(turns => {
      const index = turns.findIndex(item => (item.pending || item.error === LEFT_EARLY) && item.question === record.question);
      if (index >= 0) turns[index] = record; else turns.push(record);
    });
    pendingRequest = false;
    input.busy(false);
    reset.disabled = false;
    log.setAttribute('aria-busy', 'false');
    scrollToEnd();
  }
}

const input = composer((question, unit) => { if (!pendingRequest) send(question, unit); },
  {placeholder: '이어서 궁금한 내용을 입력하세요'});
document.querySelector('#composer').append(input.element);

reset.addEventListener('click', () => {
  if (pendingRequest) return;
  request('api/reset', {session_id: state.sessionId}).catch(() => {});
  clearChat();
  location.href = './';
});

// 첫 화면에서 넘어온 질문(?q=)은 진행 중인 대화에 이어서 보냅니다. 주소에서 지워 새로고침 시 다시 보내지 않습니다.
const params = new URLSearchParams(location.search);
const firstQuestion = (params.get('q') || '').trim();
state.turns.forEach(renderTurn);
if (state.turns.some(stale)) {
  state.turns = state.turns.map(turn => (stale(turn) ? {question: turn.question, error: LEFT_EARLY} : turn));
  saveChat(state);
}
updateEmpty();
showChatCount(state);
if (firstQuestion) {
  history.replaceState(null, '', location.pathname);
  const unit = params.get('unit') || '';
  const select = input.element.querySelector('#unit');
  if (unit && [...select.options].some(option => option.value === unit)) select.value = unit;
  send(firstQuestion.slice(0, 1000), unit);
} else {
  if (state.turns.length) {
    scrollToEnd('instant');
    announcement.textContent = `이전 대화 ${state.turns.length}개를 불러왔어요.`;
  }
  input.focus();
}

request('api/status').then(data => status(document.querySelector('#status'), data)).catch(() => {
  const element = document.querySelector('#status');
  element.textContent = '연결을 확인해 주세요';
  element.classList.add('unavailable');
});
