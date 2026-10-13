// 사이드바: 진행 중인 대화 메뉴(☰)와 너비 조절. 모든 페이지가 불러옵니다.
import {request} from '../api.js';
import {loadChat, clearChat, chatTitle} from './chat-store.js';
import {copyToClipboard, transcriptText} from './clipboard.js';

const onChatPage = () => location.pathname.endsWith('/chat.html');

// ── 대화 메뉴 ─────────────────────────────────────
function setupMenu() {
  const button = document.querySelector('#chat-menu-button');
  const menu = document.querySelector('#chat-menu');
  if (!button || !menu) return;
  const items = () => [...menu.querySelectorAll('[role="menuitem"]')];
  const open = () => { menu.hidden = false; button.setAttribute('aria-expanded', 'true'); items()[0]?.focus(); };
  const close = (focusButton = false) => {
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    resetCloseItem();
    if (focusButton) button.focus();
  };
  button.addEventListener('click', event => { event.stopPropagation(); menu.hidden ? open() : close(); });
  document.addEventListener('click', event => { if (!menu.hidden && !menu.contains(event.target)) close(); });
  menu.addEventListener('keydown', event => {
    const list = items();
    const index = list.indexOf(document.activeElement);
    if (event.key === 'Escape') { event.preventDefault(); close(true); }
    if (event.key === 'ArrowDown') { event.preventDefault(); list[(index + 1) % list.length].focus(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); list[(index - 1 + list.length) % list.length].focus(); }
  });

  const label = item => item.querySelector('.menu-label');
  const say = (item, text) => {
    const span = label(item);
    span.dataset.original ??= span.textContent;
    span.textContent = text;
    clearTimeout(item.timer);
    item.timer = setTimeout(() => { span.textContent = span.dataset.original; item.dataset.armed = ''; }, 2500);
  };
  const closeItem = menu.querySelector('[data-action="close"]');
  function resetCloseItem() {
    if (!closeItem) return;
    closeItem.dataset.armed = '';
    const span = label(closeItem);
    if (span.dataset.original) span.textContent = span.dataset.original;
  }

  menu.addEventListener('click', async event => {
    const item = event.target.closest('[role="menuitem"]');
    if (!item) return;
    event.stopPropagation();
    const state = loadChat();
    if (item.dataset.action === 'pdf') {
      close();
      // 인쇄 창에서 'PDF로 저장'을 고르면 PDF 파일로 저장·공유할 수 있습니다.
      if (onChatPage()) window.print();
      else location.href = '/chat.html?print=1';
    }
    if (item.dataset.action === 'copy') {
      const copied = await copyToClipboard(transcriptText(state.turns, chatTitle(state.turns)));
      say(item, copied ? '대화를 복사했어요' : '복사하지 못했어요');
    }
    if (item.dataset.action === 'close') {
      // 실수로 지우지 않도록 두 번 눌러야 닫힙니다.
      if (item.dataset.armed !== 'yes') {
        item.dataset.armed = 'yes';
        say(item, '한 번 더 누르면 대화가 지워져요');
        return;
      }
      request('api/reset', {session_id: state.sessionId}).catch(() => {});
      clearChat();
      close();
      if (onChatPage()) location.href = './';
    }
  });
}

// ── 사이드바 너비 조절 (PC 화면에서만) ─────────────────
const SIZE_KEY = 'bubot.sidebar';
// 최소 너비 = 이전 디자인의 기본 너비(넓은 화면 312.5px, 1375px 이하 272.5px). 그보다 좁게는 줄지 않습니다.
const minWidth = () => (window.innerWidth <= 1375 ? 272.5 : 312.5);
const MAX_WIDTH = 520;
const MAIN_MIN = 680;    // 대화·입력창이 줄바꿈 없이 보이도록 본문에 남길 최소 너비
const desktop = matchMedia('(min-width: 951px)');

function setupResizer() {
  const aside = document.querySelector('aside');
  if (!aside) return;
  const root = document.documentElement;
  const handle = document.createElement('div');
  handle.className = 'sidebar-resizer';
  handle.tabIndex = 0;
  handle.setAttribute('role', 'separator');
  handle.setAttribute('aria-orientation', 'vertical');
  handle.setAttribute('aria-label', '사이드바 너비 조절 (좌우 화살표, 두 번 클릭하면 기본값)');
  handle.title = '끌어서 너비 조절 · 두 번 클릭하면 기본값';
  aside.append(handle);

  const limit = () => Math.max(minWidth(), Math.min(MAX_WIDTH, window.innerWidth - MAIN_MIN));
  let saved = null;
  try { saved = Number(localStorage.getItem(SIZE_KEY)) || null; } catch { saved = null; }

  function apply(width, persist = false) {
    if (!width || !desktop.matches) {
      root.style.removeProperty('--sidebar-w');
      if (persist) { try { localStorage.removeItem(SIZE_KEY); } catch { /* 무시 */ } }
      return;
    }
    const value = Math.round(Math.min(limit(), Math.max(minWidth(), width)) * 10) / 10;
    root.style.setProperty('--sidebar-w', `${value}px`);
    handle.setAttribute('aria-valuemin', minWidth());
    handle.setAttribute('aria-valuemax', limit());
    handle.setAttribute('aria-valuenow', value);
    if (persist) { try { localStorage.setItem(SIZE_KEY, value); } catch { /* 무시 */ } }
  }
  apply(saved);
  window.addEventListener('resize', () => apply(saved));
  desktop.addEventListener('change', () => apply(saved));

  handle.addEventListener('pointerdown', event => {
    if (!desktop.matches) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    root.classList.add('is-resizing');
    const left = aside.getBoundingClientRect().left;
    const move = e => apply(e.clientX - left);
    const end = e => {
      handle.removeEventListener('pointermove', move);
      root.classList.remove('is-resizing');
      saved = Math.round(e.clientX - left);
      apply(saved, true);
      saved = Number(handle.getAttribute('aria-valuenow')) || saved;
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', end, {once: true});
    handle.addEventListener('pointercancel', end, {once: true});
  });
  handle.addEventListener('dblclick', () => { saved = null; apply(null, true); });
  handle.addEventListener('keydown', event => {
    const current = aside.getBoundingClientRect().width;
    const step = event.shiftKey ? 40 : 16;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      saved = current + (event.key === 'ArrowRight' ? step : -step);
      apply(saved, true);
    }
    if (event.key === 'Home') { event.preventDefault(); saved = null; apply(null, true); }
  });
}

// ── AI 연결 신호등 ─────────────────────────────────
// 초록: 정상, 노랑: 느림·한도·서버 오류, 빨강: 연결 끊김·키 문제. 30초마다 확인합니다 (서버가 20초 캐시).
function setupApiLight() {
  const light = document.querySelector('#api-light');
  if (!light) return;
  const text = light.querySelector('.api-light-text');
  const show = (level, message) => {
    light.dataset.level = level;
    text.textContent = message;
    light.title = message;
  };
  const check = () => {
    const timer = new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 15000));
    Promise.race([request('api/health'), timer])
      .then(data => show(data.level || 'red', data.message || 'AI 연결 상태를 알 수 없어요'))
      .catch(error => error.message === 'timeout'
        ? show('yellow', 'AI 연결 확인이 오래 걸려요')
        : show('red', '서버와 연결이 끊겼어요'));
  };
  check();
  setInterval(() => { if (!document.hidden) check(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
}

setupMenu();
setupResizer();
setupApiLight();
