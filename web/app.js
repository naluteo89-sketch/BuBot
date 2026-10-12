// 첫 화면: 질문을 입력하면 대화 화면(chat.html)으로 이동해 새 대화를 시작합니다.
import {request} from './api.js';
import {composer} from './components/composer.js';
import {suggestions} from './components/suggestions.js';
import {status} from './components/status.js';

const input = composer((question, unit) => {
  const params = new URLSearchParams({q: question});
  if (unit) params.set('unit', unit);
  location.href = `chat.html?${params}`;
});
document.querySelector('#composer').append(input.element);
document.querySelector('#suggestions').append(suggestions(text => input.fill(text)));

const fill = (id, text) => { if (text) document.getElementById(id).textContent = text; };
request('api/status').then(data => {
  status(document.querySelector('#status'), data);
  fill('period', /^\d{4}-\d{2}\s*~\s*\d{4}-\d{2}$/.test(data.period || '') ? data.period.replace(/(\d{4})-(\d{2})/g, '$1.$2') : '기간 정보를 확인할 수 없어요');
}).catch(() => {
  fill('status', '연결을 확인해 주세요');
  document.querySelector('#status').classList.add('unavailable');
  fill('period', '기간 정보를 불러오지 못했어요');
});
