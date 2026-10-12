export function composer(onSubmit) {
  const form = document.createElement('form');
  form.innerHTML = `<label class="sr-only" for="question">질문</label><textarea id="question" maxlength="1000" rows="2" placeholder="예: 서울과 부산의 2026년 8월 평균매매가격을 비교해줘" required></textarea><div class="composer-bottom"><label for="unit">가격 단위</label><select id="unit"><option value="">자동</option><option value="korean">억·만원</option><option value="천원">천원</option><option value="만원">만원</option><option value="억원">억원</option></select><span id="count">0 / 1000</span><button type="submit">질문 보내기 ↗</button></div>`;
  const input = form.querySelector('textarea'); const button = form.querySelector('button');
  let pending=false, until=0, timer;
  function update(){
    const seconds=Math.max(0,Math.ceil((until-Date.now())/1000));
    button.disabled=pending||seconds>0; input.disabled=pending;
    button.textContent=pending?'답변 생성 중…':seconds>0?`${seconds}초 후 전송 가능`:'질문 보내기 ↗';
    if(!seconds){clearInterval(timer);timer=undefined;}
  }
  input.addEventListener('input', () => form.querySelector('#count').textContent = `${input.value.length} / 1000`);
  input.addEventListener('keydown', e => {if(e.key === 'Enter' && !e.shiftKey && !e.isComposing) {e.preventDefault(); if(!button.disabled) form.requestSubmit();}});
  form.addEventListener('submit', e => {e.preventDefault(); if(input.value.trim() && !button.disabled){until=Date.now()+3000;clearInterval(timer);timer=setInterval(update,100);update();onSubmit(input.value.trim(), form.querySelector('select').value);}});
  return {element:form, busy(value){pending=value;update();}, fill(text){input.value=text; input.dispatchEvent(new Event('input')); input.focus();}, clear(){input.value=''; input.dispatchEvent(new Event('input')); input.focus();}};
}
