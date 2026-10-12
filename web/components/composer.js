// placeholder: 첫 화면만 예시 문장을 쓰고, 대화 화면은 예시 없는 안내 문구를 넘깁니다.
export function composer(onSubmit, {placeholder = '예: 서울과 부산의 평균 매매가격을 비교해 줘'} = {}) {
  const form = document.createElement('form');
  form.className = 'question-form';
  form.innerHTML = `<label for="question" class="sr-only">궁금한 지역과 기간을 입력하세요</label><textarea id="question" maxlength="1000" rows="2" placeholder="" aria-describedby="input-help" required></textarea><div class="composer-bottom"><label class="sr-only" for="unit">가격 표시 단위</label><select id="unit" aria-label="가격 표시 단위"><option value="">단위 자동</option><option value="korean">억·만원</option><option value="천원">천원</option><option value="만원">만원</option><option value="억원">억원</option></select><span id="input-help">Enter로 전송 · Shift+Enter로 줄바꿈</span><span id="count">0 / 1000</span><button type="submit">조회하기</button></div><div class="sr-only" role="status" id="submit-state"></div>`;
  const input = form.querySelector('textarea');
  input.placeholder = placeholder;
  const button = form.querySelector('button');
  const unit = form.querySelector('select');
  const state = form.querySelector('#submit-state');
  let pending = false, until = 0, timer;
  const focus = () => input.focus({preventScroll: true});
  function update() {
    const seconds = Math.max(0, Math.ceil((until - Date.now()) / 1000));
    button.disabled = pending || seconds > 0 || !input.value.trim();
    input.disabled = pending;
    unit.disabled = pending;
    const label = pending ? '답변 작성 중…' : seconds > 0 ? `${seconds}초 후 다시 조회` : '조회하기';
    button.textContent = label;
    state.textContent = pending || seconds > 0 ? label : '';
    form.classList.toggle('is-busy', pending);
    if (!seconds) {clearInterval(timer); timer = undefined;}
  }
  input.addEventListener('input', () => {form.querySelector('#count').textContent = `${input.value.length} / 1000`; update();});
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!button.disabled) form.requestSubmit();
    }
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (input.value.trim() && !button.disabled) {
      until = Date.now() + 3000;
      clearInterval(timer);
      timer = setInterval(update, 100);
      update();
      onSubmit(input.value.trim(), unit.value);
    }
  });
  update();
  return {
    element: form,
    busy(value) {pending = value; update();},
    focus,
    fill(text) {
      if (pending) return;
      input.value = text;
      input.dispatchEvent(new Event('input'));
      focus();
      form.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center'});
    },
    clear() {input.value = ''; input.dispatchEvent(new Event('input'));}
  };
}
