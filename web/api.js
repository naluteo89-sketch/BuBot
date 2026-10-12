export async function request(path, payload) {
  const response = await fetch(path, payload ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)} : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '서버 요청에 실패했습니다.');
  return data;
}
