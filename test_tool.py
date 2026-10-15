from collections import Counter
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tool


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fields = [
            {'key': 'entry.10', 'title': 'Gender', 'options': ['A', 'B', 'Other']},
            {'key': 'entry.20', 'title': 'Scale', 'options': [str(i) + ' — answer' for i in range(1, 6)]}]
        self.config = {'form_url': 'https://docs.google.com/forms/d/e/fixture/',
                       'batch_id': 'test-batch', 'count': 37, 'seed': 1,
                       'distributions': {'entry.10': {'A': 46, 'B': 46, 'Other': 8}},
                       'likert_weights': [4, 6, 25, 40, 25]}
        self.output = io.StringIO()

    def load(self, config):
        path = self.root / 'config.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        return tool.load_config(path)

    def prepare(self):
        folder = self.root / 'batch'
        with redirect_stdout(self.output):
            tool.prepare(folder, self.config, self.fields)
        return folder

    def send(self, folder, **kwargs):
        with redirect_stdout(self.output), patch('tool.time.sleep'):
            tool.send_batch(folder, self.config, self.fields, **kwargs)

    def test_config_normalizes_full_form_urls(self):
        for suffix in ['viewform?usp=sharing', 'formResponse', '']:
            config = dict(self.config, form_url=self.config['form_url'] + suffix)
            self.assertEqual(self.load(config), self.config)

    def test_config_rejects_invalid_inputs(self):
        for changes in [
                {'count': 0}, {'count': -1}, {'count': True}, {'count': 3.2},
                {'batch_id': '../escape'}, {'batch_id': ''}, {'seed': True},
                {'form_url': 'http://docs.google.com/forms/d/e/id/viewform'},
                {'form_url': 'https://docs.google.com.evil.test/forms/d/e/id/viewform'},
                {'form_url': 'https://user:password@docs.google.com/forms/d/e/id/viewform'},
                {'likert_weights': [1, 2]}, {'likert_weights': [0] * 5},
                {'distributions': {'entry.10': {'Other': -1}}}, {'typo': 'value'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load(dict(self.config, **changes))

    def test_weights_reject_nonfinite_and_non_numeric_values(self):
        for values in [[-1, 2], [float('nan'), 1], [float('inf')], [True, 1], ['5', 2], []]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                tool.weights(values)

    def test_largest_remainder_preserves_total_and_proportions(self):
        for total in [1, 7, 36, 200, 236, 501]:
            values = [0, 20, 25, 22, 18, 15]
            counts = tool.allocate(total, values)
            self.assertEqual(sum(counts), total)
            self.assertEqual(counts[0], 0)
            for actual, weight in zip(counts, values):
                self.assertLess(abs(actual - total * weight / 100), 1)
        self.assertEqual(tool.allocate(200, [46, 46, 8]), [92, 92, 16])
        self.assertEqual(tool.allocate(36, [20, 25, 22, 18, 15]), [7, 9, 8, 7, 5])

    def test_generation_uses_config_not_fixed_count_or_entry_ids(self):
        for count in [1, 37, 236, 501]:
            config = dict(self.config, count=count)
            rows = tool.generate(self.fields, config)
            tool.validate(self.fields, rows, count)
            self.assertEqual(rows, tool.generate(self.fields, config))
            for field in self.fields:
                actual = Counter(row[field['key']] for row in rows)
                expected = tool.allocate(count, tool.field_weights(field, config))
                self.assertEqual([actual[choice] for choice in field['options']], expected)

    def test_per_entry_override_beats_likert_default(self):
        config = copy.deepcopy(self.config)
        config['distributions']['entry.20'] = {choice: i for i, choice in enumerate(self.fields[1]['options'])}
        self.assertEqual(tool.field_weights(self.fields[1], config), [0, 1, 2, 3, 4])
        self.assertEqual(tool.field_weights(self.fields[0], dict(config, distributions={})), [1, 1, 1])

    def test_unknown_or_incomplete_distributions_fail(self):
        for rules in [{'entry.999': {'A': 1}}, {'entry.10': {'A': 1}}]:
            with self.assertRaises(ValueError):
                tool.generate(self.fields, dict(self.config, distributions=rules))

    def test_validation_rejects_bad_answers_and_count(self):
        rows = tool.generate(self.fields, self.config)
        with self.assertRaises(ValueError):
            tool.validate(self.fields, rows, 200)
        rows[0]['entry.10'] = 'invalid'
        with self.assertRaises(ValueError):
            tool.validate(self.fields, rows, self.config['count'])
        rows[0]['extra'] = 'value'
        with self.assertRaises(ValueError):
            tool.validate(self.fields, rows, self.config['count'])

    def test_prepare_is_idempotent_and_never_overwrites_csv(self):
        folder = self.prepare()
        original = (folder / 'responses.csv').read_bytes()
        with redirect_stdout(self.output):
            tool.prepare(folder, self.config, self.fields)
        self.assertEqual((folder / 'responses.csv').read_bytes(), original)
        with self.assertRaises(FileExistsError):
            tool.write_rows(folder / 'responses.csv', self.fields, [])

    def test_csv_or_config_or_schema_change_blocks_send(self):
        folder = self.prepare()
        with patch('tool.submit') as submit:
            changed = dict(self.config, count=38)
            with self.assertRaisesRegex(ValueError, 'config or live schema'):
                tool.send_batch(folder, changed, self.fields)
            changed_fields = copy.deepcopy(self.fields)
            changed_fields[0]['options'].append('New choice')
            with self.assertRaisesRegex(ValueError, 'config or live schema'):
                tool.send_batch(folder, self.config, changed_fields)
            rows = tool.read_rows(folder / 'responses.csv')
            rows[0]['entry.10'] = 'B' if rows[0]['entry.10'] != 'B' else 'A'
            with (folder / 'responses.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                writer = tool.csv.DictWriter(stream, fieldnames=[field['key'] for field in self.fields])
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, 'CSV or manifest changed'):
                self.send(folder)
            submit.assert_not_called()

    def test_resume_never_resends_confirmed_rows(self):
        folder = self.prepare()
        with patch('tool.submit') as submit:
            self.send(folder, limit=2)
            self.assertEqual(submit.call_count, 2)
            submit.reset_mock()
            self.send(folder)
            self.assertEqual(submit.call_count, 35)
            submit.reset_mock()
            self.send(folder)
            submit.assert_not_called()

    def test_interrupt_requires_explicit_reconciliation(self):
        folder = self.prepare()
        with patch('tool.submit', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.send(folder, limit=1)
        self.assertFalse((folder / 'send.lock').exists())
        with patch('tool.submit') as submit:
            with self.assertRaisesRegex(ValueError, 'Uncertain rows'):
                self.send(folder)
            submit.assert_not_called()
            self.send(folder, resolution='saved', limit=1)
            rows = tool.read_rows(folder / 'responses.csv')
            self.assertEqual(submit.call_args[0][1], rows[1])

    def test_not_saved_resolution_retries_only_uncertain_row(self):
        folder = self.prepare()
        with patch('tool.submit', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                self.send(folder, limit=1)
        with patch('tool.submit') as submit:
            self.send(folder, resolution='not-saved', limit=1)
            self.assertEqual(submit.call_args[0][1], tool.read_rows(folder / 'responses.csv')[0])

    def test_batch_lock_prevents_parallel_senders(self):
        folder = self.prepare()
        with tool.batch_lock(folder), patch('tool.submit') as submit:
            with self.assertRaisesRegex(ValueError, 'locked'):
                self.send(folder)
            submit.assert_not_called()

    def test_mismatched_legacy_and_corrupt_journals_fail_closed(self):
        folder = self.prepare()
        journal = folder / 'send-log.jsonl'
        for record in [{'protocol': 2}, {'protocol': 3, 'batch_hash': 'other'}]:
            journal.write_text(json.dumps(record) + '\n', encoding='utf-8')
            with patch('tool.submit') as submit:
                with self.assertRaisesRegex(ValueError, 'mismatched journal'):
                    self.send(folder)
                submit.assert_not_called()
        journal.write_text('{"truncated":', encoding='utf-8')
        with patch('tool.submit') as submit:
            with self.assertRaises(ValueError):
                self.send(folder)
            submit.assert_not_called()

    def test_schema_is_dynamic_and_rejects_unsupported_types(self):
        item = [1, 'Question', None, 2, [[123, [['A'], ['B']], 1]]]
        data = [None, [None, [[9, 'Section', None, 8], item]]]
        self.assertEqual(tool.parse_schema(data)[0]['key'], 'entry.123')
        item[3] = 3
        self.assertEqual(tool.parse_schema(data)[0]['options'], ['A', 'B'])
        item[3] = 0
        with self.assertRaises(ValueError):
            tool.parse_schema(data)
        item[3] = 2
        item[4][0][1][0] = ['A', None, 99]
        with self.assertRaises(ValueError):
            tool.parse_schema(data)

    def test_multisection_payload_and_confirmation(self):
        html = '<input type="hidden" name="fvv" value="1">' \
               '<input type="hidden" name="fbzx" value="fixture">' \
               '<input type="hidden" name="pageHistory" value="0">'
        data = [None, [None, [[1, 'Section', None, 8], [2, 'Question', None, 2],
                             [3, 'Section', None, 8]]]]
        payload = tool.build_payload({'entry.2': 'choice'}, html, data)
        self.assertEqual(payload['pageHistory'], '0,1,2')
        self.assertEqual(payload['entry.2'], 'choice')
        with self.assertRaises(ValueError):
            tool.build_payload({}, '', data)
        with patch('tool.fetch_form', return_value=(html, data)), \
                patch('tool.parse_schema', return_value=self.fields), patch('tool.urlopen') as network:
            response = network.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'<html>Validation error</html>'
            with self.assertRaises(ValueError):
                tool.submit(self.config['form_url'], {}, self.fields)
            response.read.return_value = b'<div class="vHW8K">Recorded</div>'
            tool.submit(self.config['form_url'], {}, self.fields)

    def test_schema_change_during_sending_never_posts(self):
        with patch('tool.fetch_form', return_value=('', [])), \
                patch('tool.parse_schema', return_value=[]), patch('tool.urlopen') as network:
            with self.assertRaises(ValueError):
                tool.submit(self.config['form_url'], {}, self.fields)
            network.assert_not_called()

    def test_section_branching_is_rejected(self):
        data = [None, [None, [[1, 'Section', None, 8, None, 123]]]]
        with self.assertRaisesRegex(ValueError, 'Section branching'):
            tool.parse_schema(data)

    def test_cli_prepare_and_resume_with_configured_batch(self):
        path = self.root / 'config.json'
        path.write_text(json.dumps(self.config), encoding='utf-8')
        with patch('tool.ROOT', self.root), patch('tool.fetch_form', return_value=('', [])), \
                patch('tool.parse_schema', return_value=self.fields), \
                patch('tool.submit') as submit, patch('tool.time.sleep'), redirect_stdout(self.output):
            with patch('sys.argv', ['tool.py', 'prepare', '--config', str(path)]):
                tool.main()
            submit.assert_not_called()
            for _ in range(2):
                with patch('sys.argv', ['tool.py', 'send', '--config', str(path)]):
                    tool.main()
            self.assertEqual(submit.call_count, self.config['count'])

    def test_cli_rejects_invalid_send_options_before_network(self):
        for args in [['send', '--limit', '0'], ['prepare', '--limit', '1'],
                     ['prepare', '--resolve-last', 'saved']]:
            with patch('sys.argv', ['tool.py'] + args + ['--config', 'unused.json']), \
                    patch('tool.fetch_form') as fetch, redirect_stdout(self.output), \
                    patch('sys.stderr', self.output), self.assertRaises(SystemExit):
                tool.main()
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
