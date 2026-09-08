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

import queue
import subprocess
import sys
import threading
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
TYPE_LABELS = [('聊天视频', 'video'), ('接收的文件', 'file'), ('图片附件', 'attach'), ('临时缓存', 'cache')]


class CleanYourWechatApp:
    """单窗口三 Tab: 智能瘦身 / 重复文件去重 / 防删白名单."""

    def __init__(self, root: ttk.Window) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry('980x720')
        self.root.minsize(880, 640)

        self.queue: 'queue.Queue[Tuple[str, Callable[..., None], Any]]' = queue.Queue()
        self.accounts = []
        self.current_categories: Dict[str, Any] = {}
        self.current_account: Optional[Any] = None
        self.preview_result = None
        self._build_ui()
        self._poll_queue()
        self.root.after(200, self._init_accounts)

    # ---------- UI 构建 ----------

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(16, 12, 16, 4))
        header.pack(fill=X)
        ttk.Label(header, text=APP_TITLE, font=('-size', 15, '-weight', 'bold')).pack(side=LEFT)
        self.lbl_account = ttk.Label(header, text='正在探测微信账号…', bootstyle='secondary')
        self.lbl_account.pack(side=RIGHT)

        self.notebook = ttk.Notebook(self.root, padding=8)
        self.notebook.pack(fill=BOTH, expand=YES, padx=12, pady=(4, 0))
        self._build_clean_tab()
        self._build_dedup_tab()
        self._build_whitelist_tab()

        self.status_var = ttk.StringVar(value='就绪')
        status = ttk.Frame(self.root, padding=(16, 6, 16, 10))
        status.pack(fill=X, side=BOTTOM)
        ttk.Label(status, textvariable=self.status_var, bootstyle='info').pack(side=LEFT)

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

        result_frame = ttk.Labelframe(tab, text='第二步 · 核对将处理的文件清单', padding=8)
        result_frame.pack(fill=BOTH, expand=YES)
        columns = ('size', 'date', 'path')
        self.clean_tree = ttk.Treeview(result_frame, columns=columns, show='headings', height=13)
        for col, text, width in (('size', '大小', 90), ('date', '最后修改', 110), ('path', '文件路径', 620)):
            self.clean_tree.heading(col, text=text)
            self.clean_tree.column(col, width=width, anchor=W if col == 'path' else E)
        self.clean_tree.pack(fill=BOTH, expand=YES)
        scroll = ttk.Scrollbar(result_frame, command=self.clean_tree.yview, orient=VERTICAL)
        self.clean_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=RIGHT, fill=Y)

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

        frame = ttk.Labelframe(tab, text='重复文件清单', padding=8)
        frame.pack(fill=BOTH, expand=YES)
        self.dedup_text = ttk.Text(frame, height=18, wrap='none')
        self.dedup_text.pack(fill=BOTH, expand=YES)
        self.dedup_result = None

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

    def _run_async(self, fn: Callable[[], Any], on_done: Callable[[Any], None], busy_text: str) -> None:
        self.status_var.set(busy_text)
        self._set_busy(True)

        def worker() -> None:
            try:
                self.queue.put(('ok', on_done, fn()))
            except Exception as exc:  # 后台线程异常统一回到主线程呈现
                self.queue.put(('err', on_done, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, on_done, payload = self.queue.get_nowait()
                self._set_busy(False)
                self.status_var.set('就绪')
                if kind == 'err':
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
        self._run_async(self._load_accounts, self._after_accounts, '正在探测微信账号…')

    def _load_accounts(self):
        self.accounts = discover_accounts()
        if self.accounts:
            self.current_account = self.accounts[0]
            self.current_categories = scan_account(self.current_account)
        return self.accounts

    def _after_accounts(self, accounts) -> None:
        if not accounts:
            self.lbl_account.config(text='未发现微信数据目录', bootstyle='danger')
            # 两种常见原因: ①从未在此 Mac 登录微信 ②终端/App 未获得
            # "完全磁盘访问权限"——微信容器目录受 TCC 保护, 无权限时
            # 扫描到的目录为空。给出可操作的修复引导, 而不是让用户猜。
            messagebox.showwarning(
                '未找到微信数据',
                '没有找到可分析的微信账号目录。常见原因:\n\n'
                '① 本机从未登录过桌面版微信\n'
                '   → 请先登录一次微信, 再重新打开本工具。\n\n'
                '② macOS 隐私权限未授权 (最常见)\n'
                '   → 打开 系统设置 → 隐私与安全性 → 完全磁盘访问权限,\n'
                '     将 CleanYourWechatTool (或运行它的终端) 加入列表,\n'
                '     然后重启本工具。\n\n'
                '微信容器位于 ~/Library/Containers/com.tencent.xinWeChat,\n'
                '没有"完全磁盘访问权限"时任何工具都无法读取它。',
            )
            return
        acc = self.current_account
        total = sum(c.total_bytes for c in self.current_categories.values())
        self.lbl_account.config(text=f'{acc.account_id} · {acc.version_type} · 共 {format_bytes(total)}',
                                bootstyle='success')

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

        def job():
            return execute_slimming(acc, cats, days, min_size, types, dry_run=True,
                                    archive_to=archive_to, whitelist_mgr=self._whitelist())

        self._run_async(job, lambda res: self._after_preview(res, acc, archive_to), '正在扫描匹配文件…')

    def _after_preview(self, res, acc, archive_to) -> None:
        self.preview_result = res if res.freed_count > 0 else None
        for item in self.clean_tree.get_children():
            self.clean_tree.delete(item)
        mode_text = '移入废纸篓' if archive_to is None else f'归档到 {archive_to}'
        if res.freed_count == 0:
            self.clean_result_var.set('没有符合条件的文件, 无需清理。')
            self.btn_execute.state(['disabled'])
            return
        files = sorted(res.affected_files, key=lambda t: t[1], reverse=True)
        for fp, size, mtime in files:
            try:
                rel = fp.relative_to(acc.root_path)
            except ValueError:
                rel = fp
            self.clean_tree.insert('', END, values=(format_bytes(size), datetime.fromtimestamp(mtime).strftime('%Y-%m-%d'), str(rel)))
        summary = f'共 {res.freed_count:,} 个文件 / {format_bytes(res.freed_bytes)} —— 确认清单后点"② 执行清理"'
        if res.protected_count:
            summary += f' (白名单已保护 {res.protected_count:,} 个文件)'
        self.clean_result_var.set(summary)
        self.btn_execute.state(['!disabled'])
        self.status_var.set(f'预览完成: {len(files):,} 个文件, 将{mode_text}')

    def _start_clean(self) -> None:
        if not self.preview_result:
            messagebox.showinfo('先预览', '请先点击"① 预览将处理的文件"核对清单')
            return
        res = self.preview_result
        # 防呆: 微信运行中清理, 统计不准且可能锁定正接收的文件
        if self._wechat_running() and not self._warn_wechat_running():
            return
        if not messagebox.askyesno('最后确认',
                                       f'将处理 {res.freed_count:,} 个文件 (释放 {format_bytes(res.freed_bytes)})。\n'
                                       '文件会进入废纸篓/归档目录, 可随时还原。\n\n确定执行吗?'):
            return
        try:
            days, min_size, types, archive_to = self._collect_args()
        except ValueError as exc:
            messagebox.showwarning('还差一步', str(exc))
            return
        acc, cats = self.current_account, self.current_categories

        def job():
            result = execute_slimming(acc, cats, days, min_size, types, dry_run=False,
                                      archive_to=archive_to, whitelist_mgr=self._whitelist())
            StateManager().record_clean(result.freed_count, result.freed_bytes,
                                        result.protected_count, result.protected_bytes,
                                        is_archive=bool(archive_to))
            return result

        self._run_async(job, lambda r: self._after_clean(r, archive_to), '正在执行清理…')

    def _after_clean(self, res, archive_to) -> None:
        self.preview_result = None
        self.btn_execute.state(['disabled'])
        for item in self.clean_tree.get_children():
            self.clean_tree.delete(item)
        msg = f'✅ 完成! 释放 {format_bytes(res.freed_bytes)} (处理 {res.freed_count:,} 个文件)'
        if res.protected_count:
            msg += f', 白名单保护 {res.protected_count:,} 个未触碰'
        tips = ['去 微信 → 设置 → 通用 → 存储空间 里核对占用变化 (微信的统计可能延迟刷新)']
        tips.append('清空系统废纸篓后磁盘空间才会真正释放' if archive_to is None
                    else f'文件已完整保存在: {archive_to}')
        self.clean_result_var.set(msg)
        messagebox.showinfo('清理完成', msg + '\n\n' + '\n'.join('· ' + t for t in tips))
        self._init_accounts()

    # ---------- 去重 ----------

    def _start_dedup_scan(self) -> None:
        if not self.current_account:
            messagebox.showwarning('提示', '未发现微信账号目录')
            return
        min_size = parse_size_str(SIZE_CHOICES[self.dedup_size_box.current()][1])
        acc, cats = self.current_account, self.current_categories

        def job():
            return find_duplicates(cats, ['video', 'file', 'attach'], min_size_bytes=min_size,
                                   whitelist_mgr=self._whitelist())

        self._run_async(job, self._after_dedup_scan, '正在计算文件指纹 (大文件可能需要一些时间)…')

    def _after_dedup_scan(self, groups) -> None:
        self.dedup_result = [g for g in groups if g.wasted_count > 0]
        self.dedup_text.delete('1.0', END)
        if not self.dedup_result:
            self.dedup_text.insert(END, '✅ 未发现重复文件, 当前没有可释放的冗余空间。\n')
            self.btn_dedup_exec.state(['disabled'])
            return
        total_saving = sum(g.saving_bytes for g in self.dedup_result)
        self.dedup_text.insert(END, f'发现 {len(self.dedup_result)} 组重复文件, 可释放 {format_bytes(total_saving)}:\n\n')
        for idx, g in enumerate(self.dedup_result, 1):
            self.dedup_text.insert(END, f'[{idx}] {format_bytes(g.file_size)}/个 × {g.wasted_count + 1} 份 (可释放 {format_bytes(g.saving_bytes)}):\n')
            self.dedup_text.insert(END, f'    保留: {g.files[0]}\n')
            for dup in g.files[1:]:
                self.dedup_text.insert(END, f'    重复: {dup}\n')
            self.dedup_text.insert(END, '\n')
        self.btn_dedup_exec.state(['!disabled'])

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
        self.dedup_text.insert(END, f'\n✅ 去重完成: 处理 {count:,} 个副本, 释放 {format_bytes(freed)}。\n')
        self.btn_dedup_exec.state(['disabled'])
        messagebox.showinfo('去重完成', f'已处理 {count:,} 个重复副本, 释放 {format_bytes(freed)} 磁盘空间。\n'
                                           '所有聊天窗口里的文件仍可正常打开。')

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


def main() -> None:
    root = ttk.Window(themename='flatly', title=APP_TITLE)
    CleanYourWechatApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
