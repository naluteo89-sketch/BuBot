export function suggestions(fill) {
  const container = document.createElement('div'); container.className='suggestions';
  for(const [title,question] of [['지역 조회','서울 2026년 8월 평균매매가격은?'],['지역 비교','강남과 서초 2026년 8월 각각 가격과 차이'],['가격 추이','서울 2026년 6월부터 8월까지 추이와 변동률'],['지역 흐름','강남구 요즘 흐름 어때?'],['시장 동향','2021년에 가장 많이 오른 지역은?'],['용어 설명','변동률은 어떻게 계산해?']]) {
    const button=document.createElement('button'); button.textContent=title+' ↗'; button.title=question; button.onclick=()=>fill(question); container.append(button);
  }
  return container;
}
