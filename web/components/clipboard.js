// 클립보드 복사와 대화 텍스트 만들기 (대화 화면 버튼과 사이드바 메뉴가 함께 씁니다)
const CHECK_MARK = '[수치 확인 필요]';

export function plainAnswer(text = '') {
  const at = text.indexOf(CHECK_MARK);
  if (at < 0) return text.trim();
  return text.slice(0, at).trim() + '\n\n※ 일부 수치는 계산 근거와 다를 수 있어요. 원본 값을 확인해 주세요.';
}

// 휴대폰에서 http://192.168.x.x 로 접속하면 보안 연결이 아니라 Clipboard API를 쓸 수 없어 예전 방식으로 복사합니다.
export async function copyToClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* 아래 방식으로 다시 시도 */ }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.className = 'clipboard-proxy';
  document.body.append(area);
  area.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch { copied = false; }
  area.remove();
  return copied;
}

// 대화 전체를 사람이 읽는 텍스트로
export function transcriptText(turns, title = '') {
  const lines = [`BuBot 대화 기록${title ? ` · ${title}` : ''}`, ''];
  turns.forEach((turn, index) => {
    lines.push(`Q${index + 1}. ${turn.question}`);
    if (turn.pending) lines.push('A. (답변을 기다리는 중이었어요)');
    else if (turn.error) lines.push(`A. (답변을 받지 못했어요) ${turn.error}`);
    else {
      lines.push(`A. ${plainAnswer(turn.answer)}`);
      if (turn.data?.source_note) lines.push(turn.data.source_note);
    }
    lines.push('');
  });
  return lines.join('\n').trim();
}
