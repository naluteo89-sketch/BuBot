export function status(element, data) {
  const ready = data.key_ready && data.index_ready;
  element.textContent = ready ? '질문할 준비가 되었어요' : '일부 조회 기능을 준비 중이에요';
  element.classList.toggle('unavailable', !ready);
}
