import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import tool


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.fields = [{'key': tool.SECTOR, 'options': list('ABCDE')},
                       {'key': 'entry.2', 'options': ['1', '2', '3']}]

    def test_balanced_valid_reproducible(self):
        rows = tool.generate(self.fields)
        self.assertEqual(rows, tool.generate(self.fields))
        self.assertEqual(set(tool.validate(self.fields, rows).values()), {40})

    def test_invalid_choice_rejected(self):
        rows = tool.generate(self.fields)
        rows[0]['entry.2'] = 'invalid'
        with self.assertRaises(ValueError):
            tool.validate(self.fields, rows)

    def test_topup_preserves_every_question_proportion(self):
        baseline = tool.generate(self.fields)
        extra = tool.topup(self.fields, baseline)
        tool.validate(self.fields, extra, balanced=False, expected_count=36)
        self.assertEqual(extra, tool.topup(self.fields, baseline))
        for field in self.fields:
            for choice in field['options']:
                before = sum(row[field['key']] == choice for row in baseline)
                added = sum(row[field['key']] == choice for row in extra)
                self.assertLess(abs(added - 36 * before / 200), 1)

    def test_csv_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'responses.csv'
            rows = tool.generate(self.fields)
            tool.write_rows(path, self.fields, rows)
            self.assertEqual(tool.read_rows(path), rows)
            with self.assertRaises(FileExistsError):
                tool.write_rows(path, self.fields, rows)
            self.assertEqual(tool.read_rows(path), rows)

    def test_revised_distribution(self):
        fields = [{'key': tool.SECTOR, 'options': [
            'Sức khỏe', 'Kỹ thuật – Công nghệ', 'Kinh tế - Kinh doanh',
            'Khoa học xã hội – Nhân văn', 'Nghệ thuật và Thiết kế']}] + self.fields[1:] + [
            {'key': 'entry.863899056', 'options': ['Nam', 'Nữ', 'Khác']},
            {'key': 'entry.scale1', 'options': [str(i) + ' — answer' for i in range(1, 6)]},
            {'key': 'entry.scale2', 'options': [str(i) + ' — answer' for i in range(1, 6)]}]
        rows = tool.generate(fields, revised=True)
        self.assertEqual(rows, tool.generate(fields, revised=True))
        counts = tool.validate(fields, rows, balanced=False)
        self.assertEqual(sorted(counts.values()), [30, 36, 40, 44, 50])
        self.assertEqual(counts['Kỹ thuật – Công nghệ'], 50)
        self.assertEqual(counts['Nghệ thuật và Thiết kế'], 30)
        self.assertEqual(sum(row['entry.863899056'] == 'Khác' for row in rows), 16)
        for key in ['entry.scale1', 'entry.scale2']:
            low = sum(row[key][0] in '12' for row in rows)
            self.assertTrue(12 <= low <= 24)
            self.assertEqual({row[key][0] for row in rows}, set('12345'))

    def test_imbalance_rejected(self):
        rows = tool.generate(self.fields)
        for row in rows:
            row[tool.SECTOR] = 'A'
        with self.assertRaises(ValueError):
            tool.validate(self.fields, rows)

    def test_confirmation_required(self):
        with patch('tool.fetch_form', return_value=('', [])), \
                patch('tool.build_payload', return_value={}), patch('tool.urlopen') as network:
            response = network.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'<html>Validation error</html>'
            with self.assertRaises(ValueError):
                tool.submit({})
            response.read.return_value = b'<div class="vHW8K">Recorded</div>'
            tool.submit({})

    def test_resume_requires_reconciliation_and_skips_confirmed(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'log.jsonl'
            journal.write_text('\n'.join(json.dumps(record) for record in [
                {'row': 1, 'protocol': 2, 'status': 'confirmation_received'},
                {'row': 2, 'protocol': 2, 'status': 'attempting'}]) + '\n', encoding='utf-8')
            rows = [{}, {}, {}]
            with self.assertRaises(ValueError):
                tool.remaining_rows(rows, journal)
            self.assertEqual(tool.remaining_rows(rows, journal, 'saved'), [(3, {})])
            self.assertEqual(tool.remaining_rows(rows, journal), [(3, {})])

    def test_not_saved_row_can_be_sent_again(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'log.jsonl'
            journal.write_text(json.dumps({'row': 1, 'protocol': 2, 'status': 'unknown_or_failed'}) + '\n',
                               encoding='utf-8')
            self.assertEqual(tool.remaining_rows([{}], journal, 'not-saved'), [(1, {})])

    def test_multisection_payload_keeps_answers_and_navigation(self):
        html = '<input type="hidden" name="fvv" value="1">' \
               '<input type="hidden" name="fbzx" value="fixture">' \
               '<input type="hidden" name="pageHistory" value="0">'
        data = [None, [None, [[1, 'Section', None, 8], [2, 'Question', None, 2],
                             [3, 'Section', None, 8]]]]
        payload = tool.build_payload({'entry.2': 'choice'}, html, data)
        self.assertEqual(payload['pageHistory'], '0,1,2')
        self.assertEqual(payload['entry.2'], 'choice')
        self.assertEqual(payload['fbzx'], 'fixture')
        with self.assertRaises(ValueError):
            tool.build_payload({}, '', data)

    def test_legacy_confirmation_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'log.jsonl'
            journal.write_text(json.dumps({'row': 1, 'status': 'confirmation_received'}),
                               encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Legacy journal'):
                tool.remaining_rows([{}], journal)


if __name__ == '__main__':
    unittest.main()
