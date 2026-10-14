import './components/sidebar.js';
import {loadStatus, periodText} from './components/status.js';
import {showChatCount} from './components/chat-store.js';
showChatCount();
const fill = (id, text) => {document.getElementById(id).textContent = text;};
loadStatus().then(data => {
  fill('period', periodText(data));
  if (data) {
    fill('source', data.source || '출처 정보를 확인할 수 없어요.');
    fill('coverage', data.coverage_note || '지역별 데이터 범위를 확인할 수 없어요.');
  } else {
    fill('source', '출처 정보를 불러오지 못했어요. 잠시 후 새로고침해 주세요.');
    fill('coverage', '지역별 데이터 범위를 불러오지 못했어요.');
  }
});
