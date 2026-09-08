#!/usr/bin/env python3
"""CleanYourWechatTool - 图形界面 (ttkbootstrap).

对标 CleanMyWechat (PyQt5) / WxCleaner (ttkbootstrap) 的产品形态:
让用户全程做选择题 (下拉/勾选/按钮), 不接触任何终端参数。
引擎层完全复用 engine/ (scanner/cleaner/dedup/whitelist), 本文件只做壳。

设计原则:
- 所有耗时操作 (扫描/清理/去重) 放后台线程, 结果经 queue 回到主线程
- 任何清理动作前必须先经过"预览文件清单"一步, 杜绝盲确认
- 白名单添加不要求用户知道 wxid (选填)
"""

from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
from collections import namedtuple
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk
import ttkbootstrap as ttk
from tkinter import filedialog, messagebox
from ttkbootstrap.constants import *

_BASE_DIR = Path(__file__).resolve().parent
for _p in (str(_BASE_DIR), str(_BASE_DIR / 'projects' / 'wechat-intelligence-hub')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine.scanner import discover_accounts, scan_account  # noqa: E402
from engine.cleaner import execute_slimming  # noqa: E402
from engine.common import format_bytes, parse_size_str  # noqa: E402
from engine.dedup import execute_dedup, find_duplicates  # noqa: E402
from engine.whitelist import WhiteListManager  # noqa: E402
from engine.state import StateManager  # noqa: E402

APP_TITLE = 'CleanYourWechatTool · 微信智能瘦身'
# macOS 专用系统字体 (苹方), 对齐原生中文应用的观感
FONT_NORMAL = ('PingFang SC', 11)
FONT_BOLD = ('PingFang SC', 11, 'bold')

# 选择题选项: 显示文案 -> 实际值
TIME_CHOICES = [
    ('清理 7 天前的文件', 7),
    ('清理 30 天前的文件', 30),
    ('清理 60 天前的文件', 60),
    ('清理 90 天前的文件 (推荐)', 90),
    ('清理 180 天前的文件', 180),
    ('清理 1 年前的文件', 365),
    ('不限时间 (全部)', 0),
]
SIZE_CHOICES = [
    ('不限大小', '0B'),
    ('大于 500KB', '500KB'),
    ('大于 1MB', '1MB'),
    ('大于 5MB', '5MB'),
    ('大于 10MB (推荐)', '10MB'),
    ('大于 50MB', '50MB'),
    ('大于 100MB', '100MB'),
    ('大于 500MB', '500MB'),
]
TYPE_LABELS = [('聊天视频', 'video'), ('接收的文件', 'file'), ('图片附件', 'attach'), ('临时缓存', 'cache'),
               ('渲染缓存与崩溃转储', 'radium'), ('运行日志', 'logs'), ('小程序插件包体', 'xplugin')]

_log = logging.getLogger('CleanYourWechatTool')

# 与引擎 SlimResult 对齐的结果容器: (freed_count, freed_bytes, protected_count, protected_bytes)
ExecResult = namedtuple('ExecResult', ['freed_count', 'freed_bytes', 'protected_count', 'protected_bytes'])


def execute_for_files(files, archive_to, whitelist_mgr, root_path=None):
    """按给定文件清单精确执行清理 (不再把过滤参数重新丢给引擎全量重跑).

    files: List[(Path, size, mtime)] —— 即预览清单中"未被排除"的文件。
    返回 ExecResult(freed_count, freed_bytes, protected_count, protected_bytes)。
    """
    import shutil as _shutil
    from engine.cleaner import move_to_trash, SAFE_SKIP_EXTS, PROTECTED_DIR_NAMES

    freed_count = freed_bytes = protected_count = protected_bytes = 0
    for fp, size, mtime in files:
        # 防御死线 (与引擎 execute_slimming 相同的物理层兜底):
        # 敏感后缀与运行时/组件目录任何情况下都不处理
        if Path(fp).suffix.lower() in SAFE_SKIP_EXTS:
            continue
        if any(part.lower() in PROTECTED_DIR_NAMES for part in Path(fp).parts):
            continue
        if whitelist_mgr:
            is_prot, _ = whitelist_mgr.is_protected(fp, mtime)
            if is_prot:
                protected_count += 1
                protected_bytes += size
                continue
        try:
            if archive_to:
                rel = fp.relative_to(root_path) if root_path else Path(fp.name)
                dest = Path(archive_to) / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    n = 2
                    while (dest.with_name(f"{dest.stem}_{n}{dest.suffix}")).exists():
                        n += 1
                    dest = dest.with_name(f"{dest.stem}_{n}{dest.suffix}")
                _shutil.move(str(fp), str(dest))
            else:
                move_to_trash(fp)
            freed_count += 1
            freed_bytes += size
        except (OSError, PermissionError, _shutil.Error):
            continue
    return ExecResult(freed_count, freed_bytes, protected_count, protected_bytes)


class CleanYourWechatApp:
    """单窗口三 Tab: 智能瘦身 / 重复文件去重 / 防删白名单."""

    def __init__(self, root: ttk.Window) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry('1020x740')
        self.root.minsize(920, 660)
        self.root.option_add('*Font', FONT_NORMAL)
        try:
            style = ttk.Style()
            style.configure('Treeview', font=FONT_NORMAL, rowheight=30)
            style.configure('Treeview.Heading', font=FONT_BOLD)
        except Exception:
            pass

        self.queue: 'queue.Queue[Tuple[str, Callable[..., None], Any]]' = queue.Queue()
        self.accounts = []
        self.current_categories: Dict[str, Any] = {}
        self.current_account: Optional[Any] = None
        self.preview_result = None
        self.tree_data: Dict[str, Dict[str, Any]] = {}  # iid -> {'path','size','mtime','included'}
        self._clean_menu = None
        self._build_ui()
        self._poll_queue()
        self.root.after(200, self._init_accounts)

    # ---------- UI 构建 ----------

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(16, 12, 16, 4))
        header.pack(fill=X)
        ttk.Label(header, text=APP_TITLE, font=FONT_BOLD).pack(side=LEFT)

        # 多账号选择: 下拉列出全部发现账号, 切换即重扫; 支持自定义微信目录
        self.account_box = ttk.Combobox(header, state='readonly', width=40)
        self.account_box.pack(side=RIGHT)
        self.account_box.bind('<<ComboboxSelected>>', self._on_account_selected)
        self.btn_recheck = ttk.Button(header, text='重新检测', command=self._init_accounts,
                                      bootstyle='secondary-outline')
        self.btn_recheck.pack(side=RIGHT, padx=(0, 8))
        self.btn_custom_dir = ttk.Button(header, text='自定义微信目录…', command=self._choose_custom_dir,
                                         bootstyle='secondary-outline')
        self.btn_custom_dir.pack(side=RIGHT, padx=(0, 8))
        self.account_hint = ttk.StringVar(value='正在探测微信账号…')
        ttk.Label(header, textvariable=self.account_hint, bootstyle='secondary').pack(side=RIGHT, padx=(0, 8))

        self.notebook = ttk.Notebook(self.root, padding=8)
        self.notebook.pack(fill=BOTH, expand=YES, padx=12, pady=(4, 0))
        self._build_clean_tab()
        self._build_dedup_tab()
        self._build_whitelist_tab()

        self.status_var = ttk.StringVar(value='就绪')
        self.achievement_var = ttk.StringVar(value='')
        status = ttk.Frame(self.root, padding=(16, 6, 16, 10))
        status.pack(fill=X, side=BOTTOM)
        self.btn_cancel = ttk.Button(status, text='取消', command=self._cancel_running,
                                     bootstyle='danger-outline')
        self.status_label = ttk.Label(status, textvariable=self.status_var, bootstyle='info')
        self.status_label.pack(side=LEFT)
        self.achievement_label = ttk.Label(status, textvariable=self.achievement_var,
                                           font=FONT_BOLD, bootstyle='success')
        self.achievement_label.pack(side=RIGHT)
        self._refresh_achievement_async()

    # ---- Tab 1: 智能瘦身 ----

    def _build_clean_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text='  智能瘦身  ')

        cond = ttk.Labelframe(tab, text='第一步 · 用选择题设置清理条件', padding=12)
        cond.pack(fill=X, pady=(0, 10))

        row1 = ttk.Frame(cond)
        row1.pack(fill=X, pady=4)
        ttk.Label(row1, text='时间范围:').pack(side=LEFT)
        self.time_box = ttk.Combobox(row1, values=[t[0] for t in TIME_CHOICES], state='readonly', width=28)
        self.time_box.current(3)
        self.time_box.pack(side=LEFT, padx=(6, 20))

        ttk.Label(row1, text='文件大小:').pack(side=LEFT)
        self.size_box = ttk.Combobox(row1, values=[s[0] for s in SIZE_CHOICES], state='readonly', width=18)
        self.size_box.current(4)
        self.size_box.pack(side=LEFT, padx=6)

        row2 = ttk.Frame(cond)
        row2.pack(fill=X, pady=4)
        ttk.Label(row2, text='文件类型:').pack(side=LEFT)
        self.type_vars: List[Tuple[str, ttk.BooleanVar]] = []
        for label, key in TYPE_LABELS:
            var = tk.BooleanVar(value=key in ('video', 'file'))
            ttk.Checkbutton(row2, text=label, variable=var, bootstyle='primary-round-toggle').pack(side=LEFT, padx=(6, 14))
            self.type_vars.append((key, var))

        row3 = ttk.Frame(cond)
        row3.pack(fill=X, pady=4)
        ttk.Label(row3, text='处理方式:').pack(side=LEFT)
        self.mode_var = ttk.StringVar(value='trash')
        ttk.Radiobutton(row3, text='移入系统废纸篓 (随时可还原, 推荐)', variable=self.mode_var, value='trash',
                        bootstyle='info-toolbutton').pack(side=LEFT, padx=6)
        ttk.Radiobutton(row3, text='无损归档到指定目录', variable=self.mode_var, value='archive',
                        bootstyle='warning-toolbutton').pack(side=LEFT, padx=6)
        self.archive_dir_var = ttk.StringVar(value='')
        ttk.Button(row3, text='选择归档目录…', command=self._choose_archive_dir, bootstyle='secondary-outline').pack(side=LEFT, padx=10)

        actions = ttk.Frame(tab)
        actions.pack(fill=X, pady=(0, 8))
        self.btn_preview = ttk.Button(actions, text='① 预览将处理的文件', command=self._start_preview, bootstyle='primary')
        self.btn_preview.pack(side=LEFT)
        self.btn_execute = ttk.Button(actions, text='② 执行清理', command=self._start_clean, bootstyle='danger',
                                      state=DISABLED)
        self.btn_execute.pack(side=LEFT, padx=10)
        ttk.Label(actions, text='先预览清单, 确认无误后再执行 —— 绝不盲删',
                  bootstyle='secondary').pack(side=LEFT, padx=10)

        result_frame = ttk.Labelframe(tab, text='第二步 · 勾选要清理的文件 (取消勾选 = 排除)', padding=8)
        result_frame.pack(fill=BOTH, expand=YES)

        tree_frame = ttk.Frame(result_frame)
        tree_frame.pack(fill=BOTH, expand=YES)
        columns = ('inc', 'size', 'date', 'path')
        self.clean_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=13, selectmode='extended')
        self.clean_tree.heading('inc', text='含')
        self.clean_tree.column('inc', width=36, anchor=CENTER)
        for col, text, width in (('size', '大小', 90), ('date', '最后修改', 110), ('path', '文件路径', 600)):
            self.clean_tree.heading(col, text=text)
            self.clean_tree.column(col, width=width, anchor=W if col == 'path' else E)
        # 排除行置灰; 已加入白名单保护的行用蓝色标识
        self.clean_tree.tag_configure('excluded', foreground='#8e8e93')
        self.clean_tree.tag_configure('protected', foreground='#007aff')
        self.clean_tree.pack(side=LEFT, fill=BOTH, expand=YES)
        scroll = ttk.Scrollbar(tree_frame, command=self.clean_tree.yview, orient=VERTICAL)
        self.clean_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=RIGHT, fill=Y)

        ctrl = ttk.Frame(result_frame)
        ctrl.pack(fill=X, pady=(6, 0))
        ttk.Button(ctrl, text='全部勾选', command=lambda: self._set_all_included(True),
                   bootstyle='success-outline').pack(side=LEFT)
        ttk.Button(ctrl, text='全部排除', command=lambda: self._set_all_included(False),
                   bootstyle='danger-outline').pack(side=LEFT, padx=6)
        ttk.Label(ctrl, text='单击「含」列切换 · Shift/Ctrl 多选 · 右键可保护/排除选中行',
                  bootstyle='secondary').pack(side=LEFT, padx=10)

        self.clean_stats_var = ttk.StringVar(value='')
        ttk.Label(result_frame, textvariable=self.clean_stats_var, bootstyle='info').pack(fill=X, pady=(4, 0))

        # 右键菜单: 访达定位 / 包含/排除选中行 / 加入白名单保护
        self._clean_menu = tk.Menu(self.clean_tree, tearoff=0)
        self._clean_menu.add_command(label='在访达中显示', command=self._reveal_selected)
        self._clean_menu.add_separator()
        self._clean_menu.add_command(label='包含选中行', command=self._include_selected)
        self._clean_menu.add_command(label='排除选中行', command=self._exclude_selected)
        self._clean_menu.add_separator()
        self._clean_menu.add_command(label='加入白名单保护', command=self._protect_selected)
        self.clean_tree.bind('<Button-1>', self._on_tree_click)
        self.clean_tree.bind('<Button-3>', self._on_tree_rightclick)
        # macOS 原生习惯: 双击用默认程序打开, 空格唤起 Quick Look 预览
        self.clean_tree.bind('<Double-1>', self._open_selected_file)
        self.clean_tree.bind('<space>', self._quicklook_selected_file)

        # 表头点击排序: 大小/日期/路径, 再次点击反转方向
        for col, key in (('inc', None), ('size', 'size'), ('date', 'mtime'), ('path', 'path')):
            if key:
                self.clean_tree.heading(col, text={'size': '大小 ▾', 'date': '最后修改', 'path': '文件路径'}[col],
                                        command=lambda c=key: self._sort_clean_tree(c))

        self.clean_result_var = ttk.StringVar(value='')
        ttk.Label(tab, textvariable=self.clean_result_var, font=('-size', 11, '-weight', 'bold'),
                  bootstyle='success').pack(fill=X, pady=(6, 0))

    # ---- Tab 2: 去重 ----

    def _build_dedup_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text='  重复文件去重  ')

        cond = ttk.Labelframe(tab, text='多群转发的同一份文件只占一份磁盘 (APFS 硬链接, 聊天记录不受影响)', padding=12)
        cond.pack(fill=X, pady=(0, 10))
        ttk.Label(cond, text='只检查大于:').pack(side=LEFT)
        self.dedup_size_box = ttk.Combobox(cond, values=[s[0] for s in SIZE_CHOICES], state='readonly', width=18)
        self.dedup_size_box.current(3)
        self.dedup_size_box.pack(side=LEFT, padx=6)
        self.btn_dedup_scan = ttk.Button(cond, text='① 扫描重复文件', command=self._start_dedup_scan, bootstyle='primary')
        self.btn_dedup_scan.pack(side=LEFT, padx=14)
        self.btn_dedup_exec = ttk.Button(cond, text='② 硬链接去重 (零风险)', command=self._start_dedup_exec,
                                         bootstyle='success', state=DISABLED)
        self.btn_dedup_exec.pack(side=LEFT, padx=6)

        frame = ttk.Labelframe(tab, text='重复文件清单 (绿色=保留底稿, 红色=重复副本; 右键可换保留哪一份)', padding=8)
        frame.pack(fill=BOTH, expand=YES)
        dcols = ('group', 'role', 'size', 'path')
        self.dedup_tree = ttk.Treeview(frame, columns=dcols, show='headings', height=17,
                                       selectmode='browse')
        for col, text, width, anchor in (('group', '组', 50, CENTER), ('role', '角色', 90, CENTER),
                                         ('size', '单文件大小', 100, E), ('path', '文件路径', 560, W)):
            self.dedup_tree.heading(col, text=text)
            self.dedup_tree.column(col, width=width, anchor=anchor)
        self.dedup_tree.tag_configure('keep', foreground='#1a9c50')
        self.dedup_tree.tag_configure('dup', foreground='#d70015')
        self.dedup_tree.tag_configure('grouphead', background='#f2f2f7', font=FONT_BOLD)
        self.dedup_tree.pack(side=LEFT, fill=BOTH, expand=YES)
        dscroll = ttk.Scrollbar(frame, command=self.dedup_tree.yview, orient=VERTICAL)
        self.dedup_tree.configure(yscrollcommand=dscroll.set)
        dscroll.pack(side=RIGHT, fill=Y)

        # 右键: 切换保留副本 / 访达定位; 双击定位
        self._dedup_menu = tk.Menu(self.dedup_tree, tearoff=0)
        self._dedup_menu.add_command(label='将此文件设为保留底稿', command=self._keep_dedup_copy)
        self._dedup_menu.add_command(label='在访达中显示', command=self._reveal_dedup_file)
        self.dedup_tree.bind('<Button-3>', self._on_dedup_rightclick)
        self.dedup_tree.bind('<Double-1>', self._reveal_dedup_file)
        self.dedup_result = []
        self._dedup_tree_meta: Dict[str, Tuple[int, int]] = {}  # iid -> (组下标, 文件下标)

    # ---- Tab 3: 白名单 ----

    def _build_whitelist_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text='  防删白名单  ')
        ttk.Label(tab, text='命中的文件在任何清理/去重中都会被跳过。填名字和关键词即可, 微信号/群ID 可不填。',
                  bootstyle='secondary').pack(fill=X, pady=(0, 8))

        form = ttk.Labelframe(tab, text='添加保护规则', padding=10)
        form.pack(fill=X, pady=(0, 10))
        row1 = ttk.Frame(form)
        row1.pack(fill=X, pady=3)
        ttk.Label(row1, text='保护对象名称 (必填, 如: 老婆 / 某某公司):').pack(side=LEFT)
        self.wl_name = ttk.StringVar()
        ttk.Entry(row1, textvariable=self.wl_name, width=26).pack(side=LEFT, padx=8)
        ttk.Label(row1, text='微信号/群ID (可不填):').pack(side=LEFT, padx=(14, 0))
        self.wl_wxid = ttk.StringVar()
        ttk.Entry(row1, textvariable=self.wl_wxid, width=22).pack(side=LEFT, padx=8)

        row2 = ttk.Frame(form)
        row2.pack(fill=X, pady=3)
        ttk.Label(row2, text='保护关键词 (逗号分隔, 文件名含关键词即受保护, 如: 合同,对账单,宝宝):').pack(side=LEFT)
        self.wl_keywords = ttk.StringVar()
        ttk.Entry(row2, textvariable=self.wl_keywords, width=30).pack(side=LEFT, padx=8)

        row3 = ttk.Frame(form)
        row3.pack(fill=X, pady=6)
        ttk.Button(row3, text='添加保护规则', command=self._add_whitelist, bootstyle='success').pack(side=LEFT)
        ttk.Button(row3, text='移除选中规则', command=self._remove_whitelist, bootstyle='danger-outline').pack(side=LEFT, padx=10)
        ttk.Button(row3, text='刷新列表', command=lambda: self._load_whitelist_async(), bootstyle='secondary-outline').pack(side=LEFT)

        frame = ttk.Labelframe(tab, text='当前生效的保护规则', padding=8)
        frame.pack(fill=BOTH, expand=YES)
        columns = ('name', 'wxid', 'keywords')
        self.wl_tree = ttk.Treeview(frame, columns=columns, show='headings', height=12)
        for col, text, width in (('name', '保护对象', 180), ('wxid', '微信号/群ID', 220), ('keywords', '保护关键词', 380)):
            self.wl_tree.heading(col, text=text)
            self.wl_tree.column(col, width=width, anchor=W)
        self.wl_tree.pack(fill=BOTH, expand=YES)

    # ---------- 后台任务调度 ----------

    def _run_async(self, fn: Callable[[Callable[[str], None]], Any],
                   on_done: Callable[[Any], None], busy_text: str,
                   cancellable: bool = False) -> None:
        """后台执行 fn(progress_cb); cancellable=True 时显示取消按钮."""
        self.status_var.set(busy_text)
        self._set_busy(True)
        self._cancel_event = threading.Event() if cancellable else None
        if cancellable:
            self.btn_cancel.pack(side=RIGHT, before=self.status_label)
        else:
            self.btn_cancel.pack_forget()

        def progress_cb(msg: str) -> None:
            self.queue.put(('progress', None, msg))

        import inspect
        takes_progress = len(inspect.signature(fn).parameters) >= 1

        def worker() -> None:
            try:
                result = fn(progress_cb) if takes_progress else fn()
                self.queue.put(('ok', on_done, result))
            except Exception as exc:  # 后台线程异常统一回到主线程呈现
                self.queue.put(('err', on_done, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_running(self) -> None:
        if getattr(self, '_cancel_event', None) is not None:
            self._cancel_event.set()
            self.status_var.set('正在取消…')

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, on_done, payload = self.queue.get_nowait()
                if kind == 'progress':
                    # 进度消息不打断任务状态, 只刷新文案
                    self.status_var.set(payload)
                    continue
                if self.btn_cancel.winfo_ismapped():
                    self.btn_cancel.pack_forget()
                self._set_busy(False)
                self.status_var.set('就绪')
                if kind == 'err':
                    cancelled = isinstance(payload, RuntimeError) and 'cancelled' in str(payload).lower()
                    if cancelled:
                        self.status_var.set('已取消')
                    else:
                        messagebox.showerror('出错了', f'操作失败: {payload}')
                    return
                on_done(payload)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _set_busy(self, busy: bool) -> None:
        if busy:
            for btn in (self.btn_preview, self.btn_execute, self.btn_dedup_scan, self.btn_dedup_exec):
                btn.state(['disabled'])
            return
        self.btn_preview.state(['!disabled'])
        self.btn_dedup_scan.state(['!disabled'])
        self.btn_execute.state(['!disabled' if self.preview_result else 'disabled'])
        self.btn_dedup_exec.state(['!disabled' if self.dedup_result else 'disabled'])

    # ---------- 成就 ----------

    def _refresh_achievement_async(self) -> None:
        def job():
            state = StateManager()
            return state.total_freed_bytes, state.total_cleans, state.total_dedups

        self._run_async(job, self._after_refresh_achievement, '正在读取累计统计…')

    def _after_refresh_achievement(self, data) -> None:
        freed, cleans, dedups = data
        if freed <= 0:
            self.achievement_var.set('')
            return
        self.achievement_var.set(
            f'🏆 已累计为这台 Mac 释放 {format_bytes(freed)} (清理 {cleans} 次 / 去重 {dedups} 次)')

    # ---------- 账号 ----------

    @staticmethod
    def _wechat_running() -> bool:
        """检测微信是否正在运行 (macOS 微信进程名为 WeChat)."""
        try:
            result = subprocess.run(['pgrep', '-x', 'WeChat'], capture_output=True, timeout=5)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _warn_wechat_running(self) -> bool:
        """微信运行中时弹窗提醒; 返回 True 表示用户选择仍要继续."""
        return messagebox.askyesno(
            '检测到微信正在运行',
            '微信正在运行。清理过程中微信可能:\n'
            '  · 持续写入新的缓存文件，影响统计准确性\n'
            '  · 极少数情况下锁定正在接收的文件\n\n'
            '建议先完全退出微信 (Cmd+Q) 再执行清理。\n\n是否仍然继续?',
            icon='warning',
        )

    def _init_accounts(self) -> None:
        self._run_async(lambda: self._load_accounts(), self._after_accounts, '正在探测微信账号…')

    def _load_accounts(self, custom_path=None):
        self.accounts = discover_accounts(custom_path)
        if self.accounts:
            self.current_account = self.accounts[0]
            self.current_categories = scan_account(self.current_account)
        return self.accounts

    def _account_choices(self) -> List[str]:
        choices = []
        for acc in self.accounts:
            try:
                cats = scan_account(acc)
                total = sum(c.total_bytes for c in cats.values())
                choices.append(f'{acc.account_id} · {acc.version_type} · {format_bytes(total)}')
            except Exception:
                choices.append(f'{acc.account_id} · {acc.version_type}')
        return choices

    def _after_accounts(self, accounts) -> None:
        if not accounts:
            self.account_box.set('')
            self.account_hint.set('未发现微信数据目录')
            # 两种常见原因: ①从未在此 Mac 登录微信 ②App 未获得
            # "完全磁盘访问权限"——微信容器目录受 TCC 保护, 无权限时
            # 扫描到的目录为空。给出可操作的修复引导, 而不是让用户猜。
            offered = messagebox.askyesno(
                '未找到微信数据',
                '没有找到可分析的微信账号目录。常见原因:\n\n'
                '① 本机从未登录过桌面版微信\n'
                '   → 请先登录一次微信, 再重新打开本工具。\n\n'
                '② macOS 隐私权限未授权 (最常见)\n'
                '   → 打开 系统设置 → 隐私与安全性 → 完全磁盘访问权限,\n'
                '     将 CleanYourWechatTool 加入列表后重启本工具。\n\n'
                '微信容器位于 ~/Library/Containers/com.tencent.xinWeChat,\n'
                '没有"完全磁盘访问权限"时任何工具都无法读取它。\n\n'
                '是否现在打开系统设置 (完全磁盘访问权限)?',
            )
            if offered:
                subprocess.run(['open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'],
                               check=False)
            return
        self.account_box['values'] = self._account_choices()
        self.account_box.current(0)
        self._apply_current_account()

    def _on_account_selected(self, _event=None) -> None:
        idx = self.account_box.current()
        if 0 <= idx < len(self.accounts):
            self.current_account = self.accounts[idx]
            self._run_async(lambda: scan_account(self.current_account), self._after_switch,
                            f'正在扫描账号 {self.current_account.account_id}…')

    def _after_switch(self, categories) -> None:
        self.current_categories = categories
        self._apply_current_account()

    def _choose_custom_dir(self) -> None:
        chosen = filedialog.askdirectory(title='选择微信数据目录 (xwechat_files 或账号目录)')
        if not chosen:
            return
        self._run_async(lambda: self._load_accounts(custom_path=chosen), self._after_accounts,
                        '正在扫描自定义目录…')

    def _apply_current_account(self) -> None:
        """当前账号变化后: 刷新提示、清空瘦身清单与去重结果、重置按钮状态."""
        acc = self.current_account
        if not acc:
            return
        idx = next((i for i, a in enumerate(self.accounts) if a.account_id == acc.account_id), None)
        if idx is not None:
            self.account_box.current(idx)
        total = sum(c.total_bytes for c in self.current_categories.values())
        self.account_hint.set(f'{acc.version_type} · 共 {format_bytes(total)}')
        # 清空与旧账号相关的预览/结果, 避免跨账号数据错乱
        self.preview_result = None
        self.dedup_result = []
        for item in self.clean_tree.get_children():
            self.clean_tree.delete(item)
        self.tree_data.clear()
        self.clean_result_var.set(f'当前账号: {acc.account_id} — 设置条件后点"① 预览将处理的文件"')
        self.btn_execute.state(['disabled'])
        self.btn_dedup_exec.state(['disabled'])
        for item in self.dedup_tree.get_children():
            self.dedup_tree.delete(item)
        self._dedup_tree_meta.clear()
        self.dedup_tree.insert('', END, values=('', '', '',
                                                f'当前账号: {acc.account_id} — 设置阈值后点"① 扫描重复文件"。'))

    # ---------- 瘦身 ----------

    def _collect_args(self) -> Tuple[int, int, List[str], Optional[Path]]:
        days = TIME_CHOICES[self.time_box.current()][1]
        min_size = parse_size_str(SIZE_CHOICES[self.size_box.current()][1])
        types = [k for k, v in self.type_vars if v.get()]
        if not types:
            raise ValueError('请至少勾选一种文件类型')
        archive_to = None
        if self.mode_var.get() == 'archive':
            raw = self.archive_dir_var.get().strip()
            if not raw:
                raise ValueError('归档模式请先选择一个目标目录 (建议外接硬盘)')
            archive_to = Path(raw)
        return days, min_size, types, archive_to

    def _choose_archive_dir(self) -> None:
        chosen = filedialog.askdirectory(title='选择归档目录 (建议外接硬盘/NAS)')
        if chosen:
            self.archive_dir_var.set(chosen)
            self.mode_var.set('archive')

    def _start_preview(self) -> None:
        try:
            days, min_size, types, archive_to = self._collect_args()
        except ValueError as exc:
            messagebox.showwarning('还差一步', str(exc))
            return
        if not self.current_account:
            messagebox.showwarning('提示', '未发现微信账号目录')
            return
        acc, cats = self.current_account, self.current_categories

        def job(progress_cb):
            return execute_slimming(acc, cats, days, min_size, types, dry_run=True,
                                    archive_to=archive_to, whitelist_mgr=self._whitelist(),
                                    progress_cb=progress_cb, cancel_event=self._cancel_event)

        self._run_async(job, lambda res: self._after_preview(res, acc, archive_to),
                        '正在扫描匹配文件…', cancellable=True)

    def _after_preview(self, res, acc, archive_to) -> None:
        self.preview_result = res if res.freed_count > 0 else None
        for item in self.clean_tree.get_children():
            self.clean_tree.delete(item)
        self.tree_data.clear()
        mode_text = '移入废纸篓' if archive_to is None else f'归档到 {archive_to}'
        if res.freed_count == 0:
            self.clean_result_var.set('没有符合条件的文件, 无需清理。')
            self.btn_execute.state(['disabled'])
            self._refresh_clean_stats()
            return
        files = sorted(res.affected_files, key=lambda t: t[1], reverse=True)
        for fp, size, mtime in files:
            try:
                rel = fp.relative_to(acc.root_path)
            except ValueError:
                rel = fp
            iid = self.clean_tree.insert(
                '', END,
                values=('☑', format_bytes(size), datetime.fromtimestamp(mtime).strftime('%Y-%m-%d'), str(rel)),
                tags=())
            self.tree_data[iid] = {'path': fp, 'size': size, 'mtime': mtime, 'included': True}
        summary = f'共 {res.freed_count:,} 个文件 / {format_bytes(res.freed_bytes)} —— 取消勾选可排除, 确认后点"② 执行清理"'
        if res.protected_count:
            summary += f' (白名单已保护 {res.protected_count:,} 个文件)'
        self.clean_result_var.set(summary)
        self.btn_execute.state(['!disabled'])
        self._refresh_clean_stats()
        self.status_var.set(f'预览完成: {len(files):,} 个文件, 将{mode_text}')

    # ---- 清单勾选/排除交互 ----

    def _set_included(self, iid: str, included: bool) -> None:
        data = self.tree_data.get(iid)
        if not data:
            return
        data['included'] = included
        self.clean_tree.set(iid, 'inc', '☑' if included else '☐')
        # 被排除 -> 置灰; 否则清除 excluded 标签 (保留 protected 标签如需)
        tags = list(self.clean_tree.item(iid, 'tags'))
        if 'excluded' in tags:
            tags.remove('excluded')
        if not included:
            tags.append('excluded')
        self.clean_tree.item(iid, tags=tuple(tags))
        self._refresh_clean_stats()

    def _set_all_included(self, included: bool) -> None:
        for iid in self.tree_data:
            self._set_included(iid, included)

    def _include_selected(self) -> None:
        for iid in self.clean_tree.selection():
            self._set_included(iid, True)

    def _exclude_selected(self) -> None:
        for iid in self.clean_tree.selection():
            self._set_included(iid, False)

    def _on_tree_click(self, event) -> None:
        """单击「含」列单元格切换该行的勾选状态; 点其它列只用于选择(支持扩展多选)."""
        region = self.clean_tree.identify('region', event.x, event.y)
        col = self.clean_tree.identify_column(event.x)
        if region != 'cell' or col != '#1':
            return
        iid = self.clean_tree.identify_row(event.y)
        if iid and iid in self.tree_data:
            self._set_included(iid, not self.tree_data[iid]['included'])

    def _on_tree_rightclick(self, event) -> None:
        iid = self.clean_tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.clean_tree.selection():
            self.clean_tree.selection_set(iid)
        self._clean_menu.tk_popup(event.x_root, event.y_root)

    def _protect_selected(self) -> None:
        """把选中文件加入防删白名单 (可保护), 并立刻在当前清单中排除它们."""
        sel = self.clean_tree.selection()
        if not sel:
            messagebox.showinfo('提示', '请先选中要保护的文件 (单击行选中, Shift/Ctrl 多选)')
            return
        added = 0
        for iid in sel:
            data = self.tree_data.get(iid)
            if not data:
                continue
            fp = data['path']
            stem = fp.stem or fp.name
            try:
                self._whitelist().add(name=f'文件:{stem}', wxid=f'file:{stem}',
                                      protect='absolute', keywords=[stem])
                added += 1
                # 受保护: 置蓝 + 本此执行排除
                tags = list(self.clean_tree.item(iid, 'tags'))
                if 'excluded' in tags:
                    tags.remove('excluded')
                if 'protected' not in tags:
                    tags.append('protected')
                self.clean_tree.item(iid, tags=tuple(tags))
                self._set_included(iid, False)
            except Exception as exc:  # 白名单写入失败不应中断其余文件
                _log.warning('加入白名单失败 %s: %s', fp, exc)
        if added:
            self._refresh_clean_stats()
            messagebox.showinfo('已保护', f'已将 {added} 个文件加入防删白名单, 本次及之后的清理/去重都会跳过它们。')

    def _selected_tree_data(self) -> Optional[Dict[str, Any]]:
        """当前选中行的文件数据 (无选中返回 None)."""
        sel = self.clean_tree.selection()
        if not sel:
            return None
        return self.tree_data.get(sel[0])

    def _open_selected_file(self, _event=None) -> None:
        """双击: 用系统默认程序打开该文件."""
        data = self._selected_tree_data()
        if not data:
            return
        fp = Path(data['path'])
        if fp.exists():
            subprocess.Popen(['open', str(fp)])
        else:
            messagebox.showwarning('文件不存在', '该文件当前不在磁盘上 (可能已被清理或移动)。')

    def _quicklook_selected_file(self, _event=None) -> None:
        """空格: 唤起 macOS Quick Look 快速预览."""
        data = self._selected_tree_data()
        if not data:
            return
        fp = Path(data['path'])
        if not fp.exists():
            messagebox.showwarning('文件不存在', '该文件当前不在磁盘上 (可能已被清理或移动)。')
            return
        # qlmanage 会阻塞到预览关闭, 必须用 Popen 非阻塞启动
        subprocess.Popen(['qlmanage', '-p', str(fp)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    def _reveal_selected(self) -> None:
        """在访达中定位选中文件 (右键菜单)."""
        sel = self.clean_tree.selection()
        if not sel:
            messagebox.showinfo('提示', '请先选中一行')
            return
        data = self.tree_data.get(sel[0])
        if data and Path(data['path']).exists():
            subprocess.run(['open', '-R', str(data['path'])], check=False)
        else:
            messagebox.showwarning('文件不存在', '该文件当前不在磁盘上 (可能已被清理或移动)。')

    def _sort_clean_tree(self, key: str) -> None:
        """表头点击排序: 重排行, 保持每行 ☑/☐ 状态与排除/保护标识."""
        current_dir = getattr(self, '_sort_dir', {}).get(key, True)
        self._sort_dir = {key: not current_dir}

        def sort_value(iid):
            d = self.tree_data.get(iid, {})
            if key == 'size':
                return d.get('size', 0)
            if key == 'mtime':
                return d.get('mtime', 0)
            return str(d.get('path', ''))

        iids = list(self.clean_tree.get_children())
        iids.sort(key=sort_value, reverse=current_dir)
        for iid in iids:
            self.clean_tree.move(iid, '', END)
        # 更新表头箭头指示
        arrow = '▾' if current_dir else '▴'
        label_map = {'size': '大小', 'date': '最后修改', 'path': '文件路径'}
        for col in ('size', 'date', 'path'):
            if col == key:
                self.clean_tree.heading(col, text=f'{label_map[col]} {arrow}',
                                        command=lambda k=key: self._sort_clean_tree(k))
            else:
                self.clean_tree.heading(col, text=label_map[col],
                                        command=lambda k=key: self._sort_clean_tree(k))

    def _refresh_clean_stats(self) -> None:
        inc = [d for d in self.tree_data.values() if d['included']]
        n = len(inc)
        m = len(self.tree_data) - n
        x = sum(d['size'] for d in inc)
        self.clean_stats_var.set(f'已选 {n} 个 / 排除 {m} 个 / 将释放 {format_bytes(x)}')

    def _start_clean(self) -> None:
        if not self.preview_result:
            messagebox.showinfo('先预览', '请先点击"① 预览将处理的文件"核对清单')
            return
        # 仅对清单中"仍勾选(未排除)"的文件精确执行, 不再把过滤参数重新丢给引擎全量重跑
        included = [(d['path'], d['size'], d['mtime']) for d in self.tree_data.values() if d['included']]
        if not included:
            messagebox.showwarning('没有可清理的文件', '清单里没有勾选任何文件。\n取消勾选的行会被排除, 不会删除。')
            return
        total_inc = len(included)
        bytes_inc = sum(s for _, s, _ in included)
        # 防呆: 微信运行中清理, 统计不准且可能锁定正接收的文件
        if self._wechat_running() and not self._warn_wechat_running():
            return
        if not messagebox.askyesno('最后确认',
                                       f'将处理 {total_inc:,} 个文件 (释放 {format_bytes(bytes_inc)})。\n'
                                       '文件会进入废纸篓/归档目录, 可随时还原。\n\n确定执行吗?'):
            return
        try:
            _, _, _, archive_to = self._collect_args()
        except ValueError as exc:
            messagebox.showwarning('还差一步', str(exc))
            return
        acc = self.current_account

        def job():
            freed_count, freed_bytes, protected_count, protected_bytes = execute_for_files(
                included, archive_to, self._whitelist(), root_path=acc.root_path)
            StateManager().record_clean(freed_count, freed_bytes, protected_count, protected_bytes,
                                        is_archive=bool(archive_to))
            return ExecResult(freed_count, freed_bytes, protected_count, protected_bytes)

        self._run_async(job, lambda r: self._after_clean(r, archive_to), '正在执行清理…')

    def _after_clean(self, res, archive_to) -> None:
        self.preview_result = None
        self.btn_execute.state(['disabled'])
        for item in self.clean_tree.get_children():
            self.clean_tree.delete(item)
        self.tree_data.clear()
        self._refresh_clean_stats()
        msg = f'✅ 完成! 释放 {format_bytes(res.freed_bytes)} (处理 {res.freed_count:,} 个文件)'
        if res.protected_count:
            msg += f', 白名单保护 {res.protected_count:,} 个未触碰'
        tips = ['去 微信 → 设置 → 通用 → 存储空间 里核对占用变化 (微信的统计可能延迟刷新)']
        tips.append('清空系统废纸篓后磁盘空间才会真正释放' if archive_to is None
                    else f'文件已完整保存在: {archive_to}')
        self.clean_result_var.set(msg)
        # 后悔药: 完成后一键直达废纸篓 (可还原) 或归档目录 (核对文件)
        target_dir = Path.home() / '.Trash' if archive_to is None else Path(archive_to)
        open_dir = messagebox.askyesno(
            '清理完成',
            msg + '\n\n' + '\n'.join('· ' + t for t in tips)
            + f'\n\n是否立即打开 {"系统废纸篓" if archive_to is None else "归档目录"}?',
        )
        if open_dir:
            subprocess.run(['open', str(target_dir)], check=False)
        self._refresh_achievement_async()
        self._init_accounts()

    # ---------- 去重 ----------

    def _start_dedup_scan(self) -> None:
        if not self.current_account:
            messagebox.showwarning('提示', '未发现微信账号目录')
            return
        min_size = parse_size_str(SIZE_CHOICES[self.dedup_size_box.current()][1])
        acc, cats = self.current_account, self.current_categories

        def job(progress_cb):
            return find_duplicates(cats, ['video', 'file', 'attach'], min_size_bytes=min_size,
                                   whitelist_mgr=self._whitelist(),
                                   progress_cb=progress_cb, cancel_event=self._cancel_event)

        self._run_async(job, self._after_dedup_scan, '正在计算文件指纹 (大文件可能需要一些时间)…',
                        cancellable=True)

    def _after_dedup_scan(self, groups) -> None:
        self.dedup_result = [g for g in groups if g.wasted_count > 0]
        self._dedup_tree_meta.clear()
        for item in self.dedup_tree.get_children():
            self.dedup_tree.delete(item)
        self.btn_dedup_exec.state(['disabled'])
        if not self.dedup_result:
            self.dedup_tree.insert('', END, values=('', '', '', '✅ 未发现重复文件, 当前没有可释放的冗余空间。'))
            return
        total_saving = sum(g.saving_bytes for g in self.dedup_result)
        for idx, g in enumerate(self.dedup_result):
            head = self.dedup_tree.insert('', END, tags=('grouphead',),
                                          values=(f'第 {idx + 1} 组', f'{len(g.files)} 份',
                                                  f'{format_bytes(g.file_size)}/个',
                                                  f'可释放 {format_bytes(g.saving_bytes)}'))
            for fi, fp in enumerate(g.files):
                role = '保留底稿' if fi == 0 else '重复副本'
                tag = 'keep' if fi == 0 else 'dup'
                iid = self.dedup_tree.insert(head, END, values=('', role, format_bytes(g.file_size), str(fp)),
                                             tags=(tag,))
                self._dedup_tree_meta[iid] = (idx, fi)
            self.dedup_tree.item(head, open=True)
        self.status_var.set(f'发现 {len(self.dedup_result)} 组重复, 可释放 {format_bytes(total_saving)}')

    def _on_dedup_rightclick(self, event) -> None:
        iid = self.dedup_tree.identify_row(event.y)
        if iid and self._dedup_tree_meta.get(iid):
            self.dedup_tree.selection_set(iid)
            self._dedup_menu.tk_popup(event.x_root, event.y_root)

    def _dedup_selected(self) -> Optional[Tuple[int, int]]:
        sel = self.dedup_tree.selection()
        if not sel:
            return None
        return self._dedup_tree_meta.get(sel[0])

    def _reveal_dedup_file(self, _event=None) -> None:
        pos = self._dedup_selected()
        if not pos:
            messagebox.showinfo('提示', '请先选中一个文件行')
            return
        fp = self.dedup_result[pos[0]].files[pos[1]]
        if Path(fp).exists():
            subprocess.run(['open', '-R', str(fp)], check=False)
        else:
            messagebox.showwarning('文件不存在', '该文件当前不在磁盘上。')

    def _keep_dedup_copy(self) -> None:
        """把选中的重复副本设为该组保留底稿 (原底稿降为重复副本).

        execute_dedup 以 files[0] 为保留底稿, 因此只需在组内重排顺序。
        """
        pos = self._dedup_selected()
        if not pos:
            messagebox.showinfo('提示', '请先选中一个文件行')
            return
        gi, fi = pos
        if fi == 0:
            messagebox.showinfo('提示', '该文件已经是本组的保留底稿。')
            return
        files = self.dedup_result[gi].files
        files.insert(0, files.pop(fi))
        self._after_dedup_scan(self.dedup_result)

    def _start_dedup_exec(self) -> None:
        if not self.dedup_result:
            return
        if self._wechat_running() and not self._warn_wechat_running():
            return
        if not messagebox.askyesno('确认去重',
                                       f'将处理 {len(self.dedup_result)} 组重复文件。\n'
                                       '硬链接去重零风险: 聊天窗口里的文件照常打开。\n\n确定执行吗?'):
            return

        def job():
            count, freed = execute_dedup(self.dedup_result, action='hardlink', dry_run=False,
                                         whitelist_mgr=self._whitelist())
            StateManager().record_dedup(count, freed, action='hardlink')
            return count, freed

        self._run_async(job, self._after_dedup_exec, '正在执行硬链接去重…')

    def _after_dedup_exec(self, result) -> None:
        count, freed = result
        messagebox.showinfo('去重完成', f'已处理 {count:,} 个重复副本, 释放 {format_bytes(freed)} 磁盘空间。\n'
                                           '所有聊天窗口里的文件仍可正常打开。')
        self._refresh_achievement_async()
        self._start_dedup_scan()

    # ---------- 白名单 ----------

    def _whitelist(self) -> WhiteListManager:
        if not hasattr(self, '_wl_mgr'):
            self._wl_mgr = WhiteListManager()
        return self._wl_mgr

    def _load_whitelist_async(self) -> None:
        self._run_async(lambda: list(self._whitelist().list_rules()), self._after_load_whitelist, '正在读取白名单…')

    def _after_load_whitelist(self, rules) -> None:
        for item in self.wl_tree.get_children():
            self.wl_tree.delete(item)
        for r in rules:
            self.wl_tree.insert('', END, values=(r.name, r.wxid or '-', ', '.join(r.keywords) or '-'))

    def _add_whitelist(self) -> None:
        name = self.wl_name.get().strip()
        if not name:
            messagebox.showwarning('还差一步', '请填写保护对象名称 (如: 老婆)')
            return
        wxid = self.wl_wxid.get().strip() or f'keyword:{name}'
        keywords = [k.strip() for k in self.wl_keywords.get().split(',') if k.strip()]
        if not keywords:
            keywords = [name]

        def job():
            self._whitelist().add(name=name, wxid=wxid, protect='absolute', keywords=keywords)
            return list(self._whitelist().list_rules())

        self._run_async(job, self._after_add_whitelist, '正在保存白名单…')

    def _after_add_whitelist(self, rules) -> None:
        self.wl_name.set('')
        self.wl_wxid.set('')
        self.wl_keywords.set('')
        self._after_load_whitelist(rules)
        messagebox.showinfo('已保存', '保护规则已生效: 清理与去重都会自动跳过这些文件。')

    def _remove_whitelist(self) -> None:
        sel = self.wl_tree.selection()
        if not sel:
            messagebox.showinfo('提示', '请先在列表中选中一条规则')
            return
        name = self.wl_tree.item(sel[0], 'values')[0]

        def job():
            self._whitelist().remove(name)
            return list(self._whitelist().list_rules())

        self._run_async(job, self._after_load_whitelist, '正在移除规则…')


def install_crash_logging() -> Path:
    """启动崩溃兜底 (对标 CleanMyWechat 的 startup_crash.log).

    未签名 .app 在用户特定环境下抛启动异常时会静默闪退, 用户只能反馈
    "打不开"。挂载 faulthandler + sys.excepthook 后, 任何未捕获异常与
    C 级崩溃都会落盘, 排障时让用户发这一个文件即可。
    """
    import faulthandler
    import traceback
    log_dir = Path.home() / 'Library' / 'Application Support' / 'CleanYourWechatTool'
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = Path.home()
    log_path = log_dir / 'startup_crash.log'
    try:
        faulthandler.enable(open(log_path, 'w'))
    except Exception:
        pass

    def _hook(exc_type, exc_value, exc_tb):
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f'\n===== {stamp} =====\n')
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        print(f'发生错误: {exc_type.__name__}: {exc_value}')
        print(f'崩溃日志已写入: {log_path}')
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    return log_path


def main() -> None:
    install_crash_logging()
    root = ttk.Window(themename='flatly', title=APP_TITLE)
    CleanYourWechatApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
