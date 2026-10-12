export function suggestions(fill) {
  const container = document.createElement('div');
  container.className = 'suggestions';
  const examples = [
    ['01', '지역 가격', '서울의 평균 매매가는?', '서울의 가장 최근 데이터 기준 평균 매매가격은?'],
    ['02', '지역 비교', '강남과 서초, 얼마나 다를까?', '가장 최근 데이터 기준 강남구와 서초구 평균 매매가격과 차이를 알려줘'],
    ['03', '가격 변화', '부산의 가격은 어떻게 변했을까?', '부산의 데이터에 있는 최근 3개월 평균 매매가격 추이를 알려줘'],
  ];
  for (const [number, title, description, question] of examples) {
    const button = document.createElement('button');
    button.type = 'button';
    button.innerHTML = `<span class="suggestion-number">${number}</span><strong>${title}</strong><span class="suggestion-description">${description}</span><span class="suggestion-arrow" aria-hidden="true">↗</span>`;
    button.addEventListener('click', () => fill(question));
    container.append(button);
  }
  return container;
}
