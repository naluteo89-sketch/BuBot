export function status(element, data) {
  element.replaceChildren();
  const lines=[data.key_ready?'● API 키 인식됨':'○ API 키 설정 필요',data.index_ready?'● RAG 지식 색인 준비됨':'○ 지식 색인 점검 필요',`데이터 ${data.period}`];
  if(data.index_message) lines.push(data.index_message);
  for(const text of lines) {
    const line=document.createElement('div'); line.textContent=text; element.append(line);
  }
}
