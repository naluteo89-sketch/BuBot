"""한/영 전환 없이 두벌식 자판으로 친 한글(예: 'tjdnfrhk' → '서울과')을 되돌립니다.

질문에 한글이 하나도 없고, 변환 결과가 모두 완성된 글자일 때만 변환합니다.
영어 문장은 자음·모음이 짝을 이루지 못해 변환되지 않습니다.
"""
KEYS = dict(zip('qwertyuiopasdfghjklzxcvbnm', 'ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ'))
KEYS.update(zip('QWERTOP', 'ㅃㅉㄸㄲㅆㅒㅖ'))
CHO = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ'
JUNG = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ'
JONG = ['', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ', 'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ',
        'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
VOWEL_PAIRS = {('ㅗ', 'ㅏ'): 'ㅘ', ('ㅗ', 'ㅐ'): 'ㅙ', ('ㅗ', 'ㅣ'): 'ㅚ', ('ㅜ', 'ㅓ'): 'ㅝ', ('ㅜ', 'ㅔ'): 'ㅞ',
               ('ㅜ', 'ㅣ'): 'ㅟ', ('ㅡ', 'ㅣ'): 'ㅢ'}
FINAL_PAIRS = {('ㄱ', 'ㅅ'): 'ㄳ', ('ㄴ', 'ㅈ'): 'ㄵ', ('ㄴ', 'ㅎ'): 'ㄶ', ('ㄹ', 'ㄱ'): 'ㄺ', ('ㄹ', 'ㅁ'): 'ㄻ',
               ('ㄹ', 'ㅂ'): 'ㄼ', ('ㄹ', 'ㅅ'): 'ㄽ', ('ㄹ', 'ㅌ'): 'ㄾ', ('ㄹ', 'ㅍ'): 'ㄿ', ('ㄹ', 'ㅎ'): 'ㅀ',
               ('ㅂ', 'ㅅ'): 'ㅄ'}


def _has_hangul(text):
    return any('가' <= ch <= '힣' or 'ㄱ' <= ch <= 'ㅣ' for ch in text)


def keyboard_to_hangul(text):
    """변환된 한글 문자열. 변환 대상이 아니거나 완성되지 않는 글자가 있으면 None."""
    if not isinstance(text, str) or _has_hangul(text) or sum(ch.isascii() and ch.isalpha() for ch in text) < 2:
        return None
    out, broken = [], False
    cho = jung = None
    jong = []

    def flush():
        nonlocal cho, jung, jong, broken
        if cho and jung:
            final = ''.join(jong) and JONG.index(FINAL_PAIRS.get(tuple(jong), jong[0]) if len(jong) == 2 else jong[0])
            out.append(chr(0xAC00 + (CHO.index(cho) * 21 + JUNG.index(jung)) * 28 + (final or 0)))
        elif cho or jung:
            broken = True
        cho, jung, jong = None, None, []

    for ch in text:
        jamo = KEYS.get(ch) or KEYS.get(ch.lower())
        if jamo is None:
            flush()
            out.append(ch)
        elif jamo in CHO or jamo in JONG:  
            if cho and jung:
                if not jong and jamo in JONG:
                    jong = [jamo]
                elif len(jong) == 1 and (jong[0], jamo) in FINAL_PAIRS:
                    jong = [jong[0], jamo]
                else:
                    flush()
                    cho = jamo
            else:
                flush()
                cho = jamo
        else:  # 모음
            if cho and jung and jong:
                carry = jong.pop()  # 받침의 마지막 자음은 다음 글자의 첫소리로 넘어갑니다
                flush()
                cho, jung = carry, jamo
            elif cho and jung and (jung, jamo) in VOWEL_PAIRS:
                jung = VOWEL_PAIRS[(jung, jamo)]
            elif cho and not jung:
                jung = jamo
            else:
                flush()
                jung = jamo
    flush()
    result = ''.join(out)
    syllables = sum('가' <= ch <= '힣' for ch in result)
    return None if broken or syllables < 2 else result
