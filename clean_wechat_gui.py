#!/usr/bin/env python3
"""CleanYourWechatTool - 现代图形界面 (CustomTkinter).

设计哲学:
- 原生 macOS 质感: 采用 CustomTkinter，自适应系统深浅色外观 (System Appearance)
- 严谨克制: 无冗余表情符号，采用清晰的排版层级、圆角轻卡片与专业视觉配色
- 数据透视: 顶部横向分类存储占比条 (Storage Bar)，各类数据占用一目了然
- 安全与选择权: 全程做选择题，先预览清单再执行，支持单项排除与一键白名单保护
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

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

_BASE_DIR = Path(__file__).resolve().parent
for _p in (str(_BASE_DIR), str(_BASE_DIR / 'projects' / 'wechat_intelligence_hub'),
           str(_BASE_DIR / 'projects' / 'wechat-intelligence-hub')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine.scanner import discover_accounts, scan_account  # noqa: E402
from engine.cleaner import execute_slimming  # noqa: E402
from engine.common import format_bytes, parse_size_str  # noqa: E402
from engine.dedup import execute_dedup, find_duplicates  # noqa: E402
from engine.whitelist import WhiteListManager  # noqa: E402
from engine.state import StateManager  # noqa: E402

APP_TITLE = 'CleanYourWechatTool · 微信智能瘦身'
FONT_FAMILY = 'PingFang SC'

# 选择题预设选项: 显示文案 -> 实际值
TIME_CHOICES = [
    ('清理 7 天前的文件', 7),
    ('清理 30 天前的文件', 30),
    ('清理 60 天前的文件', 60),
    ('清理 90 天前的文件 (推荐)', 90),
    ('清理 180 天前的文件', 180),
    ('清理 1 年前的文件', 365),
    ('不限时间 (全部匹配)', 0),
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

TYPE_DEFS = [
    ('video', '聊天视频', True),
    ('file', '接收的文件', True),
    ('attach', '图片与附件', False),
    ('cache', '临时缓存', False),
    ('radium', '渲染缓存与转储', True),
    ('logs', '运行日志', True),
    ('xplugin', '小程序插件包', False),
]

# 存储分类配色规范 (Apple HIG 色调)
CATEGORY_THEMES = {
    'video': ('聊天视频', '#0A84FF'),
    'file': ('接收文件', '#FF9F0A'),
    'attach': ('图片附件', '#30D158'),
    'cache': ('临时缓存', '#BF5AF2'),
    'radium': ('渲染与转储', '#FF375F'),
    'logs': ('运行日志', '#64D2FF'),
    'xplugin': ('插件包体', '#FFD60A'),
    'db': ('核心保留区', '#8E8E93'),
}

_log = logging.getLogger('CleanYourWechatTool')
ExecResult = namedtuple('ExecResult', ['freed_count', 'freed_bytes', 'protected_count', 'protected_bytes'])


def execute_for_files(files, archive_to, whitelist_mgr, root_path=None):
    """按给定文件清单精确执行清理."""
    import shutil as _shutil
    from engine.cleaner import move_to_trash, SAFE_SKIP_EXTS, PROTECTED_DIR_NAMES

    freed_count = freed_bytes = protected_count = protected_bytes = 0
    for fp, size, mtime in files:
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
    """CleanYourWechatTool CustomTkinter 主界面."""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry('1080x780')
        self.root.minsize(980, 700)

        # 字体规范
        self.font_title = ctk.CTkFont(family=FONT_FAMILY, size=18, weight='bold')
        self.font_subtitle = ctk.CTkFont(family=FONT_FAMILY, size=12)
        self.font_head = ctk.CTkFont(family=FONT_FAMILY, size=13, weight='bold')
        self.font_body = ctk.CTkFont(family=FONT_FAMILY, size=12)
        self.font_bold = ctk.CTkFont(family=FONT_FAMILY, size=12, weight='bold')
        self.font_small = ctk.CTkFont(family=FONT_FAMILY, size=11)

        self.queue: 'queue.Queue[Tuple[str, Callable[..., None], Any]]' = queue.Queue()
        self.accounts = []
        self.current_categories: Dict[str, Any] = {}
        self.current_account: Optional[Any] = None
        self.preview_result = None
        self.tree_data: Dict[str, Dict[str, Any]] = {}
        self.dedup_result = []
        self._dedup_tree_meta: Dict[str, Tuple[int, int]] = {}
        self._cancel_event: Optional[threading.Event] = None
        self._sort_dir: Dict[str, bool] = {}

        self._build_ui()
        self._poll_queue()
        self.root.after(150, self._init_accounts)

    # ---------- 界面构建 ----------

    def _build_ui(self) -> None:
        # 1. 顶部 Header
        self.header = ctk.CTkFrame(self.root, corner_radius=0, fg_color='transparent')
        self.header.pack(fill='x', padx=20, pady=(16, 6))

        title_box = ctk.CTkFrame(self.header, fg_color='transparent')
        title_box.pack(side='left')
        ctk.CTkLabel(title_box, text='CleanYourWechatTool', font=self.font_title, anchor='w').pack(anchor='w')
        ctk.CTkLabel(title_box, text='macOS 微信数据空间透视与安全瘦身', font=self.font_subtitle,
                     text_color=('gray50', 'gray70'), anchor='w').pack(anchor='w')

        # 账号选择与探测按钮
        acc_ctrl = ctk.CTkFrame(self.header, fg_color='transparent')
        acc_ctrl.pack(side='right')

        self.account_var = ctk.StringVar(value='正在探测微信账号…')
        self.account_menu = ctk.CTkOptionMenu(acc_ctrl, variable=self.account_var, values=['正在探测微信账号…'],
                                              command=self._on_account_selected, width=280,
                                              font=self.font_body, dropdown_font=self.font_body)
        self.account_menu.pack(side='left', padx=(0, 8))

        self.btn_recheck = ctk.CTkButton(acc_ctrl, text='重新检测', command=self._init_accounts,
                                         width=84, height=32, font=self.font_body,
                                         fg_color=('gray85', 'gray25'), text_color=('gray10', 'gray90'),
                                         hover_color=('gray75', 'gray35'))
        self.btn_recheck.pack(side='left', padx=(0, 8))

        self.btn_custom_dir = ctk.CTkButton(acc_ctrl, text='自定义目录…', command=self._choose_custom_dir,
                                            width=96, height=32, font=self.font_body,
                                            fg_color=('gray85', 'gray25'), text_color=('gray10', 'gray90'),
                                            hover_color=('gray75', 'gray35'))
        self.btn_custom_dir.pack(side='left')

        # 2. 存储透视卡片 (Hero Storage Bar)
        self.storage_card = ctk.CTkFrame(self.root, corner_radius=10, fg_color=('gray95', 'gray16'))
        self.storage_card.pack(fill='x', padx=20, pady=(6, 10))

        bar_header = ctk.CTkFrame(self.storage_card, fg_color='transparent')
        bar_header.pack(fill='x', padx=16, pady=(10, 4))
        ctk.CTkLabel(bar_header, text='微信存储空间分布', font=self.font_bold).pack(side='left')
        self.storage_summary_label = ctk.CTkLabel(bar_header, text='', font=self.font_body,
                                                  text_color=('gray40', 'gray60'))
        self.storage_summary_label.pack(side='right')

        # Canvas 存储彩虹条
        self.canvas_frame = ctk.CTkFrame(self.storage_card, fg_color='transparent', height=14)
        self.canvas_frame.pack(fill='x', padx=16, pady=(2, 6))
        self.storage_canvas = tk.Canvas(self.canvas_frame, height=12, bd=0, highlightthickness=0)
        self.storage_canvas.pack(fill='both', expand=True)
        self.storage_canvas.bind('<Configure>', lambda _: self._draw_storage_bar())

        # 分类图例容器
        self.legend_frame = ctk.CTkFrame(self.storage_card, fg_color='transparent')
        self.legend_frame.pack(fill='x', padx=16, pady=(0, 10))

        # 3. 主选项卡 (CTkTabview)
        self.tabview = ctk.CTkTabview(self.root, corner_radius=10, fg_color=('gray95', 'gray16'))
        self.tabview.pack(fill='both', expand=True, padx=20, pady=(0, 10))

        self.tab_clean = self.tabview.add('智能瘦身')
        self.tab_dedup = self.tabview.add('无损去重 (APFS)')
        self.tab_whitelist = self.tabview.add('防删白名单')

        self._build_clean_tab()
        self._build_dedup_tab()
        self._build_whitelist_tab()

        # 4. 底部状态栏
        self.status_bar = ctk.CTkFrame(self.root, corner_radius=0, fg_color='transparent', height=36)
        self.status_bar.pack(fill='x', padx=24, pady=(0, 10))

        self.status_var = ctk.StringVar(value='就绪')
        self.status_label = ctk.CTkLabel(self.status_bar, textvariable=self.status_var,
                                         font=self.font_body, anchor='w')
        self.status_label.pack(side='left')

        self.btn_cancel = ctk.CTkButton(self.status_bar, text='取消任务', command=self._cancel_running,
                                        width=80, height=26, font=self.font_small,
                                        fg_color='transparent', border_width=1,
                                        border_color='#FF3B30', text_color='#FF3B30',
                                        hover_color=('gray90', 'gray25'))

        self.achievement_var = ctk.StringVar(value='')
        self.achievement_label = ctk.CTkLabel(self.status_bar, textvariable=self.achievement_var,
                                              font=self.font_bold, text_color='#30D158')
        self.achievement_label.pack(side='right')

        self.progress_bar = ctk.CTkProgressBar(self.status_bar, width=160, height=8)
        self.progress_bar.set(0)

        self._setup_treeview_styles()
        self._refresh_achievement_async()

    # ---- Tab 1: 智能瘦身 ----

    def _build_clean_tab(self) -> None:
        tab = self.tab_clean

        # 条件卡片
        cond_card = ctk.CTkFrame(tab, corner_radius=8, fg_color=('gray90', 'gray20'))
        cond_card.pack(fill='x', padx=10, pady=(8, 10))

        # 第一行: 时间范围 + 大小阈值
        r1 = ctk.CTkFrame(cond_card, fg_color='transparent')
        r1.pack(fill='x', padx=14, pady=(10, 6))

        ctk.CTkLabel(r1, text='时间范围:', font=self.font_bold).pack(side='left', padx=(0, 8))
        self.time_choice_var = ctk.StringVar(value=TIME_CHOICES[3][0])
        self.time_menu = ctk.CTkOptionMenu(r1, variable=self.time_choice_var,
                                           values=[t[0] for t in TIME_CHOICES],
                                           width=240, font=self.font_body)
        self.time_menu.pack(side='left', padx=(0, 24))

        ctk.CTkLabel(r1, text='文件大小:', font=self.font_bold).pack(side='left', padx=(0, 8))
        self.size_choice_var = ctk.StringVar(value=SIZE_CHOICES[4][0])
        self.size_menu = ctk.CTkOptionMenu(r1, variable=self.size_choice_var,
                                           values=[s[0] for s in SIZE_CHOICES],
                                           width=180, font=self.font_body)
        self.size_menu.pack(side='left')

        # 第二行: 文件类型复选框
        r2 = ctk.CTkFrame(cond_card, fg_color='transparent')
        r2.pack(fill='x', padx=14, pady=4)
        ctk.CTkLabel(r2, text='清理分类:', font=self.font_bold).pack(side='left', padx=(0, 8))

        self.type_check_vars: Dict[str, ctk.BooleanVar] = {}
        for key, label, default in TYPE_DEFS:
            var = ctk.BooleanVar(value=default)
            self.type_check_vars[key] = var
            chk = ctk.CTkCheckBox(r2, text=label, variable=var, font=self.font_body,
                                  checkbox_width=18, checkbox_height=18, corner_radius=4)
            chk.pack(side='left', padx=(0, 14))

        # 第三行: 处理方式
        r3 = ctk.CTkFrame(cond_card, fg_color='transparent')
        r3.pack(fill='x', padx=14, pady=(6, 10))
        ctk.CTkLabel(r3, text='处理方式:', font=self.font_bold).pack(side='left', padx=(0, 8))

        self.mode_var = ctk.StringVar(value='trash')
        self.rb_trash = ctk.CTkRadioButton(r3, text='移入系统废纸篓 (推荐，随时可放回原处)',
                                           variable=self.mode_var, value='trash',
                                           font=self.font_body, radiobutton_width=16,
                                           radiobutton_height=16)
        self.rb_trash.pack(side='left', padx=(0, 16))

        self.rb_archive = ctk.CTkRadioButton(r3, text='无损归档到指定目录',
                                             variable=self.mode_var, value='archive',
                                             font=self.font_body, radiobutton_width=16,
                                             radiobutton_height=16)
        self.rb_archive.pack(side='left', padx=(0, 10))

        self.archive_dir_var = ctk.StringVar(value='')
        self.btn_archive_dir = ctk.CTkButton(r3, text='选择归档目录…', command=self._choose_archive_dir,
                                             width=120, height=28, font=self.font_small,
                                             fg_color=('gray80', 'gray30'), text_color=('gray10', 'gray90'))
        self.btn_archive_dir.pack(side='left')

        # 操作栏
        act_row = ctk.CTkFrame(tab, fg_color='transparent')
        act_row.pack(fill='x', padx=10, pady=(0, 8))

        self.btn_preview = ctk.CTkButton(act_row, text='① 扫描预览文件清单', command=self._start_preview,
                                         width=180, height=34, font=self.font_bold)
        self.btn_preview.pack(side='left', padx=(0, 10))

        self.btn_execute = ctk.CTkButton(act_row, text='② 执行清理', command=self._start_clean,
                                         width=130, height=34, font=self.font_bold,
                                         fg_color='#FF3B30', hover_color='#D70015',
                                         state='disabled')
        self.btn_execute.pack(side='left', padx=(0, 12))

        ctk.CTkLabel(act_row, text='先预览清单，核对无误后再执行 —— 绝不盲删',
                     font=self.font_subtitle, text_color=('gray50', 'gray60')).pack(side='left')

        # 结果清单卡片
        list_card = ctk.CTkFrame(tab, corner_radius=8, fg_color=('gray90', 'gray20'))
        list_card.pack(fill='both', expand=True, padx=10, pady=(0, 6))

        toolbar = ctk.CTkFrame(list_card, fg_color='transparent')
        toolbar.pack(fill='x', padx=12, pady=(8, 6))

        self.btn_sel_all = ctk.CTkButton(toolbar, text='全部包含', command=lambda: self._set_all_included(True),
                                         width=76, height=26, font=self.font_small,
                                         fg_color=('gray80', 'gray30'), text_color=('gray10', 'gray90'))
        self.btn_sel_all.pack(side='left', padx=(0, 6))

        self.btn_sel_none = ctk.CTkButton(toolbar, text='全部排除', command=lambda: self._set_all_included(False),
                                          width=76, height=26, font=self.font_small,
                                          fg_color=('gray80', 'gray30'), text_color=('gray10', 'gray90'))
        self.btn_sel_none.pack(side='left', padx=(0, 12))

        self.clean_stats_var = ctk.StringVar(value='')
        ctk.CTkLabel(toolbar, textvariable=self.clean_stats_var, font=self.font_bold,
                     text_color=('#007AFF', '#0A84FF')).pack(side='left')

        ctk.CTkLabel(toolbar, text='单击「包含」列切换 · 双击打开 · 空格预览 · 右键访达定位/白名单',
                     font=self.font_small, text_color=('gray50', 'gray60')).pack(side='right')

        # 嵌入 Treeview
        tree_container = ctk.CTkFrame(list_card, fg_color='transparent')
        tree_container.pack(fill='both', expand=True, padx=10, pady=(0, 8))

        columns = ('inc', 'size', 'date', 'cat', 'path')
        self.clean_tree = ttk.Treeview(tree_container, columns=columns, show='headings', selectmode='extended')
        self.clean_tree.heading('inc', text='包含')
        self.clean_tree.column('inc', width=48, anchor='center')
        self.clean_tree.heading('size', text='大小 ▾', command=lambda: self._sort_clean_tree('size'))
        self.clean_tree.column('size', width=90, anchor='e')
        self.clean_tree.heading('date', text='修改日期', command=lambda: self._sort_clean_tree('date'))
        self.clean_tree.column('date', width=110, anchor='center')
        self.clean_tree.heading('cat', text='数据类别')
        self.clean_tree.column('cat', width=120, anchor='center')
        self.clean_tree.heading('path', text='相对路径', command=lambda: self._sort_clean_tree('path'))
        self.clean_tree.column('path', width=580, anchor='w')

        self.clean_tree.tag_configure('excluded', foreground='#8e8e93')
        self.clean_tree.tag_configure('protected', foreground='#0a84ff')
        self.clean_tree.pack(side='left', fill='both', expand=True)

        scroll = ttk.Scrollbar(tree_container, command=self.clean_tree.yview, orient='vertical')
        self.clean_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')

        # 事件绑定
        self._clean_menu = tk.Menu(self.clean_tree, tearoff=0)
        self._clean_menu.add_command(label='在访达中显示', command=self._reveal_selected)
        self._clean_menu.add_separator()
        self._clean_menu.add_command(label='包含选中项', command=self._include_selected)
        self._clean_menu.add_command(label='排除选中项', command=self._exclude_selected)
        self._clean_menu.add_separator()
        self._clean_menu.add_command(label='加入白名单保护', command=self._protect_selected)

        self.clean_tree.bind('<Button-1>', self._on_tree_click)
        self.clean_tree.bind('<Button-3>', self._on_tree_rightclick)
        self.clean_tree.bind('<Double-1>', self._open_selected_file)
        self.clean_tree.bind('<space>', self._quicklook_selected_file)

        self.clean_result_var = ctk.StringVar(value='')
        ctk.CTkLabel(tab, textvariable=self.clean_result_var, font=self.font_bold,
                     text_color=('#30D158', '#34C759')).pack(fill='x', padx=12, pady=(0, 4))

    # ---- Tab 2: 无损去重 ----

    def _build_dedup_tab(self) -> None:
        tab = self.tab_dedup

        cond_card = ctk.CTkFrame(tab, corner_radius=8, fg_color=('gray90', 'gray20'))
        cond_card.pack(fill='x', padx=10, pady=(8, 10))

        row = ctk.CTkFrame(cond_card, fg_color='transparent')
        row.pack(fill='x', padx=14, pady=10)

        ctk.CTkLabel(row, text='检查阈值:', font=self.font_bold).pack(side='left', padx=(0, 8))
        self.dedup_size_var = ctk.StringVar(value=SIZE_CHOICES[3][0])
        self.dedup_size_menu = ctk.CTkOptionMenu(row, variable=self.dedup_size_var,
                                                values=[s[0] for s in SIZE_CHOICES],
                                                width=160, font=self.font_body)
        self.dedup_size_menu.pack(side='left', padx=(0, 16))

        self.btn_dedup_scan = ctk.CTkButton(row, text='① 扫描重复文件', command=self._start_dedup_scan,
                                            width=160, height=32, font=self.font_bold)
        self.btn_dedup_scan.pack(side='left', padx=(0, 10))

        self.btn_dedup_exec = ctk.CTkButton(row, text='② 硬链接去重 (零风险)', command=self._start_dedup_exec,
                                            width=180, height=32, font=self.font_bold,
                                            fg_color='#34C759', hover_color='#248A3D',
                                            state='disabled')
        self.btn_dedup_exec.pack(side='left', padx=(0, 14))

        ctk.CTkLabel(row, text='基于 APFS 机制，多份副本合为一份空间，聊天记录照常打开',
                     font=self.font_subtitle, text_color=('gray50', 'gray60')).pack(side='left')

        # 重复文件树状卡片
        list_card = ctk.CTkFrame(tab, corner_radius=8, fg_color=('gray90', 'gray20'))
        list_card.pack(fill='both', expand=True, padx=10, pady=(0, 6))

        tb = ctk.CTkFrame(list_card, fg_color='transparent')
        tb.pack(fill='x', padx=12, pady=(8, 6))
        ctk.CTkLabel(tb, text='重复文件分组明细', font=self.font_bold).pack(side='left')
        ctk.CTkLabel(tb, text='右键可指定保留底稿 · 双击访达定位 · 空格预览',
                     font=self.font_small, text_color=('gray50', 'gray60')).pack(side='right')

        tree_box = ctk.CTkFrame(list_card, fg_color='transparent')
        tree_box.pack(fill='both', expand=True, padx=10, pady=(0, 8))

        dcols = ('group', 'role', 'size', 'path')
        self.dedup_tree = ttk.Treeview(tree_box, columns=dcols, show='headings', selectmode='browse')
        self.dedup_tree.heading('group', text='分组')
        self.dedup_tree.column('group', width=80, anchor='center')
        self.dedup_tree.heading('role', text='角色')
        self.dedup_tree.column('role', width=90, anchor='center')
        self.dedup_tree.heading('size', text='单份大小')
        self.dedup_tree.column('size', width=100, anchor='e')
        self.dedup_tree.heading('path', text='文件路径')
        self.dedup_tree.column('path', width=620, anchor='w')

        self.dedup_tree.tag_configure('keep', foreground='#1a9c50')
        self.dedup_tree.tag_configure('dup', foreground='#d70015')
        self.dedup_tree.tag_configure('grouphead', font=('PingFang SC', 11, 'bold'))
        self.dedup_tree.pack(side='left', fill='both', expand=True)

        dscroll = ttk.Scrollbar(tree_box, command=self.dedup_tree.yview, orient='vertical')
        self.dedup_tree.configure(yscrollcommand=dscroll.set)
        dscroll.pack(side='right', fill='y')

        self._dedup_menu = tk.Menu(self.dedup_tree, tearoff=0)
        self._dedup_menu.add_command(label='将此副本设为保留底稿', command=self._keep_dedup_copy)
        self._dedup_menu.add_command(label='在访达中显示', command=self._reveal_dedup_file)

        self.dedup_tree.bind('<Button-3>', self._on_dedup_rightclick)
        self.dedup_tree.bind('<Double-1>', self._reveal_dedup_file)
        self.dedup_tree.bind('<space>', self._quicklook_dedup_file)

    # ---- Tab 3: 防删白名单 ----

    def _build_whitelist_tab(self) -> None:
        tab = self.tab_whitelist

        form_card = ctk.CTkFrame(tab, corner_radius=8, fg_color=('gray90', 'gray20'))
        form_card.pack(fill='x', padx=10, pady=(8, 10))

        ctk.CTkLabel(form_card, text='添加保护规则 (命中的文件在清理与去重中会被绝对跳过):',
                     font=self.font_bold).pack(anchor='w', padx=14, pady=(10, 6))

        r1 = ctk.CTkFrame(form_card, fg_color='transparent')
        r1.pack(fill='x', padx=14, pady=4)
        ctk.CTkLabel(r1, text='保护对象名称 (必填):', font=self.font_body).pack(side='left')
        self.wl_name_entry = ctk.CTkEntry(r1, placeholder_text='如: 家人 / 重点客户 / 财务群', width=240)
        self.wl_name_entry.pack(side='left', padx=(8, 24))

        ctk.CTkLabel(r1, text='微信号 / 群ID (选填):', font=self.font_body).pack(side='left')
        self.wl_wxid_entry = ctk.CTkEntry(r1, placeholder_text='选填, 可留空', width=200)
        self.wl_wxid_entry.pack(side='left', padx=8)

        r2 = ctk.CTkFrame(form_card, fg_color='transparent')
        r2.pack(fill='x', padx=14, pady=4)
        ctk.CTkLabel(r2, text='保护关键词 (逗号分隔):', font=self.font_body).pack(side='left')
        self.wl_kw_entry = ctk.CTkEntry(r2, placeholder_text='文件名含任一关键词即受保护, 如: 合同, 对账单, 报表',
                                        width=500)
        self.wl_kw_entry.pack(side='left', padx=8)

        r3 = ctk.CTkFrame(form_card, fg_color='transparent')
        r3.pack(fill='x', padx=14, pady=(6, 12))
        ctk.CTkButton(r3, text='添加保护规则', command=self._add_whitelist, width=120, height=30,
                      font=self.font_bold, fg_color='#34C759', hover_color='#248A3D').pack(side='left', padx=(0, 10))
        ctk.CTkButton(r3, text='移除选中规则', command=self._remove_whitelist, width=120, height=30,
                      font=self.font_body, fg_color='transparent', border_width=1,
                      border_color='#FF3B30', text_color='#FF3B30', hover_color=('gray85', 'gray30')).pack(side='left', padx=(0, 10))
        ctk.CTkButton(r3, text='刷新列表', command=self._load_whitelist_async, width=90, height=30,
                      font=self.font_body, fg_color=('gray80', 'gray30'), text_color=('gray10', 'gray90')).pack(side='left')

        # 规则列表卡片
        list_card = ctk.CTkFrame(tab, corner_radius=8, fg_color=('gray90', 'gray20'))
        list_card.pack(fill='both', expand=True, padx=10, pady=(0, 6))

        wl_tb = ctk.CTkFrame(list_card, fg_color='transparent')
        wl_tb.pack(fill='x', padx=12, pady=(8, 6))
        ctk.CTkLabel(wl_tb, text='当前生效的保护规则', font=self.font_bold).pack(side='left')

        wl_box = ctk.CTkFrame(list_card, fg_color='transparent')
        wl_box.pack(fill='both', expand=True, padx=10, pady=(0, 8))

        wcols = ('name', 'wxid', 'keywords')
        self.wl_tree = ttk.Treeview(wl_box, columns=wcols, show='headings', selectmode='browse')
        self.wl_tree.heading('name', text='保护对象')
        self.wl_tree.column('name', width=180, anchor='w')
        self.wl_tree.heading('wxid', text='微信号 / 群ID')
        self.wl_tree.column('wxid', width=220, anchor='w')
        self.wl_tree.heading('keywords', text='保护关键词')
        self.wl_tree.column('keywords', width=450, anchor='w')
        self.wl_tree.pack(side='left', fill='both', expand=True)

        wscroll = ttk.Scrollbar(wl_box, command=self.wl_tree.yview, orient='vertical')
        self.wl_tree.configure(yscrollcommand=wscroll.set)
        wscroll.pack(side='right', fill='y')

    # ---------- 样式与数据透视条 ----------

    def _setup_treeview_styles(self) -> None:
        """根据当前系统外观配置 Treeview 配色."""
        is_dark = ctk.get_appearance_mode() == 'Dark'
        bg = '#1C1C1E' if is_dark else '#FFFFFF'
        fg = '#F5F5F7' if is_dark else '#1C1C1E'
        hbg = '#2C2C2E' if is_dark else '#F2F2F7'
        hfg = '#E5E5EA' if is_dark else '#3A3A3C'
        sel_bg = '#0A84FF' if is_dark else '#007AFF'

        style = ttk.Style()
        style.theme_use('default')
        style.configure('Treeview', background=bg, foreground=fg, fieldbackground=bg,
                        rowheight=28, font=(FONT_FAMILY, 11), borderwidth=0)
        style.configure('Treeview.Heading', background=hbg, foreground=hfg,
                        font=(FONT_FAMILY, 11, 'bold'), borderwidth=0)
        style.map('Treeview', background=[('selected', sel_bg)], foreground=[('selected', '#FFFFFF')])
        style.map('Treeview.Heading', background=[('active', '#3A3A3C' if is_dark else '#E5E5EA')])

    def _draw_storage_bar(self) -> None:
        """在 Canvas 上绘制分段彩色存储透视条."""
        self.storage_canvas.delete('all')
        w = self.storage_canvas.winfo_width()
        h = self.storage_canvas.winfo_height()
        if w <= 10 or h <= 4 or not self.current_categories:
            return

        total_bytes = sum(c.total_bytes for c in self.current_categories.values())
        if total_bytes <= 0:
            self.storage_canvas.create_rectangle(0, 0, w, h, fill='#8E8E93', width=0)
            return

        x = 0
        for key, (_, color) in CATEGORY_THEMES.items():
            cat = self.current_categories.get(key)
            if not cat or cat.total_bytes <= 0:
                continue
            seg_w = max(2, int((cat.total_bytes / total_bytes) * w))
            self.storage_canvas.create_rectangle(x, 0, x + seg_w, h, fill=color, width=0)
            x += seg_w
        if x < w:
            self.storage_canvas.create_rectangle(x, 0, w, h, fill='#8E8E93', width=0)

    def _refresh_legend(self) -> None:
        """更新存储卡片底部的分类图例 pills."""
        for child in self.legend_frame.winfo_children():
            child.destroy()

        if not self.current_categories:
            return

        for key, (label, color) in CATEGORY_THEMES.items():
            cat = self.current_categories.get(key)
            if not cat or cat.total_bytes <= 0:
                continue
            pill = ctk.CTkFrame(self.legend_frame, fg_color='transparent')
            pill.pack(side='left', padx=(0, 16))

            dot = tk.Canvas(pill, width=8, height=8, bd=0, highlightthickness=0)
            dot.pack(side='left', padx=(0, 4))
            dot.create_oval(0, 0, 8, 8, fill=color, width=0)

            txt = f'{label}: {format_bytes(cat.total_bytes)}'
            ctk.CTkLabel(pill, text=txt, font=self.font_small,
                         text_color=('gray30', 'gray80')).pack(side='left')

    # ---------- 异步任务管理 ----------

    def _run_async(self, fn: Callable[..., Any], on_done: Callable[[Any], None],
                   busy_text: str, cancellable: bool = False) -> None:
        self.status_var.set(busy_text)
        self._set_busy(True)
        self._cancel_event = threading.Event() if cancellable else None
        if cancellable:
            self.btn_cancel.pack(side='left', padx=(10, 0))
            self.progress_bar.pack(side='right', padx=(0, 16))
            self.progress_bar.start()
        else:
            self.btn_cancel.pack_forget()
            self.progress_bar.stop()
            self.progress_bar.pack_forget()

        def progress_cb(msg: str) -> None:
            self.queue.put(('progress', None, msg))

        import inspect
        takes_progress = len(inspect.signature(fn).parameters) >= 1

        def worker() -> None:
            try:
                res = fn(progress_cb) if takes_progress else fn()
                self.queue.put(('ok', on_done, res))
            except Exception as exc:
                self.queue.put(('err', on_done, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_running(self) -> None:
        if self._cancel_event:
            self._cancel_event.set()
            self.status_var.set('正在取消当前任务…')

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, on_done, payload = self.queue.get_nowait()
                if kind == 'progress':
                    self.status_var.set(str(payload))
                    continue
                self.btn_cancel.pack_forget()
                self.progress_bar.stop()
                self.progress_bar.pack_forget()
                self._set_busy(False)
                self.status_var.set('就绪')
                if kind == 'err':
                    cancelled = isinstance(payload, RuntimeError) and 'cancelled' in str(payload).lower()
                    if cancelled:
                        self.status_var.set('已取消')
                    else:
                        messagebox.showerror('操作失败', f'遇到错误: {payload}')
                    return
                if on_done:
                    on_done(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _set_busy(self, busy: bool) -> None:
        state = 'disabled' if busy else 'normal'
        self.btn_preview.configure(state=state)
        self.btn_dedup_scan.configure(state=state)
        self.btn_recheck.configure(state=state)
        self.btn_custom_dir.configure(state=state)
        if not busy:
            self.btn_execute.configure(state='normal' if self.preview_result else 'disabled')
            self.btn_dedup_exec.configure(state='normal' if self.dedup_result else 'disabled')
        else:
            self.btn_execute.configure(state='disabled')
            self.btn_dedup_exec.configure(state='disabled')

    # ---------- 账号与透视 ----------

    @staticmethod
    def _wechat_running() -> bool:
        try:
            res = subprocess.run(['pgrep', '-x', 'WeChat'], capture_output=True, timeout=3)
            return res.returncode == 0
        except Exception:
            return False

    def _warn_wechat_running(self) -> bool:
        return messagebox.askyesno(
            '提示: 微信正在运行',
            '微信当前处于打开状态。执行清理过程中微信可能会写入新文件或锁定缓存。\n\n'
            '建议先退出微信 (Cmd+Q) 后再清理。\n是否仍要继续执行?',
            icon='warning')

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
                choices.append(f'{acc.account_id} ({acc.version_type}) · {format_bytes(total)}')
            except Exception:
                choices.append(f'{acc.account_id} ({acc.version_type})')
        return choices

    def _after_accounts(self, accounts) -> None:
        if not accounts:
            self.account_menu.configure(values=['未发现微信数据目录'])
            self.account_var.set('未发现微信数据目录')
            self.storage_summary_label.configure(text='请确认已在此 Mac 登录过微信，或授权完全磁盘访问权限')
            offered = messagebox.askyesno(
                '未找到微信数据',
                '未能找到微信账号存储目录。常见原因:\n\n'
                '1. 本机尚未登录过桌面端微信\n'
                '2. macOS 隐私权限未授权 (最常见): 微信容器受 TCC 保护，未授权完全磁盘访问权限时无法读取。\n\n'
                '是否立即打开系统设置授权?',
            )
            if offered:
                subprocess.run(['open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'],
                               check=False)
            return

        choices = self._account_choices()
        self.account_menu.configure(values=choices)
        self.account_var.set(choices[0])
        self._apply_current_account()

    def _on_account_selected(self, choice: str) -> None:
        for acc in self.accounts:
            if acc.account_id in choice:
                self.current_account = acc
                self._run_async(lambda: scan_account(self.current_account), self._after_switch,
                                f'正在分析账号 {acc.account_id}…')
                break

    def _after_switch(self, categories) -> None:
        self.current_categories = categories
        self._apply_current_account()

    def _choose_custom_dir(self) -> None:
        chosen = filedialog.askdirectory(title='选择微信数据目录 (xwechat_files 或账号目录)')
        if chosen:
            self._run_async(lambda: self._load_accounts(custom_path=chosen), self._after_accounts,
                            '正在扫描自定义目录…')

    def _apply_current_account(self) -> None:
        acc = self.current_account
        if not acc:
            return
        total = sum(c.total_bytes for c in self.current_categories.values())
        self.storage_summary_label.configure(text=f'当前账号总占用: {format_bytes(total)}')
        self._draw_storage_bar()
        self._refresh_legend()

        # 清空旧预览与树
        self.preview_result = None
        self.dedup_result = []
        for item in self.clean_tree.get_children():
            self.clean_tree.delete(item)
        self.tree_data.clear()
        self.btn_execute.configure(state='disabled')
        self.btn_dedup_exec.configure(state='disabled')
        for item in self.dedup_tree.get_children():
            self.dedup_tree.delete(item)
        self._dedup_tree_meta.clear()
        self.clean_stats_var.set('')
        self.clean_result_var.set(f'已选中账号: {acc.account_id}，设置条件后点击「① 扫描预览文件清单」')

    def _choose_archive_dir(self) -> None:
        chosen = filedialog.askdirectory(title='选择归档目录 (建议外接硬盘或大容量存储)')
        if chosen:
            self.archive_dir_var.set(chosen)
            self.mode_var.set('archive')
            self.btn_archive_dir.configure(text=Path(chosen).name)

    # ---------- 智能瘦身流程 ----------

    def _collect_args(self) -> Tuple[int, int, List[str], Optional[Path]]:
        idx = next((i for i, t in enumerate(TIME_CHOICES) if t[0] == self.time_choice_var.get()), 3)
        days = TIME_CHOICES[idx][1]
        s_idx = next((i for i, s in enumerate(SIZE_CHOICES) if s[0] == self.size_choice_var.get()), 4)
        min_size = parse_size_str(SIZE_CHOICES[s_idx][1])

        types = [k for k, v in self.type_check_vars.items() if v.get()]
        if not types:
            raise ValueError('请至少勾选一种清理分类')

        archive_to = None
        if self.mode_var.get() == 'archive':
            raw = self.archive_dir_var.get().strip()
            if not raw:
                raise ValueError('归档模式请先点击「选择归档目录…」指定目标路径')
            archive_to = Path(raw)
        return days, min_size, types, archive_to

    def _start_preview(self) -> None:
        try:
            days, min_size, types, archive_to = self._collect_args()
        except ValueError as exc:
            messagebox.showwarning('提示', str(exc))
            return
        if not self.current_account:
            messagebox.showwarning('提示', '尚未发现可用微信账号')
            return

        acc, cats = self.current_account, self.current_categories

        def job(progress_cb):
            return execute_slimming(acc, cats, days, min_size, types, dry_run=True,
                                    archive_to=archive_to, whitelist_mgr=self._whitelist(),
                                    progress_cb=progress_cb, cancel_event=self._cancel_event)

        self._run_async(job, lambda res: self._after_preview(res, acc, archive_to),
                        '正在扫描匹配文件…', cancellable=True)

    def _category_label_of_path(self, fp: Path) -> str:
        s = str(fp)
        if '/msg/video' in s:
            return '聊天视频'
        if '/msg/file' in s:
            return '接收文件'
        if '/msg/attach' in s:
            return '图片附件'
        if '/cache' in s:
            return '临时缓存'
        if '/radium' in s:
            return '渲染与转储'
        if '/log' in s:
            return '运行日志'
        if '/xplugin' in s:
            return '插件包体'
        return '其它数据'

    def _after_preview(self, res, acc, archive_to) -> None:
        self.preview_result = res if res.freed_count > 0 else None
        for item in self.clean_tree.get_children():
            self.clean_tree.delete(item)
        self.tree_data.clear()

        if res.freed_count == 0:
            self.clean_result_var.set('没有符合条件的文件，无需清理。')
            self.btn_execute.configure(state='disabled')
            self._refresh_clean_stats()
            return

        files = sorted(res.affected_files, key=lambda t: t[1], reverse=True)
        for fp, size, mtime in files:
            try:
                rel = fp.relative_to(acc.root_path)
            except ValueError:
                try:
                    rel = fp.relative_to(acc.root_path.parent.parent)
                except Exception:
                    rel = fp
            cat_label = self._category_label_of_path(fp)
            iid = self.clean_tree.insert(
                '', 'end',
                values=('☑', format_bytes(size),
                        datetime.fromtimestamp(mtime).strftime('%Y-%m-%d'),
                        cat_label, str(rel)),
                tags=())
            self.tree_data[iid] = {'path': fp, 'size': size, 'mtime': mtime, 'included': True}

        summary = f'共匹配 {res.freed_count:,} 个文件 / {format_bytes(res.freed_bytes)} —— 确认清单后点击「② 执行清理」'
        if res.protected_count:
            summary += f' (白名单已自动保护 {res.protected_count:,} 个文件)'
        self.clean_result_var.set(summary)
        self.btn_execute.configure(state='normal')
        self._refresh_clean_stats()

    def _set_included(self, iid: str, included: bool) -> None:
        data = self.tree_data.get(iid)
        if not data:
            return
        data['included'] = included
        self.clean_tree.set(iid, 'inc', '☑' if included else '☐')
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

    def _selected_tree_data(self) -> Optional[Dict[str, Any]]:
        sel = self.clean_tree.selection()
        if not sel:
            return None
        return self.tree_data.get(sel[0])

    def _open_selected_file(self, _event=None) -> None:
        data = self._selected_tree_data()
        if not data:
            return
        fp = Path(data['path'])
        if fp.exists():
            subprocess.Popen(['open', str(fp)])
        else:
            messagebox.showwarning('提示', '文件在磁盘上不存在或已被移动')

    def _quicklook_selected_file(self, _event=None) -> None:
        data = self._selected_tree_data()
        if not data:
            return
        fp = Path(data['path'])
        if fp.exists():
            subprocess.Popen(['qlmanage', '-p', str(fp)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _reveal_selected(self) -> None:
        sel = self.clean_tree.selection()
        if not sel:
            return
        data = self.tree_data.get(sel[0])
        if data and Path(data['path']).exists():
            subprocess.run(['open', '-R', str(data['path'])], check=False)

    def _protect_selected(self) -> None:
        sel = self.clean_tree.selection()
        if not sel:
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
                tags = list(self.clean_tree.item(iid, 'tags'))
                if 'protected' not in tags:
                    tags.append('protected')
                self.clean_tree.item(iid, tags=tuple(tags))
                self._set_included(iid, False)
            except Exception as exc:
                _log.warning('加入白名单失败 %s: %s', fp, exc)
        if added:
            self._refresh_clean_stats()
            messagebox.showinfo('已加入白名单', f'已保护 {added} 个文件，本次及后续清理均会自动跳过。')

    def _sort_clean_tree(self, key: str) -> None:
        current_dir = self._sort_dir.get(key, True)
        self._sort_dir = {key: not current_dir}

        def sort_val(iid):
            d = self.tree_data.get(iid, {})
            if key == 'size':
                return d.get('size', 0)
            if key == 'date':
                return d.get('mtime', 0)
            return str(d.get('path', ''))

        iids = list(self.clean_tree.get_children())
        iids.sort(key=sort_val, reverse=current_dir)
        for iid in iids:
            self.clean_tree.move(iid, '', 'end')

        arrow = '▾' if current_dir else '▴'
        label_map = {'size': '大小', 'date': '修改日期', 'path': '相对路径'}
        for col in ('size', 'date', 'path'):
            self.clean_tree.heading(col, text=f'{label_map[col]} {arrow if col == key else ""}',
                                    command=lambda c=col: self._sort_clean_tree(c))

    def _refresh_clean_stats(self) -> None:
        inc = [d for d in self.tree_data.values() if d['included']]
        n = len(inc)
        m = len(self.tree_data) - n
        bytes_inc = sum(d['size'] for d in inc)
        self.clean_stats_var.set(f'已选 {n} 项 / 排除 {m} 项 · 将释放 {format_bytes(bytes_inc)}')

    def _start_clean(self) -> None:
        if not self.preview_result:
            return
        included = [(d['path'], d['size'], d['mtime']) for d in self.tree_data.values() if d['included']]
        if not included:
            messagebox.showwarning('提示', '清单中所有文件均被排除，无可清理文件。')
            return

        if self._wechat_running() and not self._warn_wechat_running():
            return

        total_bytes = sum(s for _, s, _ in included)
        if not messagebox.askyesno(
            '确认执行清理',
            f'确定对选中的 {len(included):,} 个文件执行清理吗？\n'
            f'预估将释放: {format_bytes(total_bytes)}\n\n'
            f'处理方式: {"移入系统废纸篓 (随时可还原)" if self.mode_var.get() == "trash" else "归档到指定目录"}'):
            return

        try:
            _, _, _, archive_to = self._collect_args()
        except ValueError as exc:
            messagebox.showwarning('提示', str(exc))
            return
        acc = self.current_account

        def job():
            res = execute_for_files(included, archive_to, self._whitelist(), root_path=acc.root_path)
            StateManager().record_clean(res.freed_count, res.freed_bytes,
                                        res.protected_count, res.protected_bytes,
                                        is_archive=bool(archive_to))
            return res

        self._run_async(job, lambda r: self._after_clean(r, archive_to), '正在执行清理…')

    def _after_clean(self, res, archive_to) -> None:
        self.preview_result = None
        self.btn_execute.configure(state='disabled')
        for item in self.clean_tree.get_children():
            self.clean_tree.delete(item)
        self.tree_data.clear()
        self._refresh_clean_stats()

        msg = f'清理完成! 释放 {format_bytes(res.freed_bytes)} (处理 {res.freed_count:,} 个文件)'
        if res.protected_count:
            msg += f'，白名单保护 {res.protected_count:,} 个文件未触碰'
        self.clean_result_var.set(msg)

        target_dir = Path.home() / '.Trash' if archive_to is None else Path(archive_to)
        open_dir = messagebox.askyesno(
            '清理完成',
            msg + '\n\n'
            + ('清空系统废纸篓后磁盘空间将真正释放。\n' if archive_to is None else f'文件已完整保存至: {archive_to}\n')
            + f'\n是否立即打开 {"系统废纸篓" if archive_to is None else "归档目录"}?',
        )
        if open_dir:
            subprocess.run(['open', str(target_dir)], check=False)
        self._refresh_achievement_async()
        self._init_accounts()

    # ---------- 无损去重流程 ----------

    def _start_dedup_scan(self) -> None:
        if not self.current_account:
            return
        s_idx = next((i for i, s in enumerate(SIZE_CHOICES) if s[0] == self.dedup_size_var.get()), 3)
        min_size = parse_size_str(SIZE_CHOICES[s_idx][1])
        acc, cats = self.current_account, self.current_categories

        def job(progress_cb):
            return find_duplicates(cats, ['video', 'file', 'attach'], min_size_bytes=min_size,
                                   whitelist_mgr=self._whitelist(),
                                   progress_cb=progress_cb, cancel_event=self._cancel_event)

        self._run_async(job, self._after_dedup_scan, '正在计算重复文件指纹…', cancellable=True)

    def _after_dedup_scan(self, groups) -> None:
        self.dedup_result = [g for g in groups if g.wasted_count > 0]
        self._dedup_tree_meta.clear()
        for item in self.dedup_tree.get_children():
            self.dedup_tree.delete(item)
        self.btn_dedup_exec.configure(state='disabled')

        if not self.dedup_result:
            self.dedup_tree.insert('', 'end', values=('', '', '', '未发现重复文件，当前没有冗余空间可释放。'))
            return

        total_saving = sum(g.saving_bytes for g in self.dedup_result)
        for idx, g in enumerate(self.dedup_result):
            head = self.dedup_tree.insert('', 'end', tags=('grouphead',),
                                          values=(f'第 {idx + 1} 组', f'{len(g.files)} 份',
                                                  format_bytes(g.file_size),
                                                  f'可释放 {format_bytes(g.saving_bytes)}'))
            for fi, fp in enumerate(g.files):
                role = '保留底稿' if fi == 0 else '重复副本'
                tag = 'keep' if fi == 0 else 'dup'
                iid = self.dedup_tree.insert(head, 'end', values=('', role, format_bytes(g.file_size), str(fp)),
                                             tags=(tag,))
                self._dedup_tree_meta[iid] = (idx, fi)
            self.dedup_tree.item(head, open=True)

        self.status_var.set(f'发现 {len(self.dedup_result)} 组重复文件，预计可释放 {format_bytes(total_saving)}')
        self.btn_dedup_exec.configure(state='normal')

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
            return
        fp = self.dedup_result[pos[0]].files[pos[1]]
        if Path(fp).exists():
            subprocess.run(['open', '-R', str(fp)], check=False)

    def _quicklook_dedup_file(self, _event=None) -> None:
        pos = self._dedup_selected()
        if not pos:
            return
        fp = self.dedup_result[pos[0]].files[pos[1]]
        if Path(fp).exists():
            subprocess.Popen(['qlmanage', '-p', str(fp)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _keep_dedup_copy(self) -> None:
        pos = self._dedup_selected()
        if not pos:
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
        total_saving = sum(g.saving_bytes for g in self.dedup_result)
        if not messagebox.askyesno(
            '确认硬链接去重',
            f'即将对 {len(self.dedup_result)} 组重复文件执行 APFS 硬链接去重。\n'
            f'预计释放空间: {format_bytes(total_saving)}\n\n'
            '去重机制安全无损: 原有聊天窗口中的文件均可照常打开。确定执行吗?'):
            return

        def job():
            count, freed = execute_dedup(self.dedup_result, action='hardlink', dry_run=False,
                                         whitelist_mgr=self._whitelist())
            StateManager().record_dedup(count, freed, action='hardlink')
            return count, freed

        self._run_async(job, self._after_dedup_exec, '正在执行硬链接去重…')

    def _after_dedup_exec(self, result) -> None:
        count, freed = result
        messagebox.showinfo('去重完成', f'已处理 {count:,} 个重复副本，释放 {format_bytes(freed)} 空间。')
        self._refresh_achievement_async()
        self._start_dedup_scan()

    # ---------- 白名单管理 ----------

    def _whitelist(self) -> WhiteListManager:
        if not hasattr(self, '_wl_mgr'):
            self._wl_mgr = WhiteListManager()
        return self._wl_mgr

    def _load_whitelist_async(self) -> None:
        self._run_async(lambda: list(self._whitelist().list_rules()), self._after_load_whitelist,
                        '正在读取白名单…')

    def _after_load_whitelist(self, rules) -> None:
        for item in self.wl_tree.get_children():
            self.wl_tree.delete(item)
        for r in rules:
            self.wl_tree.insert('', 'end', values=(r.name, r.wxid or '-', ', '.join(r.keywords) or '-'))

    def _add_whitelist(self) -> None:
        name = self.wl_name_entry.get().strip()
        if not name:
            messagebox.showwarning('提示', '请填写保护对象名称')
            return
        wxid = self.wl_wxid_entry.get().strip() or f'keyword:{name}'
        raw_kw = self.wl_kw_entry.get().strip()
        keywords = [k.strip() for k in raw_kw.split(',') if k.strip()]
        if not keywords:
            keywords = [name]

        def job():
            self._whitelist().add(name=name, wxid=wxid, protect='absolute', keywords=keywords)
            return list(self._whitelist().list_rules())

        self._run_async(job, self._after_add_whitelist, '正在保存白名单…')

    def _after_add_whitelist(self, rules) -> None:
        self.wl_name_entry.delete(0, 'end')
        self.wl_wxid_entry.delete(0, 'end')
        self.wl_kw_entry.delete(0, 'end')
        self._after_load_whitelist(rules)
        messagebox.showinfo('已保存', '保护规则已生效，清理与去重均会自动跳过。')

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

    # ---------- 成就统计 ----------

    def _refresh_achievement_async(self) -> None:
        def job():
            st = StateManager()
            return st.total_freed_bytes, st.total_cleans, st.total_dedups

        self._run_async(job, self._after_refresh_achievement, '正在读取统计…')

    def _after_refresh_achievement(self, data) -> None:
        freed, cleans, dedups = data
        if freed > 0:
            self.achievement_var.set(
                f'累计已为本机释放 {format_bytes(freed)} (清理 {cleans} 次 / 去重 {dedups} 次)')


def install_crash_logging() -> Path:
    """挂载启动崩溃与未捕获异常日志."""
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
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    return log_path


def main() -> None:
    install_crash_logging()
    ctk.set_appearance_mode('System')
    ctk.set_default_color_theme('blue')
    root = ctk.CTk()
    CleanYourWechatApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
