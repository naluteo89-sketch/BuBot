import {request} from '../api.js';
import {message} from './message.js';
export function historyPanel() {
  const panel=document.createElement('details'); panel.className='history-panel';
  const title=document.createElement('summary'); title.textContent='저장된 테스트 기록';
  const tools=document.createElement('div'); tools.className='history-tools';
  const refresh=document.createElement('button'); refresh.textContent='기록 새로고침';
  const download=document.createElement('button'); download.textContent='전체 기록 JSON 다운로드';
  const info=document.createElement('p'); info.textContent='질문별 TXT와 전체기록.html이 C:\\BuBot\\테스트기록 폴더에 저장됩니다. 아래는 최근 100건입니다.';
  const list=document.createElement('div');
  const viewer=document.createElement('a');viewer.href='/history';viewer.target='_blank';viewer.rel='noopener';viewer.textContent='전체 기록 읽기';tools.append(viewer,refresh,download);panel.append(title,info,tools,list);
  async function load(){
    try {
      const data=await request('/api/history');list.replaceChildren();
      if(!data.records.length){list.textContent='아직 저장된 테스트가 없습니다.';return;}
      for(const record of data.records){
        const row=document.createElement('details');
        const label=document.createElement('summary');label.textContent=`${record.created_at.replace('T',' ')} · ${record.status==='success'?'응답':'오류'} · ${record.question}`;
        const meta=document.createElement('p');meta.textContent=`단위: ${record.unit||'자동'} · 처리 시간: ${record.elapsed_seconds}초`;
        row.append(label,meta,message(record.status==='success'?'assistant':'error',record.result.answer||record.result.error,{...record.result,elapsed_seconds:record.elapsed_seconds}));list.append(row);
      }
    } catch(error){list.textContent=error.message;}
  }
  refresh.onclick=load;
  panel.addEventListener('toggle',()=>{if(panel.open)load();});
  download.onclick=async()=>{
    try {const data=await request('/api/history/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='bubot-test-history.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    catch(error){list.textContent=error.message;}
  };
  return {element:panel,refresh:load};
}
