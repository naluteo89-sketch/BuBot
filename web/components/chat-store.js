// 진행 중인 대화 보관소. '새 대화'를 누르기 전까지 페이지를 옮기거나 브라우저를 다시 열어도 유지됩니다.
// 이 기기·브라우저에만 저장됩니다. 저장이 막힌 환경(사생활 보호 모드 등)에서도 화면은 동작합니다.
import {request} from '../api.js';
import {EXAMPLE_LABELS} from './suggestions.js';

const KEY = 'bubot.chat';

export const newSession = () => crypto.randomUUID ? crypto.randomUUID()
  : Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');

export function loadChat() {
  try {
    const value = JSON.parse(localStorage.getItem(KEY));
    if (value && typeof value.sessionId === 'string' && Array.isArray(value.turns)) return value;
  } catch { /* 저장 불가 또는 손상: 새 대화로 시작 */ }
  return {sessionId: newSession(), turns: []};
}

export function saveChat(state) {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* 저장 불가 환경은 무시 */ }
  showChatCount(state);
}

function clearChat() {
  try { localStorage.removeItem(KEY); } catch { /* 무시 */ }
  showChatCount({turns: []});
}

// 대화 끝내기: 서버의 대화 기억을 지우고(실패해도 무시) 이 브라우저에 저장된 대화도 지웁니다.
export function endChat(sessionId = loadChat().sessionId) {
  request('api/reset', {session_id: sessionId}).catch(() => {});
  clearChat();
}

const DEFAULT_NAV = '진행 중인 대화';
const DEFAULT_HEADING = '아파트 평균 매매가 조회';
const clip = (text, size) => (text.length > size ? text.slice(0, size).trimEnd() + '…' : text);

// 대화 제목 = 사용자가 처음 한 질문. 첫 질문이 첫 화면 예시라면 누구나 같은 제목이 되므로
// 예시의 짧은 이름 뒤에 '+ 다음 질문'을 조금 붙여 구분합니다.
export function chatTitle(turns) {
  if (!turns.length) return '';
  const first = turns[0].question;
  const label = EXAMPLE_LABELS.get(first);
  if (!label) return first;
  return turns[1] ? `${label} + ${clip(turns[1].question, 14)}` : label;
}

// 사이드바 메뉴 이름·질문 수와 대화 화면 머리글을 현재 대화로 맞춥니다.
export function showChatCount(state = loadChat()) {
  const count = state.turns.length;
  const title = chatTitle(state.turns);
  const badge = document.querySelector('#chat-count');
  if (badge) {
    badge.textContent = count;
    badge.hidden = !count;
    badge.setAttribute('aria-label', `질문 ${count}개`);
  }
  // 대화가 없으면 메뉴와 '진행 중인 대화로' 버튼을 숨깁니다 (서버가 만든 HTML도 기본은 숨김).
  document.querySelectorAll('#chat-row, #history-chat-link').forEach(link => { link.hidden = !count; });
  const navTitle = document.querySelector('#chat-title');
  if (navTitle) {
    navTitle.textContent = title || DEFAULT_NAV;
    navTitle.classList.toggle('is-question', Boolean(title));
    navTitle.closest('a').title = title ? `진행 중인 대화: ${title}` : DEFAULT_NAV;
  }
  const heading = document.querySelector('#chat-heading');
  if (heading) {
    heading.textContent = title || DEFAULT_HEADING;
    heading.title = title;
    heading.classList.toggle('is-question', Boolean(title));
  }
}

// 다른 탭에서 질문하거나 새 대화를 시작하면 이 탭의 사이드바도 맞춥니다.
window.addEventListener('storage', event => { if (event.key === KEY || event.key === null) showChatCount(); });
