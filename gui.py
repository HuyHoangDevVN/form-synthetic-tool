"""Native desktop UI; all generation and sending use tool.py's batch safeguards."""
import copy
from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, ttk

import tool


class App:
    def __init__(self, root, config_path=None):
        self.root = root
        self.config_path = config_path or tool.ROOT / 'config.local.json'
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.can_stop = False
        self.closing = False
        self.fields = []
        self.loaded_base = None
        self.current = None
        self.editors = []
        self.choice_labels = []
        self.rules = {}
        self.config = {}
        self.last_limit = None
        root.title('Form Fixture — dữ liệu kiểm thử')
        root.geometry('1120x800')
        root.minsize(940, 720)
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
            tkfont.nametofont(name).configure(family='Segoe UI', size=10)
        style.configure('Title.TLabel', font=('Segoe UI', 18, 'bold'))
        style.configure('Heading.TLabel', font=('Segoe UI', 11, 'bold'))
        style.configure('Error.TLabel', foreground='#a12622')
        style.configure('Treeview', rowheight=30)
        self.url = tk.StringVar()
        self.count = tk.StringVar(value='200')
        self.batch = tk.StringVar()
        self.status = tk.StringVar(value='Nhập link và tải câu hỏi để bắt đầu.')
        self.preview = tk.StringVar(value='Chọn một câu hỏi để chỉnh trọng số.')
        self.path_label = tk.StringVar()
        self.build()
        source = self.config_path if self.config_path.exists() else tool.ROOT / 'config.example.json'
        try:
            config = tool.load_config(source)
            if source != self.config_path:
                config['batch_id'] = 'batch-' + datetime.now().strftime('%Y%m%d-%H%M%S')
            self.apply_config(config)
        except (ValueError, OSError) as error:
            self.status.set(str(error))
        self.count.trace_add('write', lambda *_: self.update_preview())
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_id = root.after(100, self.poll)

    def build(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text='Phản hồi tổng hợp', style='Title.TLabel').grid(sticky='w')
        ttk.Label(frame, text='Nhập form · phân bổ dữ liệu · xem trước rồi gửi',
                  padding=(0, 4, 0, 8)).grid(row=1, sticky='w')
        settings = ttk.Frame(frame)
        settings.grid(row=2, sticky='ew', pady=(0, 10))
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text='Link Google Form').grid(row=0, column=0, sticky='w', padx=(0, 12))
        self.url_entry = ttk.Entry(settings, textvariable=self.url)
        self.url_entry.grid(row=0, column=1, sticky='ew')
        self.load_button = ttk.Button(settings, text='Tải câu hỏi', command=self.load_form)
        self.load_button.grid(row=0, column=2, padx=(10, 0))
        meta = ttk.Frame(settings)
        meta.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(12, 0))
        ttk.Label(meta, text='Số phản hồi').pack(side='left')
        self.count_entry = ttk.Entry(meta, textvariable=self.count, width=8)
        self.count_entry.pack(side='left', padx=(8, 20))
        ttk.Label(meta, text='Mã lô').pack(side='left')
        self.batch_entry = ttk.Entry(meta, textvariable=self.batch, width=30)
        self.batch_entry.pack(side='left', padx=8)
        self.open_button = ttk.Button(meta, text='Mở cấu hình / lô đã tạo…', command=self.open_config)
        self.open_button.pack(side='right')
        ttk.Label(settings, text='Giữ mã lô để chạy tiếp. Mã khác là một lô mới.',
                  padding=(0, 6, 0, 0)).grid(row=2, columnspan=3, sticky='w')

        split = ttk.Panedwindow(frame, orient='horizontal')
        split.grid(row=3, sticky='nsew')
        questions = ttk.Frame(split)
        questions.rowconfigure(1, weight=1)
        questions.columnconfigure(0, weight=1)
        ttk.Label(questions, text='Câu hỏi', style='Heading.TLabel').grid(sticky='w', pady=(0, 8))
        self.tree = ttk.Treeview(questions, show='tree', selectmode='browse')
        self.tree.column('#0', width=310, minwidth=240)
        self.tree.grid(row=1, sticky='nsew')
        scroll = ttk.Scrollbar(questions, command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.select_question)
        self.tree.bind('<Right>', lambda _: self.focus_weight())
        self.tree.bind('<Button-1>', lambda _: 'break' if self.busy else None)
        self.tree.bind('<Key>', lambda _: 'break' if self.busy else None, add='+')
        split.add(questions, weight=2)
        editor = ttk.Frame(split, padding=(18, 0, 0, 0))
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(1, weight=1)
        self.question_title = ttk.Label(editor, text='Phân bố từng lựa chọn', style='Heading.TLabel', wraplength=570)
        self.question_title.grid(sticky='ew', pady=(0, 8))
        canvas_frame = ttk.Frame(editor)
        canvas_frame.grid(row=1, sticky='nsew')
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(canvas_frame, highlightthickness=0,
                                background=ttk.Style(self.root).lookup('TFrame', 'background'))
        self.canvas.grid(sticky='nsew')
        scrollbar = ttk.Scrollbar(canvas_frame, command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.options = ttk.Frame(self.canvas)
        self.options.columnconfigure(0, weight=1)
        self.window = self.canvas.create_window(0, 0, window=self.options, anchor='nw')
        self.options.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self.resize_options)
        self.canvas.bind('<MouseWheel>', self.scroll_options)
        editor.bind('<Configure>', lambda event: self.question_title.configure(wraplength=max(200, event.width - 20)))
        self.preview_label = ttk.Label(editor, textvariable=self.preview, wraplength=570)
        self.preview_label.grid(row=2, sticky='ew', pady=(8, 6))
        self.apply_button = ttk.Button(editor, text='Áp dụng tỷ lệ này cho mọi câu thang 1–5', command=self.apply_scales)
        self.apply_button.grid(row=3, sticky='w')
        split.add(editor, weight=3)

        ttk.Separator(frame).grid(row=4, sticky='ew', pady=(10, 8))
        actions = ttk.Frame(frame)
        actions.grid(row=5, sticky='ew')
        self.prepare_button = ttk.Button(actions, text='Tạo / kiểm tra lô', command=self.prepare_batch)
        self.prepare_button.pack(side='left')
        self.pilot_button = ttk.Button(actions, text='Gửi thử 1', command=lambda: self.send(1))
        self.pilot_button.pack(side='left', padx=8)
        self.send_button = ttk.Button(actions, text='Gửi phần còn lại', command=self.send)
        self.send_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='Dừng sau lượt hiện tại', command=self.request_stop, state='disabled')
        self.stop_button.pack(side='right')
        self.progress = ttk.Progressbar(frame, mode='determinate')
        self.progress.grid(row=6, sticky='ew', pady=(10, 4))
        ttk.Label(frame, textvariable=self.status, wraplength=1040).grid(row=7, sticky='ew')
        self.path_widget = ttk.Label(frame, textvariable=self.path_label, wraplength=1040)
        self.recovery = ttk.Frame(frame)
        ttk.Label(self.recovery, text='Đối chiếu Forms/Sheet trước khi chọn:').pack(side='left')
        ttk.Button(self.recovery, text='Dòng đã lưu — tiếp tục',
                   command=lambda: self.send(self.last_limit, 'saved')).pack(side='left', padx=8)
        ttk.Button(self.recovery, text='Dòng chưa lưu — gửi lại',
                   command=lambda: self.send(self.last_limit, 'not-saved')).pack(side='left')
        ttk.Label(frame, text='Nút Gửi sẽ thêm dữ liệu vào Google Form. Không tự gửi lại dòng chưa rõ kết quả.',
                  padding=(0, 10, 0, 0)).grid(row=10, sticky='w')
        self.controls = [self.url_entry, self.count_entry, self.batch_entry, self.load_button,
                         self.open_button, self.prepare_button, self.pilot_button, self.send_button,
                         self.apply_button]
        self.set_busy(False)

    def apply_config(self, config):
        self.config = copy.deepcopy(config)
        self.url.set(config['form_url'])
        self.count.set(str(config['count']))
        self.batch.set(config['batch_id'])
        self.rules = copy.deepcopy(config['distributions'])
        self.fields = []
        self.loaded_base = None
        self.current = None
        self.tree.delete(*self.tree.get_children())
        self.path_label.set('')
        self.path_widget.grid_remove()
        self.progress.configure(value=0)
        self.render_options()
        self.recovery.grid_remove()
        self.set_busy(False)

    def open_config(self):
        filename = filedialog.askopenfilename(title='Chọn cấu hình hoặc manifest của lô đã tạo',
                                              filetypes=[('JSON', '*.json')])
        if not filename:
            return
        try:
            value = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
            self.apply_config(tool.validate_config(value.get('config', value)))
            self.load_form()
        except (ValueError, OSError, AttributeError) as error:
            self.status.set(str(error))

    def snapshot(self):
        self.commit_question()
        if not self.fields or tool.form_base(self.url.get().strip()) != self.loaded_base:
            raise ValueError('Hãy tải câu hỏi từ link hiện tại trước.')
        config = dict(self.config, form_url=self.url.get().strip(), count=int(self.count.get()),
                      batch_id=self.batch.get().strip(), distributions=copy.deepcopy(self.rules))
        return tool.validate_config(config)

    def load_form(self):
        try:
            base = tool.form_base(self.url.get().strip())
            if self.current is not None:
                self.commit_question()
        except ValueError as error:
            self.status.set(str(error))
            return
        def work():
            fields = tool.parse_schema(tool.fetch_form(base)[1])
            if self.config.get('form_url') == base:
                config = dict(self.config, distributions=self.rules)
                if set(self.rules) - {field['key'] for field in fields}:
                    raise ValueError('Phân bố có câu không còn trong form. Mở cấu hình đúng form trước.')
                for field in fields:
                    tool.field_weights(field, config)
            return ('form', base, fields)
        self.start('Đang tải câu hỏi…', work)

    def commit_question(self):
        if self.current is None:
            return
        values = self.weight_values()
        current_values = tool.field_weights(self.current, dict(self.config, distributions=self.rules))
        if values != current_values:
            self.rules[self.current['key']] = dict(zip(self.current['options'], values))

    def select_question(self, _=None):
        if self.busy or not self.tree.selection():
            return
        index = int(self.tree.selection()[0])
        if self.current is self.fields[index]:
            return
        try:
            self.commit_question()
        except ValueError:
            self.status.set('Trọng số phải là số không âm, tổng lớn hơn 0. Sửa câu hiện tại trước.')
            self.tree.selection_set(str(self.fields.index(self.current)))
            return
        self.current = self.fields[index]
        self.render_options()
        self.set_busy(False)

    def render_options(self):
        for child in self.options.winfo_children():
            child.destroy()
        self.editors = []
        self.choice_labels = []
        self.preview_label.configure(style='TLabel')
        if self.current is None:
            self.question_title.configure(text='Phân bố từng lựa chọn')
            self.preview.set('Tải form, sau đó chọn câu hỏi ở bên trái.')
            return
        self.question_title.configure(text=self.current['title'])
        for col, title in enumerate(['Lựa chọn', 'Trọng số', 'Tỷ lệ / số dòng']):
            ttk.Label(self.options, text=title, style='Heading.TLabel').grid(row=0, column=col, sticky='w', padx=4, pady=8)
        config = dict(self.config, distributions=self.rules)
        values = tool.field_weights(self.current, config)
        for index, (choice, value) in enumerate(zip(self.current['options'], values), 1):
            choice_label = ttk.Label(self.options, text=choice, wraplength=310)
            choice_label.grid(row=index, column=0, sticky='w', padx=4, pady=6)
            self.choice_labels.append(choice_label)
            variable = tk.StringVar(value=str(value))
            entry = ttk.Entry(self.options, textvariable=variable, width=8)
            entry.grid(row=index, column=1, sticky='w', padx=4)
            label = ttk.Label(self.options)
            label.grid(row=index, column=2, sticky='w', padx=4)
            self.editors.append((variable, entry, label))
            entry.bind('<FocusIn>', self.reveal_entry)
            variable.trace_add('write', lambda *_: self.update_preview())
        for child in self.options.winfo_children():
            child.bind('<MouseWheel>', self.scroll_options)
        self.canvas.yview_moveto(0)
        self.resize_options()
        self.update_preview()

    def weight_values(self):
        values = [float(variable.get().replace(',', '.')) for variable, _, _ in self.editors]
        tool.weights(values)
        return values

    def resize_options(self, event=None):
        width = event.width if event else self.canvas.winfo_width()
        self.canvas.itemconfigure(self.window, width=width)
        for label in self.choice_labels:
            label.configure(wraplength=max(120, width - 240))
        self.preview_label.configure(wraplength=max(200, width))

    def scroll_options(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def reveal_entry(self, event):
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        y = event.widget.winfo_y()
        height = event.widget.winfo_height()
        if y < top or y + height > bottom:
            target = y if y < top else y + height - self.canvas.winfo_height()
            self.canvas.yview_moveto(max(0, target) / max(1, self.options.winfo_height()))

    def update_preview(self):
        if not self.editors:
            return
        try:
            count = int(self.count.get())
            if count <= 0:
                raise ValueError()
            values = self.weight_values()
            numbers = tool.weights(values)
            allocated = tool.allocate(count, values)
            for (_, _, label), value, amount in zip(self.editors, numbers, allocated):
                label.configure(text='{:.1f}% / {}'.format(100 * value / sum(numbers), amount))
            self.preview_label.configure(style='TLabel')
            self.preview.set('Tổng {:g} trọng số → {} dòng. Tỷ lệ tự chuẩn hóa.'.format(sum(values), count))
        except (ValueError, OverflowError):
            for _, _, label in self.editors:
                label.configure(text='—')
            self.preview.set('Nhập số phản hồi nguyên dương và trọng số không âm, tổng lớn hơn 0.')
            self.preview_label.configure(style='Error.TLabel')

    def focus_weight(self):
        if self.editors:
            self.editors[0][1].focus_set()

    def apply_scales(self):
        try:
            self.commit_question()
            if self.current is None or not tool.is_likert(self.current):
                raise ValueError('Chọn một câu thang 1–5 trước.')
            values = tool.field_weights(self.current, dict(self.config, distributions=self.rules))
            targets = [field for field in self.fields if tool.is_likert(field)]
            for field in targets:
                self.rules[field['key']] = dict(zip(field['options'], values))
            self.status.set('Đã áp dụng tỷ lệ cho {} câu thang 1–5.'.format(len(targets)))
        except ValueError as error:
            self.status.set(str(error))

    def save_config(self, config):
        self.config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

    def prepare_batch(self):
        try:
            config = self.snapshot()
        except ValueError as error:
            self.status.set(str(error))
            return
        self.progress.configure(value=0, maximum=config['count'])
        def work():
            folder = tool.batch_path(config)
            tool.prepare(folder, config, self.fields)
            self.save_config(config)
            return ('done', 'Lô đã sẵn sàng. Xem CSV/báo cáo trước khi gửi.', str(folder))
        self.start('Đang tạo / kiểm tra lô…', work)

    def send(self, limit=None, resolution=None):
        try:
            config = self.snapshot()
            folder = tool.batch_path(config)
            if not folder.is_dir():
                raise ValueError('Hãy bấm Tạo / kiểm tra lô trước khi gửi.')
            tool.load_batch(folder, config, self.fields)
            self.save_config(config)
        except (ValueError, OSError) as error:
            self.status.set(str(error))
            return
        self.last_limit = limit
        def work():
            tool.send_batch(folder, config, self.fields, limit, resolution, self.stop,
                            lambda index, total: self.events.put(('progress', index, total)))
            rows, identity = tool.load_batch(folder, config, self.fields)
            pending, _ = tool.remaining_rows(rows, folder / 'send-log.jsonl', identity)
            confirmed = len(rows) - len(pending)
            return ('done', '{} / {} dòng đã xác nhận hoặc đối chiếu đã lưu. {}'.format(
                confirmed, len(rows), 'Đã dừng an toàn.' if self.stop.is_set() else 'Lượt chạy hoàn tất.'),
                    str(folder), confirmed, len(rows))
        self.start('Đang gửi; giữ nguyên lô và cấu hình để chạy tiếp.', work, can_stop=True)

    def start(self, message, job, can_stop=False):
        if self.busy:
            return
        self.stop.clear()
        self.can_stop = can_stop
        self.recovery.grid_remove()
        self.status.set(message)
        self.set_busy(True)
        def run():
            try:
                self.events.put(job())
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=run, daemon=False).start()

    def set_busy(self, busy):
        self.busy = busy
        for control in self.controls:
            control.configure(state='disabled' if busy else 'normal')
        for _, entry, _ in self.editors:
            entry.configure(state='disabled' if busy else 'normal')
        for control in [self.prepare_button, self.pilot_button, self.send_button]:
            if not self.fields:
                control.configure(state='disabled')
        self.apply_button.configure(state='normal' if not busy and self.current and tool.is_likert(self.current) else 'disabled')
        self.stop_button.configure(state='normal' if busy and self.can_stop else 'disabled')

    def request_stop(self):
        self.stop.set()
        self.status.set('Đang đợi lượt hiện tại hoàn tất; sẽ dừng trước dòng tiếp theo.')
        self.stop_button.configure(state='disabled')

    def poll(self):
        while not self.events.empty():
            event = self.events.get_nowait()
            if event[0] == 'progress':
                self.progress.configure(maximum=event[2], value=event[1])
                self.status.set('{}/{} dòng nhận xác nhận. Đối chiếu nội dung trên Forms/Sheet.'.format(event[1], event[2]))
                continue
            self.set_busy(False)
            if event[0] == 'form':
                _, base, fields = event
                if self.config.get('form_url') != base:
                    self.rules = {}
                    self.config.pop('likert_weights', None)
                self.fields = fields
                self.loaded_base = base
                self.config['form_url'] = base
                self.current = None
                self.tree.delete(*self.tree.get_children())
                for index, field in enumerate(fields):
                    self.tree.insert('', 'end', iid=str(index), text=field['title'])
                self.tree.selection_set('0')
                self.select_question()
                self.status.set('Đã tải {} câu hỏi. Chỉnh trọng số hoặc tạo lô.'.format(len(fields)))
                self.set_busy(False)
            elif event[0] == 'done':
                self.status.set(event[1])
                self.path_label.set(event[2])
                self.path_widget.grid(row=8, sticky='ew', pady=(4, 0))
                if len(event) > 3:
                    self.progress.configure(value=event[3], maximum=event[4])
            else:
                self.status.set(event[1])
                if event[1].startswith('Uncertain rows'):
                    self.recovery.grid(row=9, sticky='ew', pady=(8, 0))
            if self.closing:
                self.shutdown()
                return
        self.poll_id = self.root.after(100, self.poll)

    def shutdown(self):
        self.root.after_cancel(self.poll_id)
        self.root.destroy()

    def close(self):
        if self.busy:
            self.closing = True
            self.request_stop()
        else:
            self.shutdown()


if __name__ == '__main__':
    if tool.os.name == 'nt':
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    App(root)
    root.mainloop()
