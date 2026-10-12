import {request} from './api.js';
import {message} from './components/message.js';
import {composer} from './components/composer.js';
import {suggestions} from './components/suggestions.js';
import {status} from './components/status.js';

const messages = document.querySelector('#messages');
const reset = document.querySelector('#reset');
const examples = document.querySelector('.examples');
const announcement = document.querySelector('#announcement');
const newSession = () => crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
const motion = () => matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
const qa = document.querySelector('#qa-section');
const feedback = document.querySelector('#query-feedback');
let sessionId = newSession();
let pendingRequest = false;

const input = composer(async (question, unit) => {
  pendingRequest = true;
  input.busy(true);
  reset.disabled = true;
  examples.querySelectorAll('button').forEach(button => button.disabled = true);
  qa.hidden = false;
  const pair = document.createElement('div');
  pair.className = 'qa-pair';
  pair.setAttribute('role', 'group');
  pair.setAttribute('aria-label', `질문 ${messages.children.length + 1}`);
  pair.append(message('user', question));
  const pending = message('assistant', '데이터를 확인하고 있어요. 잠시만 기다려 주세요.');
  pending.classList.add('pending');
  pair.append(pending);
  messages.append(pair);
  messages.setAttribute('aria-busy', 'true');
  feedback.textContent = '입력창 아래에서 답변을 준비하고 있어요.';
  try {
    const data = await request('/api/chat', {question, unit, session_id: sessionId});
    pending.replaceWith(message('assistant', data.answer, data));
    if (data.warning) pair.append(message('error', '답변은 생성되었지만 기록을 저장하지 못했어요. 필요한 내용을 복사해 주세요.'));
    input.clear();
    feedback.textContent = '아래에 답변이 준비됐어요. 이어서 질문할 수도 있어요.';
  } catch (error) {
    pending.replaceWith(message('error', error.message));
    feedback.textContent = '답변을 불러오지 못했어요. 아래 안내를 확인하고 다시 시도해 주세요.';
  } finally {
    pendingRequest = false;
    input.busy(false);
    reset.disabled = false;
    examples.querySelectorAll('button').forEach(button => button.disabled = false);
    messages.setAttribute('aria-busy', 'false');
  }
});
document.querySelector('#composer').append(input.element);
document.querySelector('#suggestions').append(suggestions(text => {
  if (!pendingRequest) input.fill(text);
}));
reset.addEventListener('click', () => {
  if (pendingRequest) return;
  const old = sessionId;
  sessionId = newSession();
  request('/api/reset', {session_id: old}).catch(() => {});
  messages.replaceChildren();
  qa.hidden = true;
  feedback.textContent = '지역과 기간을 함께 입력하면 더 정확하게 확인할 수 있어요.';
  input.clear();
  input.focus();
  announcement.textContent = '새 대화를 시작했어요. 궁금한 지역을 입력해 주세요.';
  window.scrollTo({top: 0, behavior: motion()});
});
document.querySelector('#ask-again').addEventListener('click', () => {
  input.focus();
  input.element.scrollIntoView({behavior: motion(), block: 'center'});
});
const fill = (id, text) => { if (text) document.getElementById(id).textContent = text; };
request('/api/status').then(data => {
  status(document.querySelector('#status'), data);
  fill('period', /^\d{4}-\d{2}\s*~\s*\d{4}-\d{2}$/.test(data.period || '') ? data.period.replace(/(\d{4})-(\d{2})/g, '$1.$2') : '기간 정보를 확인할 수 없어요');
}).catch(() => {
  fill('status', '연결을 확인해 주세요');
  document.querySelector('#status').classList.add('unavailable');
  fill('period', '기간 정보를 불러오지 못했어요');
});
