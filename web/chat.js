// 대화 화면: 질문과 답변이 위에서 아래로 쌓이고, 입력창은 항상 화면 아래에 고정됩니다.
import {request} from './api.js';
import {message} from './components/message.js';
import {composer} from './components/composer.js';
import {status} from './components/status.js';

const STORE = 'bubot.chat';
const log = document.querySelector('#messages');
const empty = document.querySelector('#chat-empty');
const reset = document.querySelector('#reset');
const feedback = document.querySelector('#query-feedback');
const announcement = document.querySelector('#announcement');
const motion = () => matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
const newSession = () => crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');

// 새로고침해도 이 탭의 대화가 남도록 sessionStorage에 보관합니다 (탭을 닫으면 사라짐, 실패해도 화면은 동작).
function load() {
  try { return JSON.parse(sessionStorage.getItem(STORE)) || null; } catch { return null; }
}
function save() {
  try { sessionStorage.setItem(STORE, JSON.stringify(state)); } catch { /* 저장 불가 환경은 무시 */ }
}
function clearStore() {
  try { sessionStorage.removeItem(STORE); } catch { /* 무시 */ }
}

let state = load() || {sessionId: newSession(), turns: []};
let pendingRequest = false;

function scrollToEnd(behavior = motion()) {
  requestAnimationFrame(() => window.scrollTo({top: document.documentElement.scrollHeight, behavior}));
}
function updateEmpty() {
  empty.hidden = log.children.length > 0;
}
function turnElement(question) {
  const turn = document.createElement('div');
  turn.className = 'chat-turn';
  turn.setAttribute('role', 'group');
  turn.setAttribute('aria-label', `질문 ${log.children.length + 1}`);
  turn.append(message('user', question));
  log.append(turn);
  updateEmpty();
  return turn;
}
function renderTurn({question, answer, data, error, warning}) {
  const turn = turnElement(question);
  turn.append(error ? message('error', error) : message('assistant', answer, data || {}));
  if (warning) turn.append(message('error', warning));
}

async function send(question, unit) {
  pendingRequest = true;
  input.busy(true);
  reset.disabled = true;
  const turn = turnElement(question);
  const pending = message('assistant', '데이터를 확인하고 있어요. 잠시만 기다려 주세요.');
  pending.classList.add('pending');
  turn.append(pending);
  log.setAttribute('aria-busy', 'true');
  feedback.textContent = '답변을 준비하고 있어요.';
  scrollToEnd();
  const record = {question};
  try {
    const data = await request('api/chat', {question, unit, session_id: state.sessionId});
    pending.replaceWith(message('assistant', data.answer, data));
    record.answer = data.answer;
    record.data = data;
    if (data.warning) {
      record.warning = '답변은 생성되었지만 기록을 저장하지 못했어요. 필요한 내용을 복사해 주세요.';
      turn.append(message('error', record.warning));
    }
    input.clear();
    feedback.textContent = '이전 질문에 이어서 물어볼 수 있어요.';
  } catch (error) {
    pending.replaceWith(message('error', error.message));
    record.error = error.message;
    feedback.textContent = '답변을 불러오지 못했어요. 위 안내를 확인하고 다시 시도해 주세요.';
  } finally {
    state.turns.push(record);
    save();
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
  clearStore();
  location.href = './';
});

// 첫 화면에서 넘어온 질문(?q=)은 새 대화로 시작합니다. 주소에서 지워 새로고침 시 다시 보내지 않습니다.
const params = new URLSearchParams(location.search);
const firstQuestion = (params.get('q') || '').trim();
if (firstQuestion) {
  if (state.turns.length) request('api/reset', {session_id: state.sessionId}).catch(() => {});
  state = {sessionId: newSession(), turns: []};
  save();
  history.replaceState(null, '', location.pathname);
  const unit = params.get('unit') || '';
  const select = input.element.querySelector('#unit');
  if (unit && [...select.options].some(option => option.value === unit)) select.value = unit;
  updateEmpty();
  send(firstQuestion.slice(0, 1000), unit);
} else {
  state.turns.forEach(renderTurn);
  updateEmpty();
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
