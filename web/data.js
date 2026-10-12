import {request} from './api.js';
import {status} from './components/status.js';
const fill = (id, text) => {document.getElementById(id).textContent = text;};
request('api/status').then(data => {
  status(document.querySelector('#status'), data);
  fill('period', /^\d{4}-\d{2}\s*~\s*\d{4}-\d{2}$/.test(data.period || '') ? data.period.replace(/(\d{4})-(\d{2})/g, '$1.$2') : '기간 정보를 확인할 수 없어요');
  fill('source', data.source || '출처 정보를 확인할 수 없어요.');
  fill('coverage', data.coverage_note || '지역별 데이터 범위를 확인할 수 없어요.');
}).catch(() => {
  fill('status', '연결을 확인해 주세요');
  document.querySelector('#status').classList.add('unavailable');
  fill('period', '기간 정보를 불러오지 못했어요');
  fill('source', '출처 정보를 불러오지 못했어요. 잠시 후 새로고침해 주세요.');
  fill('coverage', '지역별 데이터 범위를 불러오지 못했어요.');
});
