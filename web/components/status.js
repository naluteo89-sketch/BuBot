import {request} from '../api.js';

export function status(element, data) {
  const ready = data.key_ready && data.index_ready;
  element.textContent = ready ? '질문할 준비가 되었어요' : '일부 조회 기능을 준비 중이에요';
  element.classList.toggle('unavailable', !ready);
}

// 서버 상태(api/status)를 불러와 준비 상태 문구(#status)를 표시하고 상태를 돌려줍니다. 실패하면 null.
export async function loadStatus() {
  const element = document.querySelector('#status');
  try {
    const data = await request('api/status');
    status(element, data);
    return data;
  } catch {
    element.textContent = '연결을 확인해 주세요';
    element.classList.add('unavailable');
    return null;
  }
}

// 보유 기간 '2012-01 ~ 2026-08' → '2012.01 ~ 2026.08'
export function periodText(data) {
  if (!data) return '기간 정보를 불러오지 못했어요';
  return /^\d{4}-\d{2}\s*~\s*\d{4}-\d{2}$/.test(data.period || '') ? data.period.replace(/(\d{4})-(\d{2})/g, '$1.$2') : '기간 정보를 확인할 수 없어요';
}
