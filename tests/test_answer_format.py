import unittest

from bubot.answer_format import source_note, unify_units
from bubot.source_display import SOURCE_LABEL


class UnifyUnitsTests(unittest.TestCase):
    def test_thousand_won_converted_to_selected_unit(self):
        self.assertEqual(unify_units('평균 매매가격은 288,122천원입니다.', 'korean'),
                         '평균 매매가격은 2억 8,812만 2,000원입니다.')
        self.assertEqual(unify_units('변화량 -1,234천원', 'korean'), '변화량 -123만 4,000원')

    def test_parenthetical_thousand_won_removed(self):
        self.assertEqual(unify_units('최신 가격 2억 8,668만원 (286,680천원)', 'korean'), '최신 가격 2억 8,668만원')

    def test_thousand_unit_choice_kept(self):
        text = '최신 가격 286,680천원'
        self.assertEqual(unify_units(text, '천원'), text)

    def test_words_without_numbers_untouched(self):
        self.assertEqual(unify_units('원본 단위는 천원입니다.', 'korean'), '원본 단위는 천원입니다.')


class SourceNoteTests(unittest.TestCase):
    def line(self, month):
        return f'[region-1-month-{month}] {SOURCE_LABEL} · 서울 · {month} · 원본 4행 · SHA-256 abc'

    def test_single_month_and_range(self):
        self.assertEqual(source_note([self.line('2026-08')]), f'출처: {SOURCE_LABEL} (기준 2026-08)')
        self.assertEqual(source_note([self.line('2026-08'), self.line('2016-09')]),
                         f'출처: {SOURCE_LABEL} (기준 2016-09 ~ 2026-08)')

    def test_empty_and_guide_only(self):
        self.assertEqual(source_note([]), '')
        self.assertEqual(source_note(['[guide-02] 지식 문서 · 용어 · 02_용어와_계산방법.md']), '출처: BuBot 안내 문서')


if __name__ == '__main__':
    unittest.main()
