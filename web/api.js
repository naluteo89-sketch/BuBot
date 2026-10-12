export async function request(path, payload) {
  const response = await fetch(path, payload ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)} : {});
  // BuBot 서버가 아닌 곳(Live Server 등 정적 서버, 403 페이지)이 HTML을 돌려주면 JSON 해석 오류 대신 원인을 알려 줍니다.
  if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
    throw new Error(`BuBot 서버에 연결되지 않았어요 (응답 ${response.status}). web_main.py로 띄운 주소(기본 포트 8000)로 접속했는지 확인해 주세요.`);
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '서버 요청에 실패했습니다.');
  return data;
}
