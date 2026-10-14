// 첫 화면: 질문을 입력하면 대화 화면(chat.html)으로 이동합니다. 진행 중인 대화가 있으면 그 대화에 이어집니다.
import './components/sidebar.js';
import {composer} from './components/composer.js';
import {suggestions} from './components/suggestions.js';
import {loadStatus, periodText} from './components/status.js';
import {loadChat, endChat, showChatCount, chatTitle} from './components/chat-store.js';

const input = composer((question, unit) => {
  const params = new URLSearchParams({q: question});
  if (unit) params.set('unit', unit);
  location.href = `chat.html?${params}`;
});
document.querySelector('#composer').append(input.element);

// 진행 중인 대화 안내: 이어 보기 또는 새로 시작
const resume = document.querySelector('#chat-resume');
const feedback = document.querySelector('#query-feedback');
function showResume() {
  const {turns} = loadChat();
  const count = turns.length;
  const title = document.querySelector('#chat-resume-title');
  title.textContent = chatTitle(turns) || '진행 중인 대화';
  title.title = title.textContent;
  showChatCount();
  resume.hidden = !count;
  document.querySelector('#chat-resume-count').textContent = count;
  feedback.textContent = count ? '질문하면 진행 중인 대화에 이어서 답해 드려요.' : '질문하면 대화 화면으로 이동해 답변을 보여 드려요.';
}
document.querySelector('#start-new').addEventListener('click', () => {
  endChat();
  showResume();
  input.focus();
});
showResume();
document.querySelector('#suggestions').append(suggestions(text => input.fill(text)));

loadStatus().then(data => { document.getElementById('period').textContent = periodText(data); });
