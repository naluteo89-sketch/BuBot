// 첫 화면 예시 질문. [번호, 제목, 짧은 설명, 실제로 보내는 질문]
export const EXAMPLES = [
  ['01', '지역 가격', '서울의 평균 매매가는?', '서울의 가장 최근 데이터 기준 평균 매매가격은?'],
  ['02', '지역 비교', '강남과 서초, 얼마나 다를까?', '가장 최근 데이터 기준 강남구와 서초구 평균 매매가격과 차이를 알려줘'],
  ['03', '가격 변화', '부산의 가격은 어떻게 변했을까?', '부산의 데이터에 있는 최근 3개월 평균 매매가격 추이를 알려줘'],
];

// 예시로 보낸 질문 → 짧은 설명 (대화 제목에 사용)
export const EXAMPLE_LABELS = new Map(EXAMPLES.map(([, , description, question]) => [question, description]));

export function suggestions(fill) {
  const container = document.createElement('div');
  container.className = 'suggestions';
  for (const [number, title, description, question] of EXAMPLES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.innerHTML = `<span class="suggestion-number">${number}</span><strong>${title}</strong><span class="suggestion-description">${description}</span><span class="suggestion-arrow" aria-hidden="true">↗</span>`;
    button.addEventListener('click', () => fill(question));
    container.append(button);
  }
  return container;
}
