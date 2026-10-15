from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import gui
import tool


class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest('Tk display unavailable: ' + str(error))
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = {'form_url': 'https://docs.google.com/forms/d/e/fixture/',
                       'batch_id': 'gui-test', 'count': 5, 'seed': 1, 'distributions': {}}
        path = self.folder / 'config.local.json'
        path.write_text(json.dumps(self.config), encoding='utf-8')
        self.app = gui.App(self.root, path)
        self.addCleanup(self.app.close)
        self.fields = [{'key': 'entry.10', 'title': 'Giới tính', 'options': ['Nam', 'Nữ', 'Khác']},
                       {'key': 'entry.20', 'title': 'Câu hỏi', 'options': ['A', 'B']}]

    def pump(self):
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertFalse(self.app.busy, self.app.status.get())

    def load_fields(self):
        with patch('tool.fetch_form', return_value=('', [])), \
                patch('tool.parse_schema', return_value=self.fields):
            self.app.load_form()
            self.pump()

    def test_load_edit_preview_and_snapshot_without_json(self):
        self.load_fields()
        self.app.count.set('37')
        for (variable, _, _), weight in zip(self.app.editors, [46, 46, 8]):
            variable.set(str(weight))
        config = self.app.snapshot()
        self.assertEqual(config['count'], 37)
        self.assertEqual(config['distributions']['entry.10'], {'Nam': 46, 'Nữ': 46, 'Khác': 8})
        self.assertIn('37 dòng', self.app.preview.get())
        self.app.editors[0][0].set('-1')
        with self.assertRaises(ValueError):
            self.app.snapshot()

    def test_prepare_and_resume_use_same_core_without_duplicate_sends(self):
        self.load_fields()
        with patch('tool.ROOT', self.folder), patch('tool.submit') as submit, \
                patch('tool.time.sleep'), redirect_stdout(io.StringIO()):
            self.app.prepare_batch()
            self.pump()
            submit.assert_not_called()
            self.app.send(1)
            self.pump()
            self.assertEqual(submit.call_count, 1)
            self.app.send()
            self.pump()
            self.assertEqual(submit.call_count, 5)
            self.app.send()
            self.pump()
            self.assertEqual(submit.call_count, 5)

    def test_edited_url_requires_reload_before_preparing(self):
        self.load_fields()
        self.app.url.set('https://docs.google.com/forms/d/e/another/viewform')
        with self.assertRaisesRegex(ValueError, 'tải câu hỏi'):
            self.app.snapshot()

    def test_config_change_after_prepare_blocks_send(self):
        self.load_fields()
        with patch('tool.ROOT', self.folder), patch('tool.submit') as submit, redirect_stdout(io.StringIO()):
            self.app.prepare_batch()
            self.pump()
            self.app.count.set('6')
            self.app.send()
            submit.assert_not_called()
            self.assertIn('config or live schema changed', self.app.status.get())

    def test_viewing_questions_preserves_existing_cli_batch_identity(self):
        with patch('tool.ROOT', self.folder), patch('tool.submit') as submit, redirect_stdout(io.StringIO()):
            folder = tool.batch_path(self.config)
            tool.prepare(folder, self.config, self.fields)
            self.load_fields()
            self.app.tree.selection_set('1')
            self.root.update()
            self.app.tree.selection_set('0')
            self.root.update()
            self.assertEqual(self.app.snapshot(), self.config)
            self.app.send(1)
            self.pump()
            self.assertEqual(submit.call_count, 1)

    def test_reloading_new_form_preserves_edited_weights(self):
        self.load_fields()
        self.app.url.set('https://docs.google.com/forms/d/e/new-form/viewform')
        self.load_fields()
        self.app.editors[0][0].set('80')
        self.load_fields()
        self.assertEqual(float(self.app.editors[0][0].get()), 80)

    def test_form_schema_error_is_reported_without_stuck_worker(self):
        self.app.rules = {'entry.999': {'invalid': 1}}
        self.load_fields()
        self.assertFalse(self.app.fields)
        self.assertIn('không còn trong form', self.app.status.get())

    def test_stop_after_current_row_and_resume(self):
        self.load_fields()
        with patch('tool.ROOT', self.folder), patch('tool.time.sleep'), redirect_stdout(io.StringIO()):
            self.app.prepare_batch()
            self.pump()
            with patch('tool.submit', side_effect=lambda *_: self.app.stop.set()) as submit:
                self.app.send()
                self.pump()
                self.assertEqual(submit.call_count, 1)
            with patch('tool.submit') as submit:
                self.app.send()
                self.pump()
                self.assertEqual(submit.call_count, 4)

    def test_unknown_result_exposes_reconciliation_without_retry(self):
        self.load_fields()
        with patch('tool.ROOT', self.folder), patch('tool.time.sleep'), redirect_stdout(io.StringIO()):
            self.app.prepare_batch()
            self.pump()
            with patch('tool.submit', side_effect=TimeoutError('Uncertain network result')):
                self.app.send(1)
                self.pump()
            with patch('tool.submit') as submit:
                self.app.send(1)
                self.pump()
                submit.assert_not_called()
                self.assertTrue(self.app.recovery.winfo_manager())
                self.app.send(1, 'saved')
                self.pump()
                self.assertEqual(submit.call_count, 1)
                self.assertIn('2 / 5', self.app.status.get())

    def test_bulk_apply_only_changes_likert_questions(self):
        choices = [str(i) + ' — câu trả lời' for i in range(1, 6)]
        self.fields += [{'key': 'entry.30', 'title': 'Thang 1', 'options': choices},
                        {'key': 'entry.40', 'title': 'Thang 2', 'options': choices}]
        self.load_fields()
        self.app.tree.selection_set('2')
        self.root.update()
        for (variable, _, _), weight in zip(self.app.editors, [4, 6, 25, 40, 25]):
            variable.set(str(weight))
        self.app.apply_scales()
        self.assertEqual(self.app.rules['entry.30'], self.app.rules['entry.40'])
        self.assertNotIn('entry.20', self.app.rules)

    def test_bulk_apply_works_with_unchanged_implicit_weights(self):
        choices = [str(i) + ' — câu trả lời' for i in range(1, 6)]
        self.fields += [{'key': 'entry.30', 'title': 'Thang 1', 'options': choices},
                        {'key': 'entry.40', 'title': 'Thang 2', 'options': choices}]
        self.load_fields()
        self.app.tree.selection_set('2')
        self.root.update()
        self.app.apply_scales()
        self.assertEqual(self.app.rules['entry.30'], dict.fromkeys(choices, 1))
        self.assertEqual(self.app.rules['entry.40'], dict.fromkeys(choices, 1))

    def test_action_buttons_match_question_and_operation(self):
        self.load_fields()
        self.assertTrue(self.app.apply_button.instate(['disabled']))
        self.app.can_stop = False
        self.app.set_busy(True)
        self.assertTrue(self.app.stop_button.instate(['disabled']))
        self.app.can_stop = True
        self.app.set_busy(True)
        self.assertTrue(self.app.stop_button.instate(['!disabled']))
        self.app.set_busy(False)

    def test_long_choices_wrap_and_editor_scrolls(self):
        self.fields = [{'key': 'entry.50', 'title': 'Câu hỏi có nhiều lựa chọn',
                        'options': [('Lựa chọn dài cần xuống dòng ' * 3) + str(i) for i in range(15)]}]
        self.load_fields()
        self.app.resize_options(type('Size', (), {'width': 500})())
        self.assertTrue(all(int(label.cget('wraplength')) == 260 for label in self.app.choice_labels))
        self.app.canvas.configure(height=100)
        self.root.update()
        self.app.canvas.configure(scrollregion=self.app.canvas.bbox('all'))
        self.app.scroll_options(type('Wheel', (), {'delta': -120})())
        self.assertGreater(self.app.canvas.yview()[0], 0)


if __name__ == '__main__':
    unittest.main()
