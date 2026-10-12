import {historyPanel} from './components/history.js';
import {request} from './api.js';
import {message} from './components/message.js';
import {composer} from './components/composer.js';
import {suggestions} from './components/suggestions.js';
import {status} from './components/status.js';
const messages=document.querySelector('#messages');
const reset=document.querySelector('#reset');
const history=historyPanel();
// 대화 세션: 서버 메모리에 최근 질문·답변(최소 3개)을 기억합니다. 새로고침·새 대화 시 새 세션.
const newSession=()=>(crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join(''));
let sessionId=newSession();
document.querySelector('#history').append(history.element);
const input=composer(async(question,unit)=>{
  input.busy(true); reset.disabled=true;
  messages.append(message('user',question));
  const pending=message('assistant','데이터를 확인하고 답변을 작성하고 있어요…'); messages.append(pending); pending.scrollIntoView({behavior:'smooth',block:'nearest'});
  try {const data=await request('/api/chat',{question,unit,session_id:sessionId}); pending.replaceWith(message('assistant',data.answer,data)); if(data.warning)messages.append(message('error',data.warning)); input.busy(false); input.clear();}
  catch(error){pending.replaceWith(message('error',error.message));}
  finally{history.refresh();input.busy(false);reset.disabled=false;messages.lastElementChild?.scrollIntoView({behavior:'smooth',block:'nearest'});}
});
document.querySelector('#composer').append(input.element);
document.querySelector('#suggestions').append(suggestions(text=>input.fill(text)));
reset.onclick=()=>{const old=sessionId;sessionId=newSession();request('/api/reset',{session_id:old}).catch(()=>{});messages.replaceChildren();input.clear();};
// 기간·결측 안내·출처·기억 개수는 서버 설정과 원본 CSV에서 받아 표시합니다 (데이터 갱신 시 화면 수정 불필요).
const fill=(id,text)=>{const element=document.getElementById(id); if(element&&text)element.textContent=text;};
request('/api/status').then(data=>{
  status(document.querySelector('#status'),data);
  fill('project',data.project); fill('period',data.period); fill('coverage',data.coverage_note);
  fill('source',data.source); fill('memory-turns',data.memory_turns&&String(data.memory_turns));
}).catch(()=>{document.querySelector('#status').textContent='서버 상태 확인 실패';fill('period','서버 상태 확인 실패');});
