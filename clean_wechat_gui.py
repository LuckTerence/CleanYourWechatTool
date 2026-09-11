#!/usr/bin/env python3
"""CleanYourWechatTool - 苹果级极简产品架构 (CustomTkinter).

产品设计哲学:
- 极致简约: 告别机房运维式控制台，收敛为「3 秒定心透视 + 3 张智能卡片 + 1 键安全释放」
- 拒绝机话: 用人话（系统垃圾、多群重复、历史大文件）替换专业路径与技术名词
- 渐进式披露: 90% 的普通用户一键搞定；10% 的谨慎用户点击「核对清单 >」抽屉查看明细
- 心理安全感: 默认移入系统废纸篓，随时可右键放回原处，彻底消除误删恐惧
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
from collections import namedtuple
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from tkinter import messagebox, ttk
try:
    from PIL import Image, ImageTk  # 仅用于窗口图标显示, 缺失时优雅降级
except ImportError:  # 打包环境缺 Pillow 时不得阻断启动 (PIL 非核心能力)
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]
import customtkinter as ctk

_BASE_DIR = Path(__file__).resolve().parent
for _p in (str(_BASE_DIR), str(_BASE_DIR / 'projects' / 'wechat_intelligence_hub'),
           str(_BASE_DIR / 'projects' / 'wechat-intelligence-hub')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine.scanner import discover_accounts, scan_account, ScanCategory  # noqa: E402
from engine.cleaner import (  # noqa: E402
    execute_slimming, move_to_trash, is_hard_protected,
    classify_file_type,
)
from engine.common import format_bytes, parse_size_str  # noqa: E402
from engine.dedup import execute_dedup, find_duplicates, DuplicateGroup  # noqa: E402
from engine.whitelist import WhiteListManager  # noqa: E402
from engine.state import StateManager  # noqa: E402

APP_TITLE = 'CleanYourWechatTool · 微信智能空间管家'
if sys.platform == 'win32':
    FONT_FAMILY = 'Microsoft YaHei'
elif sys.platform == 'darwin':
    FONT_FAMILY = 'PingFang SC'
else:
    FONT_FAMILY = 'Segoe UI'

# 高级筛选维度定义 (默认标明推荐项，支持平滑分段单选)
LARGE_DAYS_CHOICES = [
    ('30天前', 30),
    ('60天前', 60),
    ('90天前 (推荐)', 90),
    ('180天前', 180),
    ('1年前', 365),
    ('不限', 0),
]
LARGE_SIZE_CHOICES = [
    ('> 1MB', '1MB'),
    ('> 10MB (推荐)', '10MB'),
    ('> 50MB', '50MB'),
    ('> 100MB', '100MB'),
    ('> 500MB', '500MB'),
    ('> 1GB', '1GB'),
]
DEDUP_SIZE_CHOICES = [
    ('> 500KB', '500KB'),
    ('> 1MB (推荐)', '1MB'),
    ('> 5MB', '5MB'),
    ('> 10MB', '10MB'),
]


def get_asset_path(filename: str) -> Optional[Path]:
    """获取资源文件绝对路径 (兼顾源码运行与 PyInstaller 冻结包)."""
    candidates = []
    if hasattr(sys, '_MEIPASS'):
        candidates.append(Path(sys._MEIPASS) / 'assets' / filename)
        candidates.append(Path(sys._MEIPASS) / filename)
    candidates.append(_BASE_DIR / 'assets' / filename)
    candidates.append(_BASE_DIR / filename)
    for p in candidates:
        if p.exists():
            return p
    return None


_log = logging.getLogger('CleanYourWechatTool')
ExecResult = namedtuple('ExecResult', ['freed_count', 'freed_bytes', 'protected_count', 'protected_bytes'])


def execute_files_to_trash(
    files: List[Tuple[Path, int, float]],
    whitelist_mgr: Optional[WhiteListManager],
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[Any] = None,
    account_root: Optional[Path] = None,
) -> ExecResult:
    """将文件清单安全移入系统废纸篓 (支持实时进度上报与快速取消)."""
    freed_count = freed_bytes = protected_count = protected_bytes = 0
    total = len(files)
    for idx, (fp, size, mtime) in enumerate(files, 1):
        if cancel_event is not None and cancel_event.is_set():
            break
        if progress_cb is not None and (idx % 50 == 0 or idx == total):
            progress_cb(f'正在移入废纸篓 ({idx:,} / {total:,} 个文件)…')
        # 死线判定统一走 is_hard_protected (单一事实来源, 与引擎执行/界面预估同口径)
        if is_hard_protected(Path(fp), account_root):
            continue
        if whitelist_mgr:
            is_prot, _ = whitelist_mgr.is_protected(fp, mtime)
            if is_prot:
                protected_count += 1
                protected_bytes += size
                continue
        try:
            if move_to_trash(fp):
                freed_count += 1
                freed_bytes += size
        except (OSError, PermissionError):
            continue
    return ExecResult(freed_count, freed_bytes, protected_count, protected_bytes)


class CleanYourWechatApp:
    """统一视窗极简架构."""

    def __init__(self, root: ctk.CTk, auto_init: bool = True) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry('960x720')
        self.root.minsize(860, 640)

        # 字体规范
        self.font_hero_val = ctk.CTkFont(family=FONT_FAMILY, size=30, weight='bold')
        self.font_title = ctk.CTkFont(family=FONT_FAMILY, size=16, weight='bold')
        self.font_subtitle = ctk.CTkFont(family=FONT_FAMILY, size=12)
        self.font_card_title = ctk.CTkFont(family=FONT_FAMILY, size=14, weight='bold')
        self.font_card_desc = ctk.CTkFont(family=FONT_FAMILY, size=12)
        self.font_card_size = ctk.CTkFont(family=FONT_FAMILY, size=15, weight='bold')
        self.font_body = ctk.CTkFont(family=FONT_FAMILY, size=12)
        self.font_bold = ctk.CTkFont(family=FONT_FAMILY, size=12, weight='bold')
        self.font_small = ctk.CTkFont(family=FONT_FAMILY, size=11)

        self.queue: 'queue.Queue[Tuple[str, Optional[Callable[..., None]], Any]]' = queue.Queue()
        self.accounts: List[Any] = []
        self.current_categories: Dict[str, ScanCategory] = {}
        self.current_account: Optional[Any] = None

        # 三大智能卡片的数据模型
        # 卡片 1: 基础系统垃圾
        self.junk_files: List[Tuple[Path, int, float]] = []
        self.junk_bytes = 0
        # 卡片 2: 多群去重
        self.dedup_groups: List[DuplicateGroup] = []
        self.dedup_bytes = 0
        # 卡片 3: 历史大文件
        self.large_files: List[Tuple[Path, int, float]] = []
        self.large_files_meta: Dict[str, Dict[str, Any]] = {}
        self.large_bytes = 0

        self._cancel_event: Optional[threading.Event] = None
        self._is_busy = False
        self._closing = False
        self._filters_dirty = False
        self._filters_job = None

        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._setup_app_icon()
        self._build_ui()
        self._poll_queue()
        if auto_init:
            self.root.after(100, self._init_accounts)

    def _setup_app_icon(self) -> None:
        """设置窗口图标与全局 Dock 图标 (兼顾 icns 与 iconphoto)."""
        icns_path = get_asset_path('app.icns')
        if icns_path and icns_path.exists():
            try:
                self.root.iconbitmap(str(icns_path))
            except Exception:
                pass

        png_path = get_asset_path('app_1024.png')
        if png_path and png_path.exists() and Image is not None:
            try:
                img = Image.open(png_path)
                self._app_icon_tk = ImageTk.PhotoImage(img.resize((64, 64), Image.Resampling.LANCZOS))
                self.root.iconphoto(True, self._app_icon_tk)
            except Exception as e:
                _log.warning('无法设置窗口图标: %s', e)

    def _install_wheel_scroll(self) -> None:
        """接管滚轮 / 触控板双指滚动。

        背景：CTk 6.0.0 的 CTkScrollableFrame 用
        `yview('scroll', -event.delta, 'units')` —— 滚动量直接取自 delta 数值。
        在 Tk 9.0 + 真实触控板事件下该数值算出的步长为 0，表现为
        **完全滚不动，只能拖动滚动条**（用户实测反馈）。

        修复思路：只取 delta 的**方向**，用固定步长滚动，不依赖不同平台/Tk 版本
        对 delta 的数值语义；同时复用 CTk 的控件白名单，避免拦截文本框、
        滑块、滚动条等自身需要处理滚轮的控件。
        """
        sf = self.scroll_container
        canvas = sf._parent_canvas
        step_units = 4  # 约 24px/格（canvas 1 unit ≈ 6px），介于触控板与滚轮手感之间

        def _on_wheel(event) -> None:
            try:
                if not sf._check_if_valid_scroll(event.widget):
                    return  # 文本框/滑块/滚动条自行处理
            except Exception:
                pass
            delta = getattr(event, 'delta', 0)
            num = getattr(event, 'num', 0)
            if delta:
                direction = -1 if delta > 0 else 1
            elif num in (4, 5):  # X11 风格滚轮
                direction = -1 if num == 4 else 1
            else:
                return
            canvas.yview_scroll(direction * step_units, 'units')

        try:
            # 解绑 CTk 自带的实现, 避免与其叠加导致双倍滚动
            self.root.unbind_all('<MouseWheel>')
        except Exception:
            pass
        for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            try:
                self.root.bind_all(seq, _on_wheel, add='+')
            except Exception:
                pass

    # ---------- 界面构建 ----------

    def _build_ui(self) -> None:
        # 1. 顶部 Header
        self.header = ctk.CTkFrame(self.root, corner_radius=0, fg_color='transparent')
        self.header.pack(fill='x', padx=28, pady=(18, 10))

        # 左侧应用品牌标识 (App Icon + Title)
        brand_box = ctk.CTkFrame(self.header, fg_color='transparent')
        brand_box.pack(side='left')

        logo_path = get_asset_path('app_1024.png')
        if logo_path and logo_path.exists() and Image is not None:
            try:
                logo_img = Image.open(logo_path)
                self._logo_image = ctk.CTkImage(
                    light_image=logo_img,
                    dark_image=logo_img,
                    size=(36, 36),
                )
                logo_label = ctk.CTkLabel(brand_box, image=self._logo_image, text='')
                logo_label.pack(side='left', padx=(0, 12))
            except Exception as e:
                _log.warning('无法加载 Logo 图标: %s', e)

        title_box = ctk.CTkFrame(brand_box, fg_color='transparent')
        title_box.pack(side='left')
        ctk.CTkLabel(title_box, text='CleanYourWechatTool', font=self.font_title, anchor='w').pack(anchor='w')
        ctk.CTkLabel(title_box, text='macOS 微信智能空间管家', font=self.font_subtitle,
                     text_color=('gray50', 'gray65'), anchor='w').pack(anchor='w')

        # 右侧操作区: 账号选择 + 重新检测 + 防删保护入口
        top_right = ctk.CTkFrame(self.header, fg_color='transparent')
        top_right.pack(side='right')

        self.account_var = ctk.StringVar(value='正在探测微信账号…')
        self.account_menu = ctk.CTkOptionMenu(
            top_right, variable=self.account_var, values=['正在探测微信账号…'],
            command=self._on_account_selected, width=220, height=30,
            font=self.font_body, dropdown_font=self.font_body,
            corner_radius=6,
            fg_color=('gray85', 'gray25'), text_color=('gray10', 'gray90'),
            button_color=('gray75', 'gray35'))
        self.account_menu.pack(side='left', padx=(0, 8))

        self.btn_recheck = ctk.CTkButton(
            top_right, text='重新检测', command=self._init_accounts,
            width=78, height=30, font=self.font_body,
            fg_color=('gray88', 'gray25'), hover_color=('gray80', 'gray32'),
            text_color=('gray15', 'gray88'), corner_radius=6)
        self.btn_recheck.pack(side='left', padx=(0, 8))

        self.btn_whitelist = ctk.CTkButton(
            top_right, text='防删保护', command=self._open_whitelist_modal,
            width=84, height=30, font=self.font_body,
            fg_color=('gray88', 'gray25'), hover_color=('gray80', 'gray32'),
            text_color=('gray15', 'gray88'), corner_radius=6)
        self.btn_whitelist.pack(side='left')

        # 2. 底部状态与成就栏 (优先置底 pack, 保证永远不被挤出可视区)
        self.footer = ctk.CTkFrame(self.root, corner_radius=0, fg_color='transparent')
        self.footer.pack(side='bottom', fill='x', padx=28, pady=(8, 12))

        self.status_var = ctk.StringVar(value='正在诊断系统…')
        self.status_label = ctk.CTkLabel(self.footer, textvariable=self.status_var,
                                         font=self.font_small, text_color=('gray50', 'gray65'))
        self.status_label.pack(side='left')

        self.btn_cancel = ctk.CTkButton(
            self.footer, text='取消', command=self._cancel_running,
            width=60, height=22, font=self.font_small,
            fg_color='transparent', border_width=1,
            border_color='#FF3B30', text_color='#FF3B30')

        self.progress_bar = ctk.CTkProgressBar(self.footer, width=140, height=6)
        self.progress_bar.set(0)

        self.achievement_var = ctk.StringVar(value='')
        self.achievement_label = ctk.CTkLabel(self.footer, textvariable=self.achievement_var,
                                              font=self.font_bold, text_color=('#30D158', '#34C759'))
        self.achievement_label.pack(side='right')

        # 3. 中间可滚动主体容器 (解决展开选项或小屏幕窗口高度不足导致底部截断的 Bug)
        self.scroll_container = ctk.CTkScrollableFrame(
            self.root, corner_radius=0, fg_color='transparent')
        self.scroll_container.pack(side='top', fill='both', expand=True, padx=20, pady=(0, 2))
        self._install_wheel_scroll()

        # 3.1 Hero 智能诊断看板
        self.hero_card = ctk.CTkFrame(self.scroll_container, corner_radius=12, fg_color=('gray92', 'gray18'))
        self.hero_card.pack(fill='x', padx=8, pady=(4, 14))

        hero_inner = ctk.CTkFrame(self.hero_card, fg_color='transparent')
        hero_inner.pack(fill='x', padx=24, pady=20)

        # 左侧核心指标
        metrics_box = ctk.CTkFrame(hero_inner, fg_color='transparent')
        metrics_box.pack(side='left')

        ctk.CTkLabel(metrics_box, text='微信当前占用', font=self.font_subtitle,
                     text_color=('gray50', 'gray60'), anchor='w').pack(anchor='w')
        self.total_size_label = ctk.CTkLabel(metrics_box, text='-- GB', font=self.font_hero_val,
                                             anchor='w')
        self.total_size_label.pack(anchor='w', pady=(2, 4))

        self.reclaimable_label = ctk.CTkLabel(
            metrics_box, text='预计可安全释放: 分析中…', font=self.font_bold,
            text_color=('#007AFF', '#0A84FF'), anchor='w')
        self.reclaimable_label.pack(anchor='w')

        # 右侧核心行动按钮
        action_box = ctk.CTkFrame(hero_inner, fg_color='transparent')
        action_box.pack(side='right')

        self.btn_one_key = ctk.CTkButton(
            action_box, text='一键安全瘦身', command=self._execute_one_key_clean,
            width=240, height=44, font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight='bold'),
            fg_color='#007AFF', hover_color='#0062CC', corner_radius=8, state='disabled')
        self.btn_one_key.pack(anchor='e')

        ctk.CTkLabel(action_box, text='* 文件将安全移入系统废纸篓，可随时放回原处',
                     font=self.font_small, text_color=('gray50', 'gray60'), anchor='e').pack(anchor='e', pady=(6, 0))

        # 3.2 三张极简智能建议卡片
        section_label = ctk.CTkFrame(self.scroll_container, fg_color='transparent')
        section_label.pack(fill='x', padx=8, pady=(0, 6))
        ctk.CTkLabel(section_label, text='智能建议清理项', font=self.font_bold,
                     text_color=('gray30', 'gray80')).pack(side='left')
        ctk.CTkLabel(section_label, text='已根据安全性自动完成推荐配置',
                     font=self.font_small, text_color=('gray50', 'gray60')).pack(side='left', padx=(8, 0))

        self.cards_container = ctk.CTkFrame(self.scroll_container, fg_color='transparent')
        self.cards_container.pack(fill='x', padx=8, pady=(0, 10))

        # --- 卡片 1: 基础系统垃圾 ---
        self.card_junk = ctk.CTkFrame(self.cards_container, corner_radius=10, fg_color=('gray95', 'gray16'))
        self.card_junk.pack(fill='x', pady=5)
        cj_inner = ctk.CTkFrame(self.card_junk, fg_color='transparent')
        cj_inner.pack(fill='x', padx=18, pady=14)

        cj_left = ctk.CTkFrame(cj_inner, fg_color='transparent')
        cj_left.pack(side='left')
        cj_title_row = ctk.CTkFrame(cj_left, fg_color='transparent')
        cj_title_row.pack(anchor='w')
        ctk.CTkLabel(cj_title_row, text='基础系统垃圾', font=self.font_card_title).pack(side='left')
        ctk.CTkLabel(cj_title_row, text='零风险', font=self.font_small,
                     fg_color=('#E8F5E9', '#1C3829'), text_color=('#2E7D32', '#66BB6A'),
                     corner_radius=4, padx=6, pady=1).pack(side='left', padx=8)
        ctk.CTkLabel(cj_left, text='渲染引擎缓存、运行追踪日志与小程序包体，清理对聊天记录零影响',
                     font=self.font_card_desc, text_color=('gray45', 'gray65')).pack(anchor='w', pady=(3, 0))

        cj_right = ctk.CTkFrame(cj_inner, fg_color='transparent')
        cj_right.pack(side='right')
        self.junk_size_label = ctk.CTkLabel(cj_right, text='0 B', font=self.font_card_size)
        self.junk_size_label.pack(side='left', padx=(0, 16))
        self.junk_switch_var = ctk.BooleanVar(value=True)
        self.junk_switch = ctk.CTkSwitch(cj_right, text='', variable=self.junk_switch_var,
                                         command=self._update_reclaimable_sum,
                                         width=44, switch_width=44, switch_height=24)
        self.junk_switch.pack(side='left')

        # --- 卡片 2: 多群转发重复文件 ---
        self.card_dedup = ctk.CTkFrame(self.cards_container, corner_radius=10, fg_color=('gray95', 'gray16'))
        self.card_dedup.pack(fill='x', pady=5)

        cd_row = ctk.CTkFrame(self.card_dedup, fg_color='transparent')
        cd_row.pack(fill='x', padx=18, pady=14)

        cd_left = ctk.CTkFrame(cd_row, fg_color='transparent')
        cd_left.pack(side='left')
        cd_title_row = ctk.CTkFrame(cd_left, fg_color='transparent')
        cd_title_row.pack(anchor='w')
        ctk.CTkLabel(cd_title_row, text='多群转发重复文件', font=self.font_card_title).pack(side='left')
        ctk.CTkLabel(cd_title_row, text='无损合并', font=self.font_small,
                     fg_color=('#E3F2FD', '#172B4D'), text_color=('#1565C0', '#42A5F5'),
                     corner_radius=4, padx=6, pady=1).pack(side='left', padx=8)

        self.cd_adv_btn = ctk.CTkButton(
            cd_title_row, text='选项 ▾', command=self._toggle_dedup_adv,
            width=54, height=20, font=self.font_small,
            fg_color='transparent', hover_color=('gray88', 'gray24'),
            text_color=('#007AFF', '#0A84FF'), corner_radius=4)
        self.cd_adv_btn.pack(side='left', padx=4)

        ctk.CTkLabel(cd_left, text='多群转发的同一份视频与文档合并为单份存储 (APFS)，原有聊天窗口均可正常打开',
                     font=self.font_card_desc, text_color=('gray45', 'gray65')).pack(anchor='w', pady=(3, 0))

        cd_right = ctk.CTkFrame(cd_row, fg_color='transparent')
        cd_right.pack(side='right')
        self.dedup_size_label = ctk.CTkLabel(cd_right, text='0 B', font=self.font_card_size)
        self.dedup_size_label.pack(side='left', padx=(0, 16))
        self.dedup_switch_var = ctk.BooleanVar(value=True)
        self.dedup_switch = ctk.CTkSwitch(cd_right, text='', variable=self.dedup_switch_var,
                                          command=self._update_reclaimable_sum,
                                          width=44, switch_width=44, switch_height=24)
        self.dedup_switch.pack(side='left')

        # 展开式高级过滤面板 (初始收起, 展开时 pack 在 cd_row 下方)
        self.cd_adv = ctk.CTkFrame(self.card_dedup, fg_color=('gray90', 'gray20'), corner_radius=8)
        cd_adv_inner = ctk.CTkFrame(self.cd_adv, fg_color='transparent')
        cd_adv_inner.pack(fill='x', padx=14, pady=10)

        ctk.CTkLabel(cd_adv_inner, text='检测门槛:', font=self.font_small,
                     text_color=('gray35', 'gray75')).pack(side='left', padx=(0, 10))
        self.dedup_min_box = ctk.CTkSegmentedButton(
            cd_adv_inner, values=[x[0] for x in DEDUP_SIZE_CHOICES],
            command=lambda _v: self._on_filters_changed(),
            font=self.font_small, height=26,
            selected_color=('#007AFF', '#0A84FF'),
            selected_hover_color=('#0062CC', '#0070E0'),
            unselected_color=('gray84', 'gray26'),
            unselected_hover_color=('gray78', 'gray32'))
        self.dedup_min_box.set(DEDUP_SIZE_CHOICES[1][0])
        self.dedup_min_box.pack(side='left')

        # --- 卡片 3: 历史大文件 ---
        self.card_large = ctk.CTkFrame(self.cards_container, corner_radius=10, fg_color=('gray95', 'gray16'))
        self.card_large.pack(fill='x', pady=5)

        cl_row = ctk.CTkFrame(self.card_large, fg_color='transparent')
        cl_row.pack(fill='x', padx=18, pady=14)

        cl_left = ctk.CTkFrame(cl_row, fg_color='transparent')
        cl_left.pack(side='left')
        cl_title_row = ctk.CTkFrame(cl_left, fg_color='transparent')
        cl_title_row.pack(anchor='w')
        ctk.CTkLabel(cl_title_row, text='历史大文件', font=self.font_card_title).pack(side='left')
        ctk.CTkLabel(cl_title_row, text='空间大户', font=self.font_small,
                     fg_color=('#FFF3E0', '#3D2A14'), text_color=('#E65100', '#FFA726'),
                     corner_radius=4, padx=6, pady=1).pack(side='left', padx=8)

        self.cl_adv_btn = ctk.CTkButton(
            cl_title_row, text='筛选条件 ▾', command=self._toggle_large_adv,
            width=76, height=20, font=self.font_small,
            fg_color='transparent', hover_color=('gray88', 'gray24'),
            text_color=('#007AFF', '#0A84FF'), corner_radius=4)
        self.cl_adv_btn.pack(side='left', padx=4)

        self.cl_desc_label = ctk.CTkLabel(
            cl_left, text='30 天前 · 大于 10MB · 聊天视频 / 压缩包 (已按安全推荐勾选)',
            font=self.font_card_desc, text_color=('gray45', 'gray65'))
        self.cl_desc_label.pack(anchor='w', pady=(3, 0))

        cl_right = ctk.CTkFrame(cl_row, fg_color='transparent')
        cl_right.pack(side='right')
        self.btn_inspect_large = ctk.CTkButton(
            cl_right, text='核对清单 >', command=self._open_large_files_drawer,
            width=90, height=26, font=self.font_small,
            fg_color='transparent', hover_color=('gray85', 'gray30'),
            text_color=('#007AFF', '#0A84FF'))
        self.btn_inspect_large.pack(side='left', padx=(0, 10))

        self.large_size_label = ctk.CTkLabel(cl_right, text='0 B', font=self.font_card_size)
        self.large_size_label.pack(side='left', padx=(0, 16))
        self.large_switch_var = ctk.BooleanVar(value=True)
        self.large_switch = ctk.CTkSwitch(cl_right, text='', variable=self.large_switch_var,
                                          command=self._update_reclaimable_sum,
                                          width=44, switch_width=44, switch_height=24)
        self.large_switch.pack(side='left')

        # 展开式高级过滤面板 (精准两步筛选: 先保护人脉/群聊, 再勾选文件类型)
        self.cl_adv = ctk.CTkFrame(self.card_large, fg_color=('gray92', 'gray18'), corner_radius=8)
        cl_adv_inner = ctk.CTkFrame(self.cl_adv, fg_color='transparent')
        cl_adv_inner.pack(fill='x', padx=14, pady=10)

        # 步骤 1: 核心人脉防删白名单 (先选人/群)
        step1_frame = ctk.CTkFrame(cl_adv_inner, fg_color=('gray86', 'gray24'), corner_radius=6)
        step1_frame.pack(fill='x', pady=(0, 8), padx=2)
        s1_row = ctk.CTkFrame(step1_frame, fg_color='transparent')
        s1_row.pack(fill='x', padx=10, pady=6)
        ctk.CTkLabel(
            s1_row,
            text='步骤 1: 保护核心人脉与群聊 (已自动避开白名单联系人与「合同/发票」关键词)',
            font=self.font_small, text_color=('#007AFF', '#0A84FF'), anchor='w'
        ).pack(side='left', fill='x', expand=True)
        ctk.CTkButton(
            s1_row, text='管理保护名单', command=self._open_whitelist_modal,
            width=100, height=22, font=self.font_small,
            fg_color=('gray76', 'gray36'), hover_color=('gray70', 'gray42'),
            text_color=('gray10', 'gray90'), corner_radius=4
        ).pack(side='right')

        # 步骤 2: 勾选可清理的文件类型 (后选文件, 全部默认不勾选，绝对安全)
        r_types = ctk.CTkFrame(cl_adv_inner, fg_color='transparent')
        r_types.pack(fill='x', pady=(2, 4))
        ctk.CTkLabel(
            r_types,
            text='步骤 2: 勾选允许清理的文件类型 (已按安全推荐默认勾选，可自行增减):',
            font=self.font_small, text_color=('gray30', 'gray80'), anchor='w'
        ).pack(anchor='w', pady=(0, 4))

        types_box = ctk.CTkFrame(r_types, fg_color='transparent')
        types_box.pack(fill='x')
        self.large_type_vars = []
        # 注意: 这里的 key 走的是引擎的「内容语义分类」通道
        # (cleaner.classify_file_type: video/archive/document/other),
        # execute_slimming 会对未命中目录分类的文件再做一次细粒度匹配。
        # 因此 archive/document 是可用的精确筛选, 不是无效 key。
        # 默认勾选 video + archive (可安全清理), document 默认不勾 (办公文档保护)。
        for label, key, default_on in (
            ('聊天视频', 'video', True),
            ('临时压缩包 / 安装包', 'archive', True),
            ('办公重要文档 (默认保护)', 'document', False),
        ):
            var = ctk.BooleanVar(value=default_on)
            cb = ctk.CTkCheckBox(
                types_box, text=label, variable=var,
                command=lambda: self._on_filters_changed(),
                font=self.font_small, height=20, checkbox_width=18, checkbox_height=18,
                checkmark_color='white', fg_color='#007AFF', hover_color='#0062CC'
            )
            cb.pack(side='left', padx=(0, 16))
            self.large_type_vars.append((key, var))

        # 辅助时间与大小范围 (可选调节)
        r_opts = ctk.CTkFrame(cl_adv_inner, fg_color='transparent')
        r_opts.pack(fill='x', pady=(8, 0))

        # 第一行: 清理时间
        r1 = ctk.CTkFrame(r_opts, fg_color='transparent')
        r1.pack(fill='x', pady=2)
        ctk.CTkLabel(r1, text='清理时间:', font=self.font_small,
                     text_color=('gray35', 'gray75'), width=60, anchor='w').pack(side='left')
        self.large_days_box = ctk.CTkSegmentedButton(
            r1, values=[x[0] for x in LARGE_DAYS_CHOICES],
            command=lambda _v: self._on_filters_changed(),
            font=self.font_small, height=26,
            selected_color=('#007AFF', '#0A84FF'),
            selected_hover_color=('#0062CC', '#0070E0'),
            unselected_color=('gray84', 'gray26'),
            unselected_hover_color=('gray78', 'gray32'))
        # 默认 30 天: 实测 90 天阈值在典型机器上匹配 0 个文件, 首次使用
        # 会看到 0 B 而误判工具无用; 30 天既能筛出真实大文件又不激进
        # (清理走废纸篓可恢复, 且白名单优先)。
        self.large_days_box.set(LARGE_DAYS_CHOICES[0][0])
        self.large_days_box.pack(side='left', fill='x', expand=True)

        # 第二行: 最小大小
        r2 = ctk.CTkFrame(r_opts, fg_color='transparent')
        r2.pack(fill='x', pady=(4, 2))
        ctk.CTkLabel(r2, text='最小大小:', font=self.font_small,
                     text_color=('gray35', 'gray75'), width=60, anchor='w').pack(side='left')
        self.large_size_box = ctk.CTkSegmentedButton(
            r2, values=[x[0] for x in LARGE_SIZE_CHOICES],
            command=lambda _v: self._on_filters_changed(),
            font=self.font_small, height=26,
            selected_color=('#007AFF', '#0A84FF'),
            selected_hover_color=('#0062CC', '#0070E0'),
            unselected_color=('gray84', 'gray26'),
            unselected_hover_color=('gray78', 'gray32'))
        self.large_size_box.set(LARGE_SIZE_CHOICES[1][0])
        self.large_size_box.pack(side='left', fill='x', expand=True)

        self._refresh_achievement_async()

    # ---------- 异步调度与队列 ----------

    def _run_async(self, fn: Callable[..., Any], on_done: Callable[[Any], None],
                   busy_text: str, cancellable: bool = False) -> None:
        if getattr(self, '_is_busy', False):
            # 重叠防护: 后台任务进行中拒绝新任务 (cancel_event 是实例属性,
            # 重叠会让旧 job 读到被覆盖的 event, 取消失效), 并回滚下拉显示
            self.status_var.set('已有任务进行中, 请等待完成或取消后再操作')
            cur = getattr(self, 'current_account', None)
            if cur is not None:
                self.account_var.set(f'{cur.account_id} ({cur.version_type})')
            return
        self._is_busy = True
        self.status_var.set(busy_text)
        self._cancel_event = threading.Event() if cancellable else None
        if cancellable:
            self.btn_cancel.pack(side='left', padx=(10, 0))
            self.progress_bar.pack(side='left', padx=(10, 0))
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

    def _on_close(self) -> None:
        self._closing = True
        self._cancel_running()
        try:
            self.root.destroy()
        except Exception:
            pass

    def _cancel_running(self) -> None:
        if self._cancel_event:
            self._cancel_event.set()
            self.status_var.set('正在取消当前操作…')

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
                self._is_busy = False
                self.status_var.set('就绪')
                if kind == 'err':
                    cancelled = isinstance(payload, RuntimeError) and 'cancelled' in str(payload).lower()
                    if cancelled:
                        self.status_var.set('已取消')
                    else:
                        messagebox.showerror('遇到错误', f'操作未能完成: {payload}')
                    break
                if on_done is not None:
                    try:
                        on_done(payload)
                    except Exception as cb_exc:
                        # 回调内异常此前会走 Tk 的 report_callback_exception,
                        # 在窗口化应用里无声无息 (界面停在半更新状态); 显式上报
                        import traceback
                        _log.error('on_done 回调异常:\n%s', traceback.format_exc())
                        messagebox.showerror('遇到错误', f'界面更新失败: {cb_exc}')
        except queue.Empty:
            pass
        finally:
            if not getattr(self, '_closing', False):
                try:
                    self.root.after(100, self._poll_queue)
                except Exception:
                    pass

    # ---------- 诊断分析与数据装载 ----------

    def _init_accounts(self) -> None:
        self._run_async(lambda: discover_accounts(), self._after_discover_accounts, '正在探测微信账号…')

    def _after_discover_accounts(self, accounts) -> None:
        self.accounts = accounts
        if not accounts:
            self.account_var.set('未发现可用账号')
            self.total_size_label.configure(text='0 B')
            self.reclaimable_label.configure(text='未找到微信数据目录')
            if sys.platform == 'darwin':
                offered = messagebox.askyesno(
                    '未找到微信数据',
                    '未能读取到微信存储目录。常见原因:\n\n'
                    '1. 本机尚未登录过桌面版微信\n'
                    '2. 完全磁盘访问权限未授权 (最常见)\n'
                    '   微信容器受 macOS 保护, 未授权时任何工具都无法读取。\n\n'
                    '手动授权路径: 系统设置 → 隐私与安全性 → 完全磁盘访问权限,\n'
                    '打开 CleanYourWechatTool 的开关, 然后重启本应用。\n\n'
                    '是否立即打开该设置面板?',
                )
                if offered:
                    subprocess.run(
                        ['open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'],
                        check=False,
                    )
            else:
                messagebox.showinfo(
                    '未找到微信数据',
                    '未能读取到微信存储目录。常见原因:\n\n'
                    '1. 本机尚未安装或登录过桌面版微信\n'
                    '2. 微信数据保存在非标准自定义盘符，请在微信客户端「设置 → 文件管理」中查看真实存储路径。',
                )
            return

        choices = [f'{acc.account_id} ({acc.version_type})' for acc in accounts]
        self.account_menu.configure(values=choices)
        self.account_var.set(choices[0])
        self.current_account = accounts[0]
        self._diagnose_account_async(self.current_account)

    def _on_account_selected(self, choice: str) -> None:
        # 任务进行中不允许切换账号: 否则 current_account 已在下方被改写,
        # 而 _run_async 的守卫会拒绝新任务并把下拉回滚成"新账号",
        # 造成界面显示与实际扫描的账号错位 (后续 account_root 也会不匹配)。
        if getattr(self, '_is_busy', False):
            cur = getattr(self, 'current_account', None)
            if cur is not None:
                self.account_var.set(f'{cur.account_id} ({cur.version_type})')
            self.status_var.set('已有任务进行中, 请等待完成或取消后再切换账号')
            return
        for acc in self.accounts:
            if acc.account_id in choice:
                self.current_account = acc
                self._diagnose_account_async(acc)
                break

    def _diagnose_account_async(self, acc=None) -> None:
        """后台全维度诊断分析当前账号 (阈值取自高级选项, 默认值已选好)."""
        acc = acc or self.current_account
        if not acc:
            return
        days = self._choice_value(self.large_days_box, LARGE_DAYS_CHOICES, 90)
        min_bytes = self._size_choice_bytes(self.large_size_box, LARGE_SIZE_CHOICES, 10 * 1024 * 1024)
        large_types = [k for k, var in self.large_type_vars if var.get()]
        dedup_min = self._size_choice_bytes(self.dedup_min_box, DEDUP_SIZE_CHOICES, 1024 * 1024)

        def job(progress_cb):
            progress_cb('正在透视微信存储分布…')
            cats = scan_account(acc)

            # 1. 基础系统垃圾: 缓存 + 日志 + 转储 + 插件
            #    预estimation 口径必须与执行一致: 先剔除死线命中项与白名单保护项,
            #    否则界面承诺的释放量会高于实际 (此前实测虚高 22%)
            acc_root = acc.root_path
            wl = self._whitelist()
            junk_files = []
            for k in ('cache', 'radium', 'logs', 'xplugin'):
                if k in cats and cats[k].files:
                    junk_files.extend(
                        f for f in cats[k].files
                        if not is_hard_protected(f[0], acc_root)
                        and not wl.is_protected(f[0], f[2])[0]
                    )

            # 2. 多群重复文件去重
            progress_cb('正在计算多群转发重复文件…')
            dup_groups = find_duplicates(cats, ['video', 'file', 'attach'],
                                         min_size_bytes=dedup_min, whitelist_mgr=wl,
                                         cancel_event=self._cancel_event)

            # 3. 历史大文件 (按高级选项阈值)
            progress_cb('正在筛选历史超大文件…')
            large_res = execute_slimming(acc, cats, days=days, min_size_bytes=min_bytes,
                                         selected_types=large_types, dry_run=True,
                                         whitelist_mgr=wl, cancel_event=self._cancel_event)
            return cats, junk_files, dup_groups, large_res, (days, min_bytes, large_types)

        self._run_async(job, self._after_diagnose, '正在智能诊断微信空间…', cancellable=True)

    @staticmethod
    def _choice_value(box, choices, default):
        text = box.get()
        for label, value in choices:
            if label == text:
                return value
        return default

    @staticmethod
    def _size_choice_bytes(box, choices, default_bytes):
        text = box.get()
        for label, value in choices:
            if label == text:
                return parse_size_str(value)
        return default_bytes

    def _toggle_large_adv(self) -> None:
        if bool(self.cl_adv.winfo_manager()):
            self.cl_adv.pack_forget()
            self.cl_adv_btn.configure(text='筛选条件 ▾')
        else:
            self.cl_adv.pack(fill='x', padx=18, pady=(0, 14))
            self.cl_adv_btn.configure(text='筛选条件 ▴')

    def _toggle_dedup_adv(self) -> None:
        if bool(self.cd_adv.winfo_manager()):
            self.cd_adv.pack_forget()
            self.cd_adv_btn.configure(text='选项 ▾')
        else:
            self.cd_adv.pack(fill='x', padx=18, pady=(0, 14))
            self.cd_adv_btn.configure(text='选项 ▴')

    def _on_filters_changed(self) -> None:
        """高级选项变更: 600ms 去抖后自动重算; 忙碌时标记待重算."""
        if getattr(self, '_is_busy', False):
            self._filters_dirty = True
            return
        self._filters_dirty = False
        job_id = getattr(self, '_filters_job', None)
        if job_id is not None:
            try:
                self.root.after_cancel(job_id)
            except Exception:
                pass
        self._filters_job = self.root.after(600, lambda: self._diagnose_account_async())

    def _after_diagnose(self, payload) -> None:
        cats, junk_files, dup_groups, large_res, (days, min_bytes, large_types) = payload
        self.current_categories = cats
        type_names = {'video': '聊天视频', 'archive': '压缩包/安装包', 'document': '办公文档'}
        if not large_types:
            self.cl_desc_label.configure(
                text='未勾选任何文件类型 · 历史大文件处于 100% 绝对保护状态 (展开筛选条件按需勾选)'
            )
        else:
            types_str = ' / '.join(type_names.get(k, k) for k in large_types)
            count = len(large_res.affected_files)
            hint = '' if count else ' · 当前条件无匹配，可展开筛选条件放宽时间范围'
            self.cl_desc_label.configure(
                text=f'{days} 天前 · 大于 {format_bytes(min_bytes)} · {types_str}'
                     f' → 检测到 {count} 个文件 {format_bytes(large_res.freed_bytes)}{hint}'
            )

        total_bytes = sum(c.total_bytes for c in cats.values())
        self.total_size_label.configure(text=format_bytes(total_bytes))

        # 1. 垃圾卡片
        self.junk_files = junk_files
        self.junk_bytes = sum(s for _, s, _ in junk_files)
        self.junk_size_label.configure(text=format_bytes(self.junk_bytes))

        # 2. 去重卡片
        valid_dups = [g for g in dup_groups if g.wasted_count > 0]
        self.dedup_groups = valid_dups
        self.dedup_bytes = sum(g.saving_bytes for g in valid_dups)
        self.dedup_size_label.configure(text=format_bytes(self.dedup_bytes))

        # 3. 大文件卡片
        self.large_files = sorted(large_res.affected_files, key=lambda t: t[1], reverse=True)
        self.large_bytes = large_res.freed_bytes
        self.large_size_label.configure(text=format_bytes(self.large_bytes))
        self.btn_inspect_large.configure(text=f'核对 {len(self.large_files)} 个文件 >')

        # 初始化大文件元数据 (默认全选)
        self.large_files_meta.clear()
        for idx, (fp, sz, mt) in enumerate(self.large_files):
            self.large_files_meta[str(idx)] = {
                'path': fp, 'size': sz, 'mtime': mt, 'included': True
            }

        self._update_reclaimable_sum()

        # 高级选项在诊断期间被改动: 完成后自动重算一次
        if getattr(self, '_filters_dirty', False):
            self._filters_dirty = False
            self.root.after(300, lambda: self._diagnose_account_async())

    def _update_reclaimable_sum(self) -> None:
        """根据当前开启的卡片开关动态更新预估释放总额与按钮状态."""
        # 合计按**绝对路径**去重: junk 与 large 可能命中同一文件
        # (例如超过 30 天的大视频同时属于缓存与大文件候选), 简单相加会虚高。
        # 两处优雅降级以保持向后兼容: 无 junk 明细时回退预计算值 junk_bytes;
        # 大文件元数据缺 path 键时仍计入其 size (仅不参与去重)。
        counted: set = set()
        reclaimable = 0
        inc_large = [d for d in self.large_files_meta.values() if d.get('included')]
        if self.junk_switch_var.get():
            if self.junk_files:
                for fp, size, _m in self.junk_files:
                    if str(fp) not in counted:
                        counted.add(str(fp))
                        reclaimable += size
            else:
                reclaimable += self.junk_bytes
        if self.large_switch_var.get():
            for d in inc_large:
                fp = d.get('path')
                if fp is None:
                    reclaimable += d.get('size', 0)
                elif str(fp) not in counted:
                    counted.add(str(fp))
                    reclaimable += d.get('size', 0)
        if self.dedup_switch_var.get():
            # 去重释放的是"重复副本占用的空间", 与文件大小语义不同, 无法按路径去重
            reclaimable += self.dedup_bytes

        if reclaimable > 0:
            self.reclaimable_label.configure(
                text=f'预计可安全释放: {format_bytes(reclaimable)}',
                text_color=('#007AFF', '#0A84FF'))
            self.btn_one_key.configure(
                state='normal',
                text=f'一键安全瘦身 (已选 {format_bytes(reclaimable)})')
        else:
            self.reclaimable_label.configure(
                text='当前没有选中可释放项',
                text_color=('gray50', 'gray65'))
            self.btn_one_key.configure(state='disabled', text='一键安全瘦身')

    # ---------- 一键清理执行 ----------

    @staticmethod
    def _wechat_running() -> bool:
        try:
            if sys.platform == 'win32':
                res = subprocess.run(
                    ['tasklist', '/FI', 'IMAGENAME eq WeChat.exe'],
                    capture_output=True, text=True, timeout=2
                )
                return 'WeChat.exe' in res.stdout
            res = subprocess.run(['pgrep', '-x', 'WeChat'], capture_output=True, timeout=2)
            return res.returncode == 0
        except Exception:
            return False

    def _execute_one_key_clean(self) -> None:
        if self._is_busy:
            return

        reclaimable = 0
        if self.junk_switch_var.get():
            reclaimable += self.junk_bytes
        if self.dedup_switch_var.get():
            reclaimable += self.dedup_bytes
        if self.large_switch_var.get():
            inc_large = sum(d['size'] for d in self.large_files_meta.values() if d['included'])
            reclaimable += inc_large

        if reclaimable <= 0:
            messagebox.showinfo('提示', '请至少开启一个清理建议项')
            return

        if self._wechat_running():
            proceed = messagebox.askyesno(
                '提示: 微信正在运行',
                '微信当前处于运行状态。清理过程中微信可能会写入新缓存或锁定接收中的文件。\n\n'
                '建议先退出微信 (Cmd+Q) 后再清理。\n是否仍要继续执行?',
                icon='warning')
            if not proceed:
                return

        confirmed = messagebox.askyesno(
            '确认执行一键瘦身',
            f'即将清理选中的微信数据，预计释放空间: {format_bytes(reclaimable)}。\n\n'
            '所有清理文件将安全移入系统废纸篓，可随时放回原处。确定执行吗?',
        )
        if not confirmed:
            return

        wl = self._whitelist()

        # 收集待移入废纸篓的文件 (按绝对路径去重: junk 与 large 可能命中同一文件,
        #   重复收集会让预估与实际不符, 且第二次必然失败)
        target_trash_files: List[Tuple[Path, int, float]] = []
        _seen_paths: set = set()
        if self.junk_switch_var.get():
            for fp, size, mtime in self.junk_files:
                if str(fp) not in _seen_paths:
                    _seen_paths.add(str(fp))
                    target_trash_files.append((fp, size, mtime))
        if self.large_switch_var.get():
            for d in self.large_files_meta.values():
                fp = d.get('path')
                if d.get('included') and fp is not None and str(fp) not in _seen_paths:
                    _seen_paths.add(str(fp))
                    target_trash_files.append((fp, d.get('size', 0), d.get('mtime', 0.0)))

        do_dedup = self.dedup_switch_var.get() and len(self.dedup_groups) > 0
        dedup_groups_to_run = self.dedup_groups if do_dedup else []
        # 去重与清理互斥: 组内任一文件已被选中移入废纸篓时, 整组跳过去重。
        # 否则会出现"先合并为硬链接、随后该路径又被清理"的连锁动作,
        # 结果超出用户"只合并不删除"的预期。
        if do_dedup and target_trash_files:
            _trash_paths = {str(fp) for fp, _, _ in target_trash_files}
            kept = [g for g in dedup_groups_to_run
                    if not any(str(f) in _trash_paths for f in g.files)]
            if len(kept) != len(dedup_groups_to_run):
                _log.info('去重与清理存在重叠, 已跳过 %d 组以避让清理',
                          len(dedup_groups_to_run) - len(kept))
            dedup_groups_to_run = kept
            do_dedup = bool(dedup_groups_to_run)

        def job(progress_cb):
            total_freed = 0
            # 1. 废纸篓清理
            if target_trash_files:
                res = execute_files_to_trash(
                    target_trash_files,
                    wl,
                    progress_cb=progress_cb,
                    cancel_event=self._cancel_event,
                    account_root=getattr(self.current_account, 'root_path', None),
                )
                total_freed += res.freed_bytes
                StateManager().record_clean(
                    res.freed_count,
                    res.freed_bytes,
                    res.protected_count,
                    res.protected_bytes,
                )

            # 2. 多群去重 (APFS 硬链接)
            if dedup_groups_to_run:
                progress_cb(f'正在执行多群去重 ({len(dedup_groups_to_run)} 组)…')
                d_count, d_freed = execute_dedup(dedup_groups_to_run, action='hardlink',
                                                 dry_run=False, whitelist_mgr=wl)
                total_freed += d_freed
                StateManager().record_dedup(d_count, d_freed, action='hardlink')

            return total_freed

        self._run_async(job, self._after_one_key_clean, '正在执行一键瘦身…', cancellable=True)

    def _after_one_key_clean(self, total_freed: int) -> None:
        msg = f'瘦身完成! 成功释放 {format_bytes(total_freed)} 磁盘空间。'
        trash_name = '系统回收站' if sys.platform == 'win32' else '系统废纸篓'
        open_trash = messagebox.askyesno(
            '清理完成',
            msg + '\n\n'
            f'文件已安全放入{trash_name}。清空{trash_name}后磁盘空间将真正释放。\n'
            f'是否立即打开{trash_name}核对?',
        )
        if open_trash:
            if sys.platform == 'darwin':
                subprocess.run(['open', str(Path.home() / '.Trash')], check=False)
            elif sys.platform == 'win32':
                subprocess.run(['explorer.exe', 'shell:RecycleBinFolder'], check=False)
            else:
                subprocess.run(['xdg-open', str(Path.home() / '.local/share/Trash')], check=False)

        self._refresh_achievement_async()
        if self.current_account:
            self._diagnose_account_async(self.current_account)

    # ---------- 渐进式抽屉: 大文件核对清单 ----------

    def _open_large_files_drawer(self) -> None:
        """打开历史大文件明细抽屉弹窗."""
        if not self.large_files:
            messagebox.showinfo('提示', '当前没有匹配的历史大文件')
            return

        drawer = ctk.CTkToplevel(self.root)
        drawer.title('历史大文件核对清单')
        drawer.geometry('780x560')
        drawer.minsize(680, 440)
        drawer.transient(self.root)
        drawer.grab_set()

        header = ctk.CTkFrame(drawer, corner_radius=0, fg_color='transparent')
        header.pack(fill='x', padx=20, pady=(16, 8))
        ctk.CTkLabel(header, text='历史大文件核对', font=self.font_card_title).pack(side='left')

        stats_var = ctk.StringVar()
        ctk.CTkLabel(header, textvariable=stats_var, font=self.font_small,
                     text_color=('#007AFF', '#0A84FF')).pack(side='right')

        toolbar = ctk.CTkFrame(drawer, corner_radius=0, fg_color='transparent')
        toolbar.pack(fill='x', padx=20, pady=(0, 8))

        # 嵌入 Treeview
        tree_box = ctk.CTkFrame(drawer, corner_radius=8, fg_color=('gray90', 'gray20'))
        tree_box.pack(fill='both', expand=True, padx=20, pady=(0, 12))

        cols = ('inc', 'type', 'size', 'date', 'path')
        tree = ttk.Treeview(tree_box, columns=cols, show='headings', selectmode='extended')
        tree.heading('inc', text='包含')
        tree.column('inc', width=48, anchor='center')
        tree.heading('type', text='类别')
        tree.column('type', width=70, anchor='center')
        tree.heading('size', text='大小')
        tree.column('size', width=90, anchor='e')
        tree.heading('date', text='修改日期')
        tree.column('date', width=110, anchor='center')
        tree.heading('path', text='相对路径')
        tree.column('path', width=450, anchor='w')

        tree.tag_configure('excluded', foreground='#8e8e93')
        tree.pack(side='left', fill='both', expand=True, padx=(4, 0), pady=4)

        scroll = ttk.Scrollbar(tree_box, command=tree.yview, orient='vertical')
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y', pady=4)

        acc = self.current_account
        root_path = acc.root_path if acc else Path.home()

        def update_stats():
            inc = [d for d in self.large_files_meta.values() if d['included']]
            bytes_inc = sum(d['size'] for d in inc)
            stats_var.set(f'已选 {len(inc)} / {len(self.large_files_meta)} 项 · 释放 {format_bytes(bytes_inc)}')

        type_badges = {
            'video': '视频',
            'archive': '压缩包',
            'document': '文档',
            'other': '其他',
        }

        # 填充数据
        for idx_str, d in self.large_files_meta.items():
            fp, sz, mt, inc = d['path'], d['size'], d['mtime'], d['included']
            ft = classify_file_type(fp)
            cat_badge = type_badges.get(ft, '其他')
            try:
                rel = fp.relative_to(root_path)
            except ValueError:
                rel = fp.name
            tree.insert('', 'end', iid=idx_str,
                        values=('☑' if inc else '☐', cat_badge, format_bytes(sz),
                                datetime.fromtimestamp(mt).strftime('%Y-%m-%d'), str(rel)),
                        tags=('' if inc else 'excluded',))

        update_stats()

        def set_included(iid, state):
            if iid in self.large_files_meta:
                self.large_files_meta[iid]['included'] = state
                tree.set(iid, 'inc', '☑' if state else '☐')
                tree.item(iid, tags=('' if state else 'excluded',))
                update_stats()
                inc_bytes = sum(d['size'] for d in self.large_files_meta.values() if d['included'])
                self.large_size_label.configure(text=format_bytes(inc_bytes))
                self._update_reclaimable_sum()

        def on_click(event):
            region = tree.identify('region', event.x, event.y)
            col = tree.identify_column(event.x)
            if region == 'cell' and col == '#1':
                iid = tree.identify_row(event.y)
                # 后台诊断完成会重建 large_files_meta, 抽屉里残留的旧 iid 可能已不存在
                if iid and iid in self.large_files_meta:
                    cur = self.large_files_meta[iid]['included']
                    set_included(iid, not cur)

        def set_all(state):
            for iid in self.large_files_meta:
                self.large_files_meta[iid]['included'] = state
                tree.set(iid, 'inc', '☑' if state else '☐')
                tree.item(iid, tags=('' if state else 'excluded',))
            update_stats()
            inc_bytes = sum(d['size'] for d in self.large_files_meta.values() if d['included'])
            self.large_size_label.configure(text=format_bytes(inc_bytes))
            self._update_reclaimable_sum()

        ctk.CTkButton(toolbar, text='全部包含', command=lambda: set_all(True),
                      width=74, height=26, font=self.font_small,
                      fg_color=('gray80', 'gray30'), text_color=('gray10', 'gray90')).pack(side='left', padx=(0, 6))
        ctk.CTkButton(toolbar, text='全部排除', command=lambda: set_all(False),
                      width=74, height=26, font=self.font_small,
                      fg_color=('gray80', 'gray30'), text_color=('gray10', 'gray90')).pack(side='left', padx=(0, 10))
        ctk.CTkLabel(toolbar, text='单击包含列切换 · 双击打开 · 空格预览',
                     font=self.font_small, text_color=('gray50', 'gray60')).pack(side='left')

        tree.bind('<Button-1>', on_click)
        tree.bind('<Double-1>', lambda _: self._open_drawer_file(tree))
        tree.bind('<space>', lambda _: self._quicklook_drawer_file(tree))

        footer = ctk.CTkFrame(drawer, corner_radius=0, fg_color='transparent')
        footer.pack(fill='x', padx=20, pady=(0, 16))
        ctk.CTkButton(footer, text='完成核对', command=drawer.destroy,
                      width=110, height=32, font=self.font_bold).pack(side='right')

    def _open_drawer_file(self, tree: ttk.Treeview) -> None:
        sel = tree.selection()
        if not sel:
            return
        d = self.large_files_meta.get(sel[0])
        if d and Path(d['path']).exists():
            target = str(d['path'])
            try:
                if sys.platform == 'win32':
                    os.startfile(target)
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', target])
                else:
                    subprocess.Popen(['xdg-open', target])
            except Exception as e:
                _log.warning('无法打开文件 %s: %s', target, e)

    def _quicklook_drawer_file(self, tree: ttk.Treeview) -> None:
        sel = tree.selection()
        if not sel:
            return
        d = self.large_files_meta.get(sel[0])
        if d and Path(d['path']).exists():
            target = str(d['path'])
            try:
                if sys.platform == 'darwin':
                    subprocess.Popen(['qlmanage', '-p', target],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif sys.platform == 'win32':
                    subprocess.Popen(['explorer', f'/select,{target}'])
                else:
                    subprocess.Popen(['xdg-open', target])
            except Exception as e:
                _log.warning('无法预览文件 %s: %s', target, e)

    # ---------- 白名单弹窗 ----------

    def _whitelist(self) -> WhiteListManager:
        if not hasattr(self, '_wl_mgr'):
            self._wl_mgr = WhiteListManager()
        return self._wl_mgr

    def _open_whitelist_modal(self) -> None:
        """打开防删保护规则管理弹窗."""
        modal = ctk.CTkToplevel(self.root)
        modal.title('防删白名单守护')
        modal.geometry('680x480')
        modal.minsize(600, 380)
        modal.transient(self.root)
        modal.grab_set()

        header = ctk.CTkFrame(modal, corner_radius=0, fg_color='transparent')
        header.pack(fill='x', padx=20, pady=(16, 8))
        ctk.CTkLabel(header, text='防删白名单规则', font=self.font_card_title).pack(anchor='w')
        ctk.CTkLabel(header, text='命中的联系人、群聊或关键词文件在任何清理与去重中绝对不会被触碰',
                     font=self.font_small, text_color=('gray50', 'gray60')).pack(anchor='w')

        # 输入卡片
        in_card = ctk.CTkFrame(modal, corner_radius=8, fg_color=('gray92', 'gray18'))
        in_card.pack(fill='x', padx=20, pady=8)

        r1 = ctk.CTkFrame(in_card, fg_color='transparent')
        r1.pack(fill='x', padx=14, pady=(10, 4))
        ctk.CTkLabel(r1, text='保护名称:', font=self.font_body).pack(side='left')
        name_entry = ctk.CTkEntry(r1, placeholder_text='如: 家人 / 重点客户 / 财务群', width=200)
        name_entry.pack(side='left', padx=(6, 16))

        ctk.CTkLabel(r1, text='关键词:', font=self.font_body).pack(side='left')
        kw_entry = ctk.CTkEntry(r1, placeholder_text='如: 合同,对账单,宝宝', width=240)
        kw_entry.pack(side='left', padx=6)

        r2 = ctk.CTkFrame(in_card, fg_color='transparent')
        r2.pack(fill='x', padx=14, pady=(4, 10))

        # 列表卡片
        list_box = ctk.CTkFrame(modal, corner_radius=8, fg_color=('gray90', 'gray20'))
        list_box.pack(fill='both', expand=True, padx=20, pady=(0, 10))

        tree = ttk.Treeview(list_box, columns=('name', 'kw'), show='headings', selectmode='browse')
        tree.heading('name', text='保护对象')
        tree.column('name', width=200, anchor='w')
        tree.heading('kw', text='关键词')
        tree.column('kw', width=400, anchor='w')
        tree.pack(side='left', fill='both', expand=True, padx=(4, 0), pady=4)

        scroll = ttk.Scrollbar(list_box, command=tree.yview, orient='vertical')
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y', pady=4)

        def refresh_list():
            for item in tree.get_children():
                tree.delete(item)
            for r in self._whitelist().list_rules():
                tree.insert('', 'end', values=(r.name, ', '.join(r.keywords) or '-'))

        def add_rule():
            name = name_entry.get().strip()
            if not name:
                messagebox.showwarning('提示', '请填写保护对象名称')
                return
            kw = [k.strip() for k in kw_entry.get().split(',') if k.strip()] or [name]
            self._whitelist().add(name=name, wxid=f'kw:{name}', protect='absolute', keywords=kw)
            name_entry.delete(0, 'end')
            kw_entry.delete(0, 'end')
            refresh_list()

        def remove_rule():
            sel = tree.selection()
            if not sel:
                return
            n = tree.item(sel[0], 'values')[0]
            self._whitelist().remove(n)
            refresh_list()

        ctk.CTkButton(r2, text='添加保护', command=add_rule, width=90, height=28,
                      font=self.font_bold, fg_color='#34C759', hover_color='#248A3D').pack(side='left', padx=(0, 8))
        ctk.CTkButton(r2, text='移除选中', command=remove_rule, width=90, height=28,
                      font=self.font_body, fg_color='transparent', border_width=1,
                      border_color='#FF3B30', text_color='#FF3B30').pack(side='left')

        refresh_list()

        foot = ctk.CTkFrame(modal, corner_radius=0, fg_color='transparent')
        foot.pack(fill='x', padx=20, pady=(0, 14))
        ctk.CTkButton(foot, text='完成', command=modal.destroy, width=90, height=30).pack(side='right')

    # ---------- 成就统计 ----------

    def _refresh_achievement_async(self) -> None:
        def job():
            st = StateManager()
            return st.total_freed_bytes, st.total_cleans, st.total_dedups

        def on_done(data):
            freed, cleans, dedups = data
            if freed > 0:
                self.achievement_var.set(
                    f'累计已为本机释放 {format_bytes(freed)} (清理 {cleans} 次 / 去重 {dedups} 次)')

        self._run_async(job, on_done, '正在读取统计…')


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
