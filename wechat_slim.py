#!/usr/bin/env python3
"""WeChat Slim - 微信智能瘦身与无损归档极简 CLI.

支持自动探测 Mac 微信 4.0+ 与 3.x 存储目录，
支持文件类型过滤（视频/文件/附件/缓存）、时间跨度过滤与大小过滤，
支持安全移入废纸篓（可撤销）与无损外置硬盘归档，
绝不修改或删除核心聊天记录数据库 (db_storage / *.db)。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple, Set, Union
import urllib.parse
import webbrowser
import logging

# 引入核心人脉白名单与状态记录管理器
try:
    from wechat_intelligence_hub.engine.whitelist import WhiteListManager, WhiteListRule
    from wechat_intelligence_hub.engine.state import StateManager, SlimHistoryRecord
except ImportError:
    try:
        from engine.whitelist import WhiteListManager, WhiteListRule
        from engine.state import StateManager, SlimHistoryRecord
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'projects' / 'wechat-intelligence-hub'))
        from engine.whitelist import WhiteListManager, WhiteListRule
        from engine.state import StateManager, SlimHistoryRecord


class Colors:
    """零依赖 ANSI 彩色终端输出."""
    USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    RESET = "\033[0m" if USE_COLOR else ""
    BOLD = "\033[1m" if USE_COLOR else ""
    GREEN = "\033[32m" if USE_COLOR else ""
    BLUE = "\033[34m" if USE_COLOR else ""
    YELLOW = "\033[33m" if USE_COLOR else ""
    RED = "\033[31m" if USE_COLOR else ""
    CYAN = "\033[36m" if USE_COLOR else ""
    GRAY = "\033[90m" if USE_COLOR else ""
    MAGENTA = "\033[35m" if USE_COLOR else ""


def setup_logger(log_file: Optional[Path] = None) -> logging.Logger:
    """初始化审计日志系统，记录到 ~/.wechat_slim/audit.log."""
    logger = logging.getLogger("wechat_slim")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        if not log_file:
            log_dir = Path.home() / ".wechat_slim"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "audit.log"
        try:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.INFO)
            formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception:
            pass
    return logger


_audit_logger = setup_logger()


def render_progress(current: int, total: int, prefix: str = "", bar_len: int = 25) -> None:
    """平滑终端字符动态进度条."""
    if not sys.stdout.isatty() or total <= 0:
        return
    pct = min(1.0, current / total)
    filled = int(bar_len * pct)
    bar = "█" * filled + "░" * (bar_len - filled)
    sys.stdout.write(f"\r  {prefix} [{bar}] {pct*100:5.1f}% ({current:,}/{total:,})")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def format_bytes(size: float) -> str:
    """格式化字节大小为人类可读字符串."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0 or unit == 'TB':
            return f'{size:.1f} {unit}'
        size /= 1024.0
    return f'{size:.1f} B'


def parse_size_str(size_str: str) -> int:
    """解析大小字符串 (如 10MB, 500KB, 1GB) 为字节数."""
    s = size_str.strip().upper()
    if not s:
        return 0
    multipliers = {
        'B': 1,
        'K': 1024,
        'KB': 1024,
        'M': 1024 * 1024,
        'MB': 1024 * 1024,
        'G': 1024 * 1024 * 1024,
        'GB': 1024 * 1024 * 1024,
    }
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            num = s[: -len(suffix)].strip()
            try:
                return int(float(num) * mult)
            except ValueError:
                break
    try:
        return int(s)
    except ValueError:
        raise ValueError(f'无法识别的大小格式: {size_str} (例如: 10MB, 500KB)')


@dataclass
class AccountProfile:
    """微信账号存储路径描述."""
    account_id: str
    version_type: str  # 'v4' or 'v3'
    root_path: Path
    db_path: Optional[Path] = None
    msg_video_path: Optional[Path] = None
    msg_file_path: Optional[Path] = None
    msg_attach_path: Optional[Path] = None
    cache_path: Optional[Path] = None
    temp_path: Optional[Path] = None


@dataclass
class ScanCategory:
    """某一类文件的空间统计."""
    name: str
    description: str
    path: Path
    is_protected: bool = False
    file_count: int = 0
    total_bytes: int = 0
    files: List[Tuple[Path, int, float]] = field(default_factory=list)  # (path, size, mtime)


def discover_accounts(custom_path: Optional[Path] = None) -> List[AccountProfile]:
    """自动发现或指定当前 Mac 上的微信存储账号路径."""
    if custom_path:
        cp = Path(custom_path).resolve()
        if cp.is_dir():
            if (cp / 'db_storage').exists() or (cp / 'msg').exists() or (cp / 'cache').exists():
                return [
                    AccountProfile(
                        account_id=cp.name,
                        version_type='custom (自定义目录)',
                        root_path=cp,
                        db_path=cp / 'db_storage' if (cp / 'db_storage').exists() else None,
                        msg_video_path=cp / 'msg/video' if (cp / 'msg/video').exists() else None,
                        msg_file_path=cp / 'msg/file' if (cp / 'msg/file').exists() else None,
                        msg_attach_path=cp / 'msg/attach' if (cp / 'msg/attach').exists() else None,
                        cache_path=cp / 'cache' if (cp / 'cache').exists() else None,
                        temp_path=cp / 'temp' if (cp / 'temp').exists() else None,
                    )
                ]
            accs = []
            for sub in cp.iterdir():
                if sub.is_dir() and not sub.name.startswith('.'):
                    accs.append(AccountProfile(
                        account_id=sub.name,
                        version_type='custom (自定义目录)',
                        root_path=sub,
                        db_path=sub / 'db_storage' if (sub / 'db_storage').exists() else None,
                        msg_video_path=sub / 'msg/video' if (sub / 'msg/video').exists() else None,
                        msg_file_path=sub / 'msg/file' if (sub / 'msg/file').exists() else None,
                        msg_attach_path=sub / 'msg/attach' if (sub / 'msg/attach').exists() else None,
                        cache_path=sub / 'cache' if (sub / 'cache').exists() else None,
                        temp_path=sub / 'temp' if (sub / 'temp').exists() else None,
                    ))
            if accs:
                return accs
        return []

    accounts: List[AccountProfile] = []
    home = Path.home()

    # 1. 微信 4.0+ 路径: ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/
    v4_base = home / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
    if v4_base.is_dir():
        for item in v4_base.iterdir():
            if item.is_dir() and item.name not in ['all_users', 'Backup'] and not item.name.startswith('.'):
                acc = AccountProfile(
                    account_id=item.name,
                    version_type='v4 (微信 4.0+)',
                    root_path=item,
                    db_path=item / 'db_storage' if (item / 'db_storage').exists() else None,
                    msg_video_path=item / 'msg/video' if (item / 'msg/video').exists() else None,
                    msg_file_path=item / 'msg/file' if (item / 'msg/file').exists() else None,
                    msg_attach_path=item / 'msg/attach' if (item / 'msg/attach').exists() else None,
                    cache_path=item / 'cache' if (item / 'cache').exists() else None,
                    temp_path=item / 'temp' if (item / 'temp').exists() else None,
                )
                accounts.append(acc)

    # 2. 微信 3.x 传统路径: ~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/
    v3_base = home / 'Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat'
    if v3_base.is_dir():
        for ver in v3_base.iterdir():
            if ver.is_dir() and not ver.name.startswith('.'):
                for acc_dir in ver.iterdir():
                    if acc_dir.is_dir() and len(acc_dir.name) == 32 and not acc_dir.name.startswith('.'):
                        acc = AccountProfile(
                            account_id=acc_dir.name[:8] + '...',
                            version_type=f'v3 ({ver.name})',
                            root_path=acc_dir,
                            msg_attach_path=acc_dir / 'Message/MessageTemp' if (acc_dir / 'Message/MessageTemp').exists() else None,
                            cache_path=acc_dir / 'Caches' if (acc_dir / 'Caches').exists() else None,
                        )
                        accounts.append(acc)

    return accounts


def scan_directory(category_name: str, desc: str, dir_path: Optional[Path], is_protected: bool = False) -> ScanCategory:
    """递归统计指定目录下的文件数量与总大小."""
    cat = ScanCategory(name=category_name, description=desc, path=dir_path or Path('/dev/null'), is_protected=is_protected)
    if not dir_path or not dir_path.exists():
        return cat

    for root, _, files in os.walk(dir_path):
        for f in files:
            fp = Path(root) / f
            try:
                st = fp.stat()
                cat.file_count += 1
                cat.total_bytes += st.st_size
                cat.files.append((fp, st.st_size, st.st_mtime))
            except (OSError, PermissionError):
                continue

    return cat


def scan_account(acc: AccountProfile) -> Dict[str, ScanCategory]:
    """对单个账号执行全量存储透视扫描."""
    results: Dict[str, ScanCategory] = {}

    # 1. 核心数据库 (必须保护)
    results['db'] = scan_directory('db_storage', '核心聊天数据库与文字索引 [🔒 绝对保护，禁止删除]', acc.db_path, is_protected=True)

    # 2. 视频缓存
    results['video'] = scan_directory('video', '接收与缓存的视频文件 (msg/video)', acc.msg_video_path)

    # 3. 接收文件
    results['file'] = scan_directory('file', '接收的文档与办公文件 (msg/file)', acc.msg_file_path)

    # 4. 聊天图片与多媒体附件
    results['attach'] = scan_directory('attach', '聊天图片、表情与多媒体附件 (msg/attach)', acc.msg_attach_path)

    # 5. 缓存与临时文件
    cache_files: List[Tuple[Path, int, float]] = []
    total_cache_size = 0
    total_cache_count = 0
    for p in [acc.cache_path, acc.temp_path]:
        if p and p.exists():
            c = scan_directory('cache_raw', '', p)
            cache_files.extend(c.files)
            total_cache_size += c.total_bytes
            total_cache_count += c.file_count

    results['cache'] = ScanCategory(
        name='cache',
        description='运行临时缓存与缩略图 (cache/temp) [可安全清理]',
        path=acc.cache_path or acc.root_path,
        file_count=total_cache_count,
        total_bytes=total_cache_size,
        files=cache_files,
    )

    return results


def move_to_trash(file_path: Path) -> bool:
    """安全将文件移入 macOS 废纸篓 (可通过访达随时放回原处)."""
    try:
        resolved = str(file_path.resolve())
        cmd = ['osascript', '-e', f'tell application "Finder" to delete POSIX file "{resolved}"']
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False


@dataclass
class SlimResult:
    """瘦身执行统计结果 (支持解构赋值 (freed_count, freed_bytes) 保持向下兼容)."""
    freed_count: int
    freed_bytes: int
    protected_count: int = 0
    protected_bytes: int = 0

    def __iter__(self):
        return iter((self.freed_count, self.freed_bytes))


def execute_slimming(
    acc: AccountProfile,
    categories: Dict[str, ScanCategory],
    days: int,
    min_size_bytes: int,
    selected_types: List[str],
    dry_run: bool = False,
    archive_to: Optional[Path] = None,
    whitelist_mgr: Optional[WhiteListManager] = None,
) -> SlimResult:
    """执行瘦身与清理操作 (集成核心人脉防删白名单检查).
    
    返回: SlimResult (可解构为 (清理文件数, 释放字节数))
    """
    cutoff_time = datetime.now() - timedelta(days=days) if days > 0 else datetime.now() + timedelta(days=99999)
    cutoff_ts = cutoff_time.timestamp()

    freed_bytes = 0
    freed_count = 0
    protected_bytes = 0
    protected_count = 0

    if archive_to:
        archive_to = archive_to.resolve()
        if not dry_run:
            archive_to.mkdir(parents=True, exist_ok=True)

    total_target_files = sum(len(c.files) for k, c in categories.items() if k in selected_types and not c.is_protected)
    cur_idx = 0

    for type_key in selected_types:
        cat = categories.get(type_key)
        if not cat or cat.is_protected:
            continue

        for fp, size, mtime in cat.files:
            cur_idx += 1
            if not dry_run and total_target_files > 50 and cur_idx % 20 == 0:
                render_progress(cur_idx, total_target_files, prefix="正在瘦身处理")

            # 绝对安全护栏 1：绝不处理数据库文件
            if fp.suffix in ['.db', '.db-wal', '.db-shm', '.sqlite', '.wcdb'] or 'db_storage' in fp.parts:
                continue

            # 过滤条件 1: 文件大小阈值
            if size < min_size_bytes:
                continue

            # 过滤条件 2: 时间跨度 (mtime 必须早于截断时间)
            if days > 0 and mtime > cutoff_ts:
                continue

            # 绝对安全护栏 2 (Phase 2): 核心人脉防删白名单检查
            if whitelist_mgr:
                is_prot, _ = whitelist_mgr.is_protected(fp, mtime)
                if is_prot:
                    protected_count += 1
                    protected_bytes += size
                    continue

            # 命中待处理文件
            freed_count += 1
            freed_bytes += size

            if dry_run:
                continue

            if archive_to:
                # 归档模式：计算相对路径并安全移动到外置目录
                try:
                    rel_path = fp.relative_to(acc.root_path)
                except ValueError:
                    rel_path = Path(cat.name) / fp.name
                dest_path = archive_to / rel_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(fp), str(dest_path))
            else:
                # 默认安全清理：移至 macOS 废纸篓
                move_to_trash(fp)

    if not dry_run and total_target_files > 50:
        render_progress(total_target_files, total_target_files, prefix="正在瘦身处理")

    return SlimResult(freed_count, freed_bytes, protected_count, protected_bytes)


@dataclass
class DuplicateGroup:
    """一组内容完全相同的重复文件."""
    file_hash: str
    file_size: int
    files: List[Path]
    saving_bytes: int = 0
    wasted_count: int = 0


def compute_fast_hash(fp: Path, size: int) -> str:
    """快速稀疏哈希: 仅采样头、中、尾生成指纹，大幅加速大文件初筛."""
    chunk = 16384
    hasher = hashlib.md5()
    try:
        with open(fp, 'rb') as f:
            if size <= chunk * 3:
                hasher.update(f.read())
            else:
                hasher.update(f.read(chunk))
                f.seek(size // 2 - chunk // 2)
                hasher.update(f.read(chunk))
                f.seek(size - chunk)
                hasher.update(f.read(chunk))
    except (OSError, PermissionError):
        return ''
    return hasher.hexdigest()


def compute_full_hash(fp: Path) -> str:
    """全量 MD5 计算完整文件校验和."""
    hasher = hashlib.md5()
    try:
        with open(fp, 'rb') as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
    except (OSError, PermissionError):
        return ''
    return hasher.hexdigest()


def find_duplicates(
    categories: Dict[str, ScanCategory],
    selected_types: List[str],
    min_size_bytes: int = 1024,
) -> List[DuplicateGroup]:
    """三级流水线快速查找重复文件 (大小桶分流 -> 稀疏哈希 -> 全量哈希)."""
    # 1. 收集文件并按文件精确大小归类 (大小不同的文件绝不可能是重复文件)
    size_buckets: Dict[int, List[Path]] = defaultdict(list)
    for t in selected_types:
        cat = categories.get(t)
        if not cat or cat.is_protected:
            continue
        for fp, size, _ in cat.files:
            if fp.suffix in ['.db', '.db-wal', '.db-shm', '.sqlite', '.wcdb'] or 'db_storage' in fp.parts:
                continue
            if size > 0 and size >= min_size_bytes:
                size_buckets[size].append(fp)

    # 2. 仅对存在相同大小的文件进行快速哈希初筛
    candidate_buckets = [fps for fps in size_buckets.values() if len(fps) > 1]
    fast_hash_buckets: Dict[Tuple[int, str], List[Path]] = defaultdict(list)
    for fps in candidate_buckets:
        for fp in fps:
            try:
                sz = fp.stat().st_size
                fh = compute_fast_hash(fp, sz)
                if fh:
                    fast_hash_buckets[(sz, fh)].append(fp)
            except (OSError, PermissionError):
                continue

    # 3. 仅对稀疏哈希碰撞的文件进行全量 MD5 确认
    full_hash_groups: Dict[str, Tuple[int, List[Path]]] = defaultdict(lambda: (0, []))
    for (size, _), fps in fast_hash_buckets.items():
        if len(fps) <= 1:
            continue
        for fp in fps:
            try:
                full_h = compute_full_hash(fp)
                if full_h:
                    prev_size, prev_list = full_hash_groups[full_h]
                    full_hash_groups[full_h] = (size, prev_list + [fp])
            except (OSError, PermissionError):
                continue

    duplicate_groups: List[DuplicateGroup] = []
    for fhash, (size, fps) in full_hash_groups.items():
        if len(fps) > 1:
            # 检查是否有文件已经互为硬链接 (相同 st_dev 和 st_ino)
            inodes_seen: Set[Tuple[int, int]] = set()
            wasted_count = 0
            for fp in fps:
                try:
                    st = fp.stat()
                    key = (st.st_dev, st.st_ino)
                    if key in inodes_seen:
                        continue
                    inodes_seen.add(key)
                except OSError:
                    continue

            if len(inodes_seen) > 1:
                wasted_count = len(inodes_seen) - 1
                saving = wasted_count * size
            else:
                wasted_count = 0
                saving = 0

            # 按修改时间排序，保留最早或最基础的文件为主副本
            fps.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
            duplicate_groups.append(DuplicateGroup(
                file_hash=fhash,
                file_size=size,
                files=fps,
                saving_bytes=saving,
                wasted_count=wasted_count,
            ))

    # 按可释放空间由大到小排序
    duplicate_groups.sort(key=lambda g: g.saving_bytes, reverse=True)
    return duplicate_groups


def execute_dedup(
    groups: List[DuplicateGroup],
    action: str = 'hardlink',
    dry_run: bool = False,
) -> Tuple[int, int]:
    """执行重复文件去重.
    
    action='hardlink': (推荐) 将重复文件原子替换为系统硬链接，原路径原文件名完全保留，
                       微信内各群聊仍可正常读取，但在 macOS APFS 物理磁盘仅占一份空间！
    action='trash':    将冗余副本直接移至 macOS 废纸篓。
    """
    processed_count = 0
    freed_bytes = 0
    total_copies = sum(len(grp.files) - 1 for grp in groups if grp.wasted_count > 0 and len(grp.files) >= 2)

    for grp in groups:
        if grp.wasted_count == 0 or len(grp.files) < 2:
            continue
        primary = grp.files[0]
        try:
            prim_st = primary.stat()
            prim_ino_key = (prim_st.st_dev, prim_st.st_ino)
        except OSError:
            continue

        for dup in grp.files[1:]:
            try:
                dup_st = dup.stat()
                if (dup_st.st_dev, dup_st.st_ino) == prim_ino_key:
                    continue
            except OSError:
                continue

            processed_count += 1
            freed_bytes += grp.file_size

            if not dry_run and total_copies > 10 and processed_count % 5 == 0:
                render_progress(processed_count, total_copies, prefix="正在去重处理")

            if dry_run:
                continue

            if action == 'hardlink':
                try:
                    # 使用临时硬链接原子替换，确保过程安全
                    tmp_link = dup.with_name(f".tmp_link_{os.getpid()}_{dup.name}")
                    os.link(primary, tmp_link)
                    os.replace(tmp_link, dup)
                except Exception:
                    continue
            elif action == 'trash':
                move_to_trash(dup)

    if not dry_run and total_copies > 10:
        render_progress(total_copies, total_copies, prefix="正在去重处理")

    return processed_count, freed_bytes


def cmd_dedup(args: argparse.Namespace) -> None:
    """执行重复文件查重与硬链接/废纸篓去重."""
    custom_path = getattr(args, 'path', None)
    accounts = discover_accounts(custom_path)
    if not accounts:
        print('[-] 未发现可操作的微信账号目录。')
        return

    acc = accounts[0]
    categories = scan_account(acc)
    types = [t.strip() for t in args.types.split(',') if t.strip()]
    min_size_bytes = parse_size_str(args.min_size)

    action_desc = '转为 APFS 硬链接 (零风险: 微信各群仍能正常打开，但只占 1 份物理磁盘)' if args.action == 'hardlink' else '将多余副本移入系统废纸篓'

    print('=' * 66)
    print('       WeChat Slim - 多群重复文件智能查重与去重 (Phase 2)')
    print('=' * 66)
    print(f'  • 目标账号   : {acc.account_id} ({acc.version_type})')
    print(f'  • 查重范围   : {", ".join(types)}')
    print(f'  • 大小阈值   : 仅检查超过 {args.min_size} 的文件')
    print(f'  • 处理方式   : {action_desc}')
    if args.dry_run:
        print('  • 演练模式   : [Dry-Run - 仅分析展示，不实际修改磁盘]')
    print('-' * 66)
    print('正在计算文件特征哈希指纹，请稍候...')

    groups = find_duplicates(categories, types, min_size_bytes=min_size_bytes)
    actionable_groups = [g for g in groups if g.wasted_count > 0]
    total_wasted_copies = sum(g.wasted_count for g in actionable_groups)
    total_saving_bytes = sum(g.saving_bytes for g in actionable_groups)

    print(f'\n[✓] 查重完成: 发现 {len(actionable_groups)} 组重复文件，包含 {total_wasted_copies:,} 个多余副本')
    print(f'    可释放物理空间: {format_bytes(total_saving_bytes)}')

    if not actionable_groups:
        print('\n[✓] 太棒了！当前范围内未发现占用多份空间的重复文件。')
        return

    # 展示前 5 组最占空间的重复文件
    print('\n[Top 重复文件样本]:')
    for idx, grp in enumerate(actionable_groups[:5], 1):
        print(f"  {idx}. [{format_bytes(grp.file_size)}/个] 共有 {len(grp.files)} 个副本 (可释放 {format_bytes(grp.saving_bytes)}):")
        print(f"     底稿保留: {grp.files[0].name}")
        for dup in grp.files[1:3]:
            print(f"     重复副本: {dup.parent.name}/{dup.name}")
        if len(grp.files) > 3:
            print(f"     ... 等共 {len(grp.files)} 个文件")

    if args.dry_run:
        print('\n[演练完成] 如需实际执行去重，请去掉 --dry-run 参数。')
        return

    if not args.force:
        confirm = input(f'\n确认要对这 {total_wasted_copies:,} 个副本执行 [{args.action}] 去重吗? [y/N]: ').strip().lower()
        if confirm != 'y':
            print('[x] 操作已取消。')
            return

    print('\n正在执行去重处理...')
    done_count, done_bytes = execute_dedup(actionable_groups, action=args.action, dry_run=False)
    state_mgr = StateManager(getattr(args, 'state_path', None))
    state_mgr.record_dedup(done_count, done_bytes, action=args.action)
    _audit_logger.info(
        f"cmd_dedup completed: action={args.action}, processed={done_count}, freed_bytes={done_bytes}"
    )
    print(f'{Colors.GREEN}[✓]{Colors.RESET} 去重成功！已处理 {done_count:,} 个重复副本，成功释放 {Colors.BOLD}{Colors.GREEN}{format_bytes(done_bytes)}{Colors.RESET} 物理磁盘空间！')
    if args.action == 'hardlink':
        print('    提示: 已转换为 APFS 硬链接，微信中所有聊天窗口里的文件依然可原样点击打开！')
    prompt_nps_if_needed(state_mgr)


def cmd_tag(args: argparse.Namespace) -> None:
    """核心人脉与重要会话防删白名单管理."""
    wl_mgr = WhiteListManager(getattr(args, 'whitelist_config', None))

    if getattr(args, 'add', None):
        name = args.add
        wxid = getattr(args, 'wxid', None)
        if not wxid:
            print("[-] 添加失败: 请提供 --wxid 参数 (例如: --add \"老婆\" --wxid wxid_xxx)")
            return
        protect = getattr(args, 'protect', 'absolute') or 'absolute'
        keywords = [k.strip() for k in args.keywords.split(',')] if getattr(args, 'keywords', None) else []
        retain_days = getattr(args, 'retain_days', 0) or 0
        rule = wl_mgr.add(name, wxid, protect=protect, keywords=keywords, retain_days=retain_days)
        _audit_logger.info(f"cmd_tag: added rule '{rule.name}' (wxid: {rule.wxid})")
        print(f"{Colors.GREEN}[✓]{Colors.RESET} 成功添加白名单保护规则: {rule.name} (ID: {rule.wxid})")
        print(f"    保护级别: {'绝对保护 (永不删除)' if rule.protect == 'absolute' else f'保留 {rule.retain_days} 天内文件'}")
        if rule.keywords:
            print(f"    包含关键词: {', '.join(rule.keywords)}")
        return

    if getattr(args, 'remove', None):
        ok = wl_mgr.remove(args.remove)
        if ok:
            _audit_logger.info(f"cmd_tag: removed rule '{args.remove}'")
            print(f"{Colors.GREEN}[✓]{Colors.RESET} 成功移除白名单保护规则: {args.remove}")
        else:
            print(f"[-] 未找到匹配的白名单规则: {args.remove}")
        return

    if getattr(args, 'clear', False):
        wl_mgr.clear()
        _audit_logger.info("cmd_tag: cleared all whitelist rules")
        print(f"{Colors.GREEN}[✓]{Colors.RESET} 已清空白名单所有保护规则。")
        return

    # 默认展示所有规则列表
    rules = wl_mgr.list_rules()
    print("=" * 66)
    print(f"{Colors.BOLD}{Colors.MAGENTA}       WeChat Slim - 核心人脉与重要会话防删白名单{Colors.RESET}")
    print("=" * 66)
    if not rules:
        print("  当前暂无白名单规则。")
        print("  提示: 使用以下命令添加核心保护人脉，防止重要文件被误删:")
        print("    python3 wechat_slim.py tag --add \"老婆\" --wxid wxid_xxx --protect absolute")
        print("    python3 wechat_slim.py tag --add \"重要客户\" --wxid xxx@chatroom --keywords \"合同,签约\"")
        print("=" * 66)
        return

    print(f"  当前共生效 {len(rules)} 条白名单保护规则:\n")
    for idx, r in enumerate(rules, 1):
        prot_str = "🔒 绝对保护 (永不删除)" if r.protect == "absolute" else f"⏱️ 保留 {r.retain_days} 天"
        kw_str = f" | 关键词: {', '.join(r.keywords)}" if r.keywords else ""
        print(f"  {idx}. [{r.name}]")
        print(f"     微信ID/群ID: {r.wxid}")
        print(f"     保护级别   : {prot_str}{kw_str}")
        print(f"     创建时间   : {r.created_at[:19].replace('T', ' ')}")
    print("=" * 66)


def cmd_scan(args: argparse.Namespace) -> None:
    """执行扫描并展示存储透视概览 (包含白名单防删统计)."""
    custom_path = getattr(args, 'path', None)
    accounts = discover_accounts(custom_path)
    if not accounts:
        print('[-] 未在指定或默认微信容器中发现微信数据目录。')
        print('    提示: 请确认微信是否安装，或是否有登录过的账号。')
        return

    wl_mgr = WhiteListManager(getattr(args, 'whitelist_config', None))
    active_rules = wl_mgr.list_rules()

    state_mgr = StateManager(getattr(args, 'state_path', None))
    state_mgr.record_scan()
    _audit_logger.info(f"cmd_scan completed: scanned {len(accounts)} accounts")

    print('=' * 66)
    print(f'{Colors.BOLD}{Colors.GREEN}       WeChat Slim - 微信智能存储透视器{Colors.RESET}')
    print('=' * 66)

    for idx, acc in enumerate(accounts, 1):
        print(f"\n[账号 {idx}] ID: {acc.account_id} | 版本: {acc.version_type}")
        print(f"路径: {acc.root_path}")
        print("-" * 66)

        categories = scan_account(acc)
        total_account_size = sum(c.total_bytes for c in categories.values())
        cleanable_size = sum(c.total_bytes for k, c in categories.items() if not c.is_protected)

        for key, cat in categories.items():
            status_tag = '[🔒 数据库绝对保护]' if cat.is_protected else '[可瘦身]'
            percent = (cat.total_bytes / total_account_size * 100) if total_account_size > 0 else 0
            size_str = format_bytes(cat.total_bytes).rjust(10)
            count_str = f'({cat.file_count:,} 个文件)'.rjust(16)
            print(f'  • {cat.name.ljust(12)} : {size_str}  {count_str}  {percent:5.1f}%  {status_tag}')

        print('-' * 66)
        clean_ratio = (cleanable_size / total_account_size * 100) if total_account_size > 0 else 0
        print(f'  总空间占用   : {format_bytes(total_account_size)}')
        print(f'  可瘦身潜力   : {format_bytes(cleanable_size)} ({clean_ratio:.1f}% 的空间可被安全瘦身/转存)')

        # 白名单保护统计
        if active_rules:
            wl_count = 0
            wl_bytes = 0
            for c in categories.values():
                if c.is_protected:
                    continue
                for fp, sz, mt in c.files:
                    is_p, _ = wl_mgr.is_protected(fp, mt)
                    if is_p:
                        wl_count += 1
                        wl_bytes += sz
            names = ", ".join(r.name for r in active_rules[:3])
            if len(active_rules) > 3:
                names += f" 等 {len(active_rules)} 条"
            print('-' * 66)
            print(f"  🛡️ 核心人脉白名单保护:")
            print(f"  • 活跃白名单规则 : {len(active_rules)} 条 ({names})")
            print(f"  • 已锁定保护文件 : {wl_count:,} 个文件 ({format_bytes(wl_bytes)} 空间受白名单绝对保护，绝不误删)")
    print()


def cmd_clean(args: argparse.Namespace) -> None:
    """执行瘦身清理或外置归档."""
    custom_path = getattr(args, 'path', None)
    accounts = discover_accounts(custom_path)
    if not accounts:
        print('[-] 未发现可操作的微信账号目录。')
        return

    acc = accounts[0]
    categories = scan_account(acc)
    types = [t.strip() for t in args.types.split(',') if t.strip()]
    min_size_bytes = parse_size_str(args.min_size)
    archive_dir = Path(args.archive_to) if args.archive_to else None
    wl_mgr = WhiteListManager(getattr(args, 'whitelist_config', None))

    action_name = f'无损转存归档至 [{archive_dir}]' if archive_dir else '安全移入系统废纸篓 (Trash)'

    print('=' * 66)
    print('  WeChat Slim - 执行配置')
    print('=' * 66)
    print(f'  • 目标账号   : {acc.account_id} ({acc.version_type})')
    print(f'  • 清理类型   : {", ".join(types)}')
    print(f'  • 时间过滤   : 清理 {args.days} 天前的文件' if args.days > 0 else '  • 时间过滤   : 不限时间')
    print(f'  • 大小过滤   : 仅处理超过 {args.min_size} 的文件' if min_size_bytes > 0 else '  • 大小过滤   : 不限文件大小')
    print(f'  • 执行动作   : {action_name}')
    if args.dry_run:
        print('  • 模拟运行   : [演练模式 Dry-Run - 不实际移动或删除任何文件]')
    print('-' * 66)

    pre_res = execute_slimming(
        acc, categories, args.days, min_size_bytes, types, dry_run=True, archive_to=archive_dir, whitelist_mgr=wl_mgr
    )

    print(f'  预估影响     : 共计 {pre_res.freed_count:,} 个文件，可释放 {format_bytes(pre_res.freed_bytes)} 空间')
    if pre_res.protected_count > 0:
        print(f'  🛡️ 白名单保护: 已自动跳过并锁定保护 {pre_res.protected_count:,} 个核心联系人文件 ({format_bytes(pre_res.protected_bytes)} 空间)')

    if pre_res.freed_count == 0:
        print('\n[✓] 没有符合当前过滤条件的文件，无需清理。')
        return

    if not args.force and not args.dry_run:
        confirm = input(f'\n确认要对这 {pre_res.freed_count:,} 个文件执行 {action_name} 吗? [y/N]: ').strip().lower()
        if confirm != 'y':
            print('[x] 操作已取消。')
            return

    if not args.dry_run:
        print('\n正在处理中，请稍候...')
        act_res = execute_slimming(
            acc, categories, args.days, min_size_bytes, types, dry_run=False, archive_to=archive_dir, whitelist_mgr=wl_mgr
        )
        state_mgr = StateManager(getattr(args, 'state_path', None))
        state_mgr.record_clean(
            freed_count=act_res.freed_count,
            freed_bytes=act_res.freed_bytes,
            protected_count=act_res.protected_count,
            protected_bytes=act_res.protected_bytes,
            is_archive=bool(archive_dir),
        )
        _audit_logger.info(
            f"cmd_clean completed: freed_count={act_res.freed_count}, freed_bytes={act_res.freed_bytes}, "
            f"protected_count={act_res.protected_count}, protected_bytes={act_res.protected_bytes}, "
            f"is_archive={bool(archive_dir)}"
        )
        print(f'{Colors.GREEN}[✓]{Colors.RESET} 处理完成！成功释放 {Colors.BOLD}{Colors.GREEN}{format_bytes(act_res.freed_bytes)}{Colors.RESET} 空间（处理了 {act_res.freed_count:,} 个文件）。')
        if act_res.protected_count > 0:
            print(f'    🛡️ 白名单防删: 严格保护了 {act_res.protected_count:,} 个核心联系人文件未被触碰。')
        if not archive_dir:
            print('    提示: 文件已被安全放入废纸篓。如需彻底释放磁盘空间，请清空废纸篓。')
        else:
            print(f'    提示: 所有文件已完整保存至外置目录: {archive_dir}')
        prompt_nps_if_needed(state_mgr)
    else:
        print('\n[演练完成] 实际执行时请去掉 --dry-run 参数。')


def prompt_nps_if_needed(state_mgr: StateManager) -> None:
    """如果满足 NPS 触发条件且终端处于交互状态，向用户展示满意度打分调查."""
    if not state_mgr.should_trigger_nps():
        return
    if not sys.stdin.isatty():
        return

    print("\n" + "=" * 66)
    print(f"{Colors.BOLD}{Colors.YELLOW}🌟 感谢您使用 WeChat Slim 微信智能存储管理工具！{Colors.RESET}")
    print(f"您已累计释放了 {Colors.GREEN}{format_bytes(state_mgr.total_freed_bytes)}{Colors.RESET} 物理磁盘空间。")
    print("为了帮助我们持续改进，您愿意向身边的朋友推荐 WeChat Slim 吗？")
    print("打分范围: 0分 (绝不推荐) ～ 10分 (非常推荐)")
    print("=" * 66)
    try:
        ans = input("请输入您的评分 [0-10, 直接回车跳过]: ").strip()
        if ans.isdigit():
            score = int(ans)
            if 0 <= score <= 10:
                state_mgr.record_nps(score)
                _audit_logger.info(f"NPS 调查打分记录: {score} 分")
                print(f"{Colors.GREEN}[✓] 感谢您的珍贵反馈 ({score} 分)！我们将持续为您优化体验。{Colors.RESET}")
                return
        state_mgr.mark_nps_prompted()
        print("[✓] 已跳过评分，感谢支持！")
    except (KeyboardInterrupt, EOFError):
        state_mgr.mark_nps_prompted()
        print()


def cmd_stats(args: argparse.Namespace) -> None:
    """查看历史累计瘦身统计与操作记录."""
    state_path = getattr(args, 'state_path', None)
    state_mgr = StateManager(state_path)

    print("=" * 66)
    print(f"{Colors.BOLD}{Colors.BLUE}       WeChat Slim - 历史累计瘦身统计与审计大盘{Colors.RESET}")
    print("=" * 66)
    print(f"  • 状态存储路径 : {state_mgr.state_path}")
    log_dir = Path.home() / ".wechat_slim"
    audit_log = log_dir / "audit.log"
    print(f"  • 审计日志路径 : {audit_log}")
    print("-" * 66)
    print(f"  • 累计运行次数 : {Colors.BOLD}{state_mgr.total_runs}{Colors.RESET} 次")
    print(f"  • 累计空间扫描 : {state_mgr.total_scans} 次")
    print(f"  • 累计瘦身清理 : {state_mgr.total_cleans} 次")
    print(f"  • 累计查重去重 : {state_mgr.total_dedups} 次")
    print(f"  • 累计释放空间 : {Colors.BOLD}{Colors.GREEN}{format_bytes(state_mgr.total_freed_bytes)}{Colors.RESET}")
    print(f"  • 累计保护文件 : {Colors.CYAN}{format_bytes(state_mgr.total_protected_bytes)}{Colors.RESET} (白名单核心防删)")
    nps_str = f"{state_mgr.nps_score} / 10 分" if state_mgr.nps_score is not None else "尚未打分 (使用 10 次后自动开启反馈)"
    print(f"  • NPS 满意度   : {Colors.YELLOW}{nps_str}{Colors.RESET}")
    print("-" * 66)

    if not state_mgr.history:
        print("  当前尚无详细历史操作记录。")
    else:
        print("  [最近 5 次操作记录]:")
        recent = state_mgr.history[-5:]
        for idx, rec in enumerate(reversed(recent), 1):
            ts = rec.timestamp[:19].replace("T", " ")
            action_map = {
                "clean": "清理瘦身",
                "archive": "外置归档",
                "dedup_hardlink": "APFS硬链接去重",
                "dedup_trash": "废纸篓去重",
                "scan": "存储扫描",
            }
            act_name = action_map.get(rec.action, rec.action)
            print(f"  {idx}. [{ts}] {act_name}")
            print(f"     影响文件: {rec.count:,} 个 | 释放空间: {format_bytes(rec.freed_bytes)}")
            if rec.protected_bytes > 0:
                print(f"     白名单保护: {format_bytes(rec.protected_bytes)}")
            if rec.note:
                print(f"     备注: {rec.note}")
    print("=" * 66)

WEB_UI_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WeChat Slim - 微信智能存储透视与安全瘦身大盘</title>
    <style>
        :root {
            --bg: #f5f6f8;
            --card-bg: #ffffff;
            --text-main: #1d1d1f;
            --text-sub: #86868b;
            --border: #e5e5ea;
            --primary: #0071e3;
            --primary-hover: #0077ed;
            --success: #34c759;
            --warning: #ff9500;
            --danger: #ff3b30;
            --radius: 12px;
            --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: var(--font); background: var(--bg); color: var(--text-main); line-height: 1.5; padding: 24px 16px; }
        .container { max-width: 980px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
        .logo { font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
        .badge { background: #e8f2ff; color: var(--primary); padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
        .grid-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: var(--card-bg); border-radius: var(--radius); border: 1px solid var(--border); padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.03); }
        .stat-label { font-size: 13px; color: var(--text-sub); font-weight: 500; margin-bottom: 6px; }
        .stat-val { font-size: 26px; font-weight: 700; letter-spacing: -0.5px; }
        .stat-desc { font-size: 12px; color: var(--text-sub); margin-top: 4px; }
        .progress-bar-container { background: #e5e5ea; border-radius: 8px; height: 14px; overflow: hidden; display: flex; margin: 16px 0 8px 0; }
        .progress-seg { height: 100%; transition: width 0.3s; }
        .bg-attach { background: #0071e3; }
        .bg-video { background: #5856d6; }
        .bg-file { background: #34c759; }
        .bg-cache { background: #ff9500; }
        .bg-db { background: #8e8e93; }
        .legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: var(--text-sub); margin-bottom: 24px; }
        .legend-item { display: flex; align-items: center; gap: 6px; }
        .legend-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
        .tabs { display: flex; gap: 8px; border-bottom: 1px solid var(--border); margin-bottom: 20px; }
        .tab-btn { background: none; border: none; padding: 10px 16px; font-size: 14px; font-weight: 600; color: var(--text-sub); cursor: pointer; border-bottom: 2px solid transparent; }
        .tab-btn.active { color: var(--primary); border-bottom-color: var(--primary); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .form-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 16px; }
        .form-group label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
        .form-group input, .form-group select { width: 100%; padding: 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
        .checkbox-group { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin: 12px 0; font-size: 13px; }
        .btn-group { display: flex; gap: 12px; margin-top: 18px; }
        .btn { padding: 10px 20px; border-radius: 8px; border: none; font-size: 14px; font-weight: 600; cursor: pointer; transition: background 0.2s; }
        .btn-primary { background: var(--primary); color: white; }
        .btn-primary:hover { background: var(--primary-hover); }
        .btn-secondary { background: #e5e5ea; color: var(--text-main); }
        .btn-secondary:hover { background: #d1d1d6; }
        .btn-success { background: var(--success); color: white; }
        .console { background: #1c1c1e; color: #30d158; padding: 16px; border-radius: 8px; font-family: ui-monospace, Menlo, monospace; font-size: 12px; max-height: 200px; overflow-y: auto; white-space: pre-wrap; margin-top: 20px; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
        th, td { text-align: left; padding: 10px; border-bottom: 1px solid var(--border); }
        th { color: var(--text-sub); font-weight: 600; }
    </style>
</head>
<body>
<div class="container">
    <header>
        <div class="logo">🧹 WeChat Slim <span class="badge" id="accountBadge">正在连接...</span></div>
        <div style="font-size: 13px; color: var(--text-sub);" id="accountPath"></div>
    </header>

    <div class="grid-stats">
        <div class="card">
            <div class="stat-label">微信总占用空间</div>
            <div class="stat-val" id="totalSize">--</div>
            <div class="stat-desc" id="totalFiles">正在扫描数据...</div>
        </div>
        <div class="card">
            <div class="stat-label">可安全释放潜力</div>
            <div class="stat-val" style="color: var(--success);" id="cleanableSize">--</div>
            <div class="stat-desc" id="cleanableRatio">大文件与缓存可瘦身</div>
        </div>
        <div class="card">
            <div class="stat-label">核心数据库与文字消息</div>
            <div class="stat-val" style="color: var(--text-sub);" id="dbSize">--</div>
            <div class="stat-desc">🔒 100% 绝对保护，绝不误删</div>
        </div>
    </div>

    <div class="card" style="margin-bottom: 24px;">
        <div style="font-size: 14px; font-weight: 600; margin-bottom: 8px;">存储空间结构分布</div>
        <div class="progress-bar-container" id="progressBar"></div>
        <div class="legend" id="legend"></div>
    </div>

    <div class="card">
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('slim')">🚀 智能安全瘦身</button>
            <button class="tab-btn" onclick="switchTab('dedup')">🔗 多群查重 (APFS硬链接)</button>
            <button class="tab-btn" onclick="switchTab('whitelist')">🛡️ 核心人脉防删白名单</button>
            <button class="tab-btn" onclick="switchTab('history')">📊 历史累计与审计</button>
            <button class="tab-btn" onclick="switchTab('details')">📋 存储明细</button>
        </div>

        <!-- 瘦身 Tab -->
        <div id="tab-slim" class="tab-content active">
            <div class="form-row">
                <div class="form-group">
                    <label>时间范围</label>
                    <select id="slimDays">
                        <option value="90" selected>清理 90 天前的文件 (推荐)</option>
                        <option value="30">清理 30 天前的文件 (深度)</option>
                        <option value="180">清理 180 天前的文件 (保守)</option>
                        <option value="0">不限时间 (全量清理)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>单文件大小阈值</label>
                    <select id="slimMinSize">
                        <option value="10MB" selected>大于 10MB 的大文件 (推荐)</option>
                        <option value="20MB">大于 20MB 的超大文件</option>
                        <option value="50MB">大于 50MB 的特大视频/文档</option>
                        <option value="0B">不限大小 (清理所有选定类型)</option>
                    </select>
                </div>
            </div>
            <div class="form-group">
                <label>清理类型</label>
                <div class="checkbox-group">
                    <label><input type="checkbox" id="typeVideo" checked> 聊天视频 (msg/video)</label>
                    <label><input type="checkbox" id="typeFile" checked> 接收的文档 (msg/file)</label>
                    <label><input type="checkbox" id="typeAttach"> 聊天多媒体图片 (msg/attach)</label>
                    <label><input type="checkbox" id="typeCache" checked> 临时运行缓存 (cache/temp)</label>
                </div>
            </div>
            <div class="form-group" style="margin-top: 12px;">
                <label>外置硬盘归档目录 (可选，留空则默认移入废纸篓)</label>
                <input type="text" id="archivePath" placeholder="例如: /Volumes/MySSD/WeChatBackup (自动建立对应文件夹保持结构)">
            </div>
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="executeSlim(true)">🔍 模拟演练 (Dry-Run)</button>
                <button class="btn btn-primary" onclick="executeSlim(false)">⚡ 开始执行安全瘦身</button>
            </div>
        </div>

        <!-- 查重 Tab -->
        <div id="tab-dedup" class="tab-content">
            <p style="font-size: 13px; color: var(--text-sub); margin-bottom: 16px;">
                在多群中被多次转发的相同大文件，将通过 APFS 硬链接秒级去重：所有聊天窗口里依然可正常打开文件，但在物理 SSD 磁盘上只占 1 份空间！
            </p>
            <div class="form-row">
                <div class="form-group">
                    <label>查重文件大小门槛</label>
                    <select id="dedupMinSize">
                        <option value="500KB" selected>大于 500KB (推荐)</option>
                        <option value="1MB">大于 1MB</option>
                        <option value="5MB">大于 5MB (仅查大视频/文档)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>去重动作</label>
                    <select id="dedupAction">
                        <option value="hardlink" selected>替换为 APFS 硬链接 (零风险，强烈推荐)</option>
                        <option value="trash">移入 macOS 废纸篓</option>
                    </select>
                </div>
            </div>
            <div class="btn-group">
                <button class="btn btn-secondary" onclick="scanDedup()">🔍 扫描重复文件</button>
                <button class="btn btn-success" onclick="executeDedup()">🔗 执行硬链接秒级去重</button>
            </div>
            <div id="dedupResults" style="margin-top: 16px;"></div>
        </div>

        <!-- 白名单 Tab -->
        <div id="tab-whitelist" class="tab-content">
            <p style="font-size: 13px; color: var(--text-sub); margin-bottom: 16px;">
                加入白名单的核心人脉（家人、老板、重要客户）与其聊天中的文件、视频在任何清理动作中都将受到<b>绝对隔离保护</b>，系统会自动识别并跳过，绝不误删。
            </p>
            <div class="form-row">
                <div class="form-group">
                    <label>人脉/群备注名</label>
                    <input type="text" id="wlName" placeholder="例如: 老婆、公司财务群、核心客户A">
                </div>
                <div class="form-group">
                    <label>微信ID / 群ID (wxid)</label>
                    <input type="text" id="wlWxid" placeholder="例如: wxid_xxx 或 xxx@chatroom">
                </div>
                <div class="form-group">
                    <label>保护级别</label>
                    <select id="wlProtect">
                        <option value="absolute" selected>绝对保护 (永不删除)</option>
                        <option value="retain_days">保留指定天数内文件</option>
                    </select>
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>保护文件名关键词 (可选，逗号分隔)</label>
                    <input type="text" id="wlKeywords" placeholder="例如: 合同,发票,签约,宝宝照片">
                </div>
                <div class="form-group">
                    <label>保留天数 (配合保留天数选项)</label>
                    <input type="number" id="wlRetainDays" value="365" placeholder="默认: 365 天">
                </div>
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="addWhitelistRule()">➕ 添加防删白名单保护</button>
                <button class="btn btn-secondary" onclick="loadWhitelist()">🔄 刷新列表</button>
            </div>

            <div style="margin-top: 20px;">
                <div style="font-size: 14px; font-weight: 600; margin-bottom: 8px;">已生效的防删白名单规则</div>
                <table>
                    <thead>
                        <tr><th>保护对象</th><th>微信ID / 群ID</th><th>保护级别</th><th>指定关键词</th><th>创建时间</th><th>操作</th></tr>
                    </thead>
                    <tbody id="whitelistBody"></tbody>
                </table>
            </div>
        </div>

        <!-- 历史与审计 Tab -->
        <div id="tab-history" class="tab-content">
            <p style="font-size: 13px; color: var(--text-sub); margin-bottom: 16px;">
                系统全生命周期运行指标与本地审计跟踪。每次清理、查重与外置归档均受严密记录。
            </p>
            <div class="grid-stats" style="margin-bottom: 16px;">
                <div class="card" style="padding: 14px;">
                    <div class="stat-label">累计运行次数</div>
                    <div class="stat-val" id="histRuns">--</div>
                    <div class="stat-desc" id="histScansCleans">--</div>
                </div>
                <div class="card" style="padding: 14px;">
                    <div class="stat-label">累计释放空间</div>
                    <div class="stat-val" style="color: var(--success);" id="histFreed">--</div>
                    <div class="stat-desc">SSD 磁盘真实释放</div>
                </div>
                <div class="card" style="padding: 14px;">
                    <div class="stat-label">白名单锁定保护</div>
                    <div class="stat-val" style="color: var(--primary);" id="histProtected">--</div>
                    <div class="stat-desc">严格守护跳过的文件空间</div>
                </div>
                <div class="card" style="padding: 14px;">
                    <div class="stat-label">NPS 推荐度评分</div>
                    <div class="stat-val" style="color: var(--warning);" id="histNps">--</div>
                    <div class="stat-desc">用户满意度</div>
                </div>
            </div>

            <div style="font-size: 14px; font-weight: 600; margin-bottom: 8px;">最近操作历史明细</div>
            <table>
                <thead>
                    <tr><th>时间</th><th>操作类型</th><th>影响文件数</th><th>释放空间</th><th>保护空间</th><th>备注</th></tr>
                </thead>
                <tbody id="historyBody"></tbody>
            </table>

            <div style="margin-top: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <div style="font-size: 14px; font-weight: 600;">本地安全审计日志 (最近 30 条)</div>
                    <div style="font-size: 12px; color: var(--text-sub);" id="auditLogPath"></div>
                </div>
                <div class="console" id="auditLogConsole" style="max-height: 180px;">正在加载审计日志...</div>
            </div>
        </div>

        <!-- 明细 Tab -->
        <div id="tab-details" class="tab-content">
            <table>
                <thead>
                    <tr><th>目录类别</th><th>占用大小</th><th>文件数量</th><th>占比</th><th>安全状态</th></tr>
                </thead>
                <tbody id="detailsBody"></tbody>
            </table>
        </div>

        <div class="console" id="logConsole">> WeChat Slim 就绪。等待指令...</div>
    </div>
</div>

<script>
    let globalData = null;
    function log(msg) {
        const c = document.getElementById('logConsole');
        c.innerText += '\\n' + msg;
        c.scrollTop = c.scrollHeight;
    }

    function switchTab(name) {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        event.target.classList.add('active');
        document.getElementById('tab-' + name).classList.add('active');
        if (name === 'whitelist') loadWhitelist();
        if (name === 'history') loadHistory();
    }

    async function loadStats() {
        log('正在扫描本地微信存储...');
        const res = await fetch('/api/stats');
        globalData = await res.json();
        renderStats();
        loadWhitelist();
        loadHistory();
    }

    function renderStats() {
        if (!globalData || !globalData.account) return;
        document.getElementById('accountBadge').innerText = globalData.account.id + ' (' + globalData.account.version + ')';
        document.getElementById('accountPath').innerText = globalData.account.root_path;
        document.getElementById('totalSize').innerText = globalData.total_size_str;
        document.getElementById('totalFiles').innerText = globalData.total_files + ' 个文件';
        document.getElementById('cleanableSize').innerText = globalData.cleanable_size_str;
        document.getElementById('cleanableRatio').innerText = globalData.cleanable_ratio + '% 空间可被安全瘦身';
        document.getElementById('dbSize').innerText = globalData.categories.db.size_str;

        // Progress Bar
        const bar = document.getElementById('progressBar');
        const legend = document.getElementById('legend');
        bar.innerHTML = '';
        legend.innerHTML = '';

        const colors = { attach: 'bg-attach', video: 'bg-video', file: 'bg-file', cache: 'bg-cache', db: 'bg-db' };
        const hex = { attach: '#0071e3', video: '#5856d6', file: '#34c759', cache: '#ff9500', db: '#8e8e93' };

        for (let k in globalData.categories) {
            const cat = globalData.categories[k];
            const seg = document.createElement('div');
            seg.className = 'progress-seg ' + (colors[k] || 'bg-db');
            seg.style.width = cat.percent + '%';
            seg.title = cat.name + ': ' + cat.size_str;
            bar.appendChild(seg);

            legend.innerHTML += `<div class="legend-item"><span class="legend-dot" style="background:${hex[k] || '#8e8e93'}"></span>${cat.name} (${cat.size_str}, ${cat.percent}%)</div>`;
        }

        // Details Table
        const tbody = document.getElementById('detailsBody');
        tbody.innerHTML = '';
        for (let k in globalData.categories) {
            const cat = globalData.categories[k];
            const badge = cat.is_protected ? '<span style="color:#8e8e93; font-weight:600;">🔒 绝对保护</span>' : '<span style="color:#34c759; font-weight:600;">✓ 可瘦身</span>';
            tbody.innerHTML += `<tr><td><b>${cat.name}</b><br><small style="color:#86868b">${cat.description}</small></td><td>${cat.size_str}</td><td>${cat.file_count}</td><td>${cat.percent}%</td><td>${badge}</td></tr>`;
        }
        log('扫描完成: 微信总占用 ' + globalData.total_size_str + '，可瘦身潜力 ' + globalData.cleanable_size_str);
    }

    async function executeSlim(isDryRun) {
        const types = [];
        if (document.getElementById('typeVideo').checked) types.push('video');
        if (document.getElementById('typeFile').checked) types.push('file');
        if (document.getElementById('typeAttach').checked) types.push('attach');
        if (document.getElementById('typeCache').checked) types.push('cache');

        const payload = {
            days: parseInt(document.getElementById('slimDays').value),
            min_size: document.getElementById('slimMinSize').value,
            types: types.join(','),
            archive_to: document.getElementById('archivePath').value.trim() || null,
            dry_run: isDryRun
        };

        log((isDryRun ? '[演练开始]' : '[开始执行]') + ' 正在处理符合条件的文件...');
        const res = await fetch('/api/clean', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const data = await res.json();
        log(data.message);
        if (!isDryRun) {
            loadStats();
            loadHistory();
        }
    }

    async function scanDedup() {
        const minSize = document.getElementById('dedupMinSize').value;
        log('正在计算特征哈希并排查重复副本 (门槛 ' + minSize + ')...');
        const res = await fetch('/api/dedup_scan?min_size=' + minSize);
        const data = await res.json();
        const container = document.getElementById('dedupResults');
        if (data.actionable_groups_count === 0) {
            container.innerHTML = '<div style="color:var(--success); font-weight:600; margin-top:8px;">✓ 太棒了！未发现占用多份空间的重复文件。</div>';
            log('查重完成: 未发现冗余副本。');
            return;
        }
        container.innerHTML = `<div style="margin-top:12px; font-weight:600;">发现 ${data.actionable_groups_count} 组重复文件，共 ${data.total_wasted_copies} 个副本，可节省 ${data.total_saving_str} 物理空间！</div>`;
        log(`查重完成: 发现 ${data.actionable_groups_count} 组重复，可释放 ${data.total_saving_str}`);
    }

    async function executeDedup() {
        const minSize = document.getElementById('dedupMinSize').value;
        const action = document.getElementById('dedupAction').value;
        log('正在执行去重 (模式: ' + action + ')...');
        const res = await fetch('/api/dedup_exec', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ min_size: minSize, action: action })
        });
        const data = await res.json();
        log(data.message);
        loadStats();
        loadHistory();
    }

    async function loadWhitelist() {
        try {
            const res = await fetch('/api/whitelist');
            const data = await res.json();
            const tbody = document.getElementById('whitelistBody');
            tbody.innerHTML = '';
            if (!data.rules || data.rules.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-sub); padding:16px;">当前暂无白名单保护规则</td></tr>';
                return;
            }
            data.rules.forEach(r => {
                const protStr = r.protect === 'absolute' ? '<span style="color:var(--success); font-weight:600;">🔒 绝对保护</span>' : `<span style="color:var(--warning)">⏱️ 保留 ${r.retain_days} 天</span>`;
                const kwStr = r.keywords && r.keywords.length > 0 ? r.keywords.join(', ') : '-';
                const ts = (r.created_at || '').substring(0, 19).replace('T', ' ');
                tbody.innerHTML += `<tr>
                    <td><b>${r.name}</b></td>
                    <td><code>${r.wxid}</code></td>
                    <td>${protStr}</td>
                    <td>${kwStr}</td>
                    <td><small style="color:var(--text-sub)">${ts}</small></td>
                    <td><button class="btn btn-secondary" style="padding:4px 10px; font-size:12px;" onclick="removeWhitelistRule('${r.wxid}')">移除</button></td>
                </tr>`;
            });
        } catch (e) {}
    }

    async function addWhitelistRule() {
        const name = document.getElementById('wlName').value.trim();
        const wxid = document.getElementById('wlWxid').value.trim();
        if (!name || !wxid) {
            alert('请提供联系人姓名和微信号/群ID！');
            return;
        }
        const payload = {
            name: name,
            wxid: wxid,
            protect: document.getElementById('wlProtect').value,
            keywords: document.getElementById('wlKeywords').value.trim(),
            retain_days: parseInt(document.getElementById('wlRetainDays').value || '0')
        };
        const res = await fetch('/api/whitelist/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        log(data.message || '白名单已更新');
        document.getElementById('wlName').value = '';
        document.getElementById('wlWxid').value = '';
        document.getElementById('wlKeywords').value = '';
        loadWhitelist();
    }

    async function removeWhitelistRule(target) {
        if (!confirm('确定要移除规则 ' + target + ' 吗？')) return;
        const res = await fetch('/api/whitelist/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target: target })
        });
        const data = await res.json();
        log(data.message || '规则已移除');
        loadWhitelist();
    }

    async function loadHistory() {
        try {
            const res = await fetch('/api/history');
            const data = await res.json();
            document.getElementById('histRuns').innerText = data.total_runs + ' 次';
            document.getElementById('histScansCleans').innerText = `扫描 ${data.total_scans} 次 / 清理 ${data.total_cleans} 次 / 去重 ${data.total_dedups} 次`;
            document.getElementById('histFreed').innerText = data.total_freed_str;
            document.getElementById('histProtected').innerText = data.total_protected_str;
            document.getElementById('histNps').innerText = data.nps_score !== null ? data.nps_score + ' / 10 分' : '尚未评分';
            document.getElementById('auditLogPath').innerText = data.audit_log_path || '';

            const tbody = document.getElementById('historyBody');
            tbody.innerHTML = '';
            const actMap = {
                'clean': '清理瘦身',
                'archive': '外置归档',
                'dedup_hardlink': 'APFS硬链接去重',
                'dedup_trash': '废纸篓去重',
                'scan': '空间扫描'
            };
            if (!data.history || data.history.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-sub); padding:16px;">尚无历史操作记录</td></tr>';
            } else {
                data.history.forEach(h => {
                    const ts = (h.timestamp || '').substring(0, 19).replace('T', ' ');
                    const actName = actMap[h.action] || h.action;
                    const freedStr = h.freed_bytes ? (h.freed_bytes / 1024 / 1024).toFixed(1) + ' MB' : '0 B';
                    const protStr = h.protected_bytes ? (h.protected_bytes / 1024 / 1024).toFixed(1) + ' MB' : '-';
                    tbody.innerHTML += `<tr>
                        <td><small style="color:var(--text-sub)">${ts}</small></td>
                        <td><b>${actName}</b></td>
                        <td>${h.count || 0}</td>
                        <td style="color:var(--success); font-weight:600;">${freedStr}</td>
                        <td style="color:var(--primary);">${protStr}</td>
                        <td><small style="color:var(--text-sub)">${h.note || ''}</small></td>
                    </tr>`;
                });
            }

            const alc = document.getElementById('auditLogConsole');
            if (data.recent_logs && data.recent_logs.length > 0) {
                alc.innerText = data.recent_logs.join('\\n');
            } else {
                alc.innerText = '> 审计日志文件尚为空或尚未生成操作。';
            }
            alc.scrollTop = alc.scrollHeight;
        } catch (e) {}
    }

    window.onload = loadStats;
</script>
</body>
</html>
"""


class WeChatSlimWebHandler(BaseHTTPRequestHandler):
    """本地轻量级 WebUI HTTP 请求处理器."""
    custom_path: Optional[Path] = None
    whitelist_config: Optional[Path] = None
    state_path: Optional[Path] = None

    def _send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/':
            raw = WEB_UI_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif parsed.path == '/api/stats':
            accounts = discover_accounts(self.custom_path)
            if not accounts:
                self._send_json({'error': '未找到微信账号目录'}, status=404)
                return
            acc = accounts[0]
            categories = scan_account(acc)
            total_bytes = sum(c.total_bytes for c in categories.values())
            cleanable_bytes = sum(c.total_bytes for k, c in categories.items() if not c.is_protected)
            clean_ratio = (cleanable_bytes / total_bytes * 100) if total_bytes > 0 else 0

            cat_dict = {}
            for k, c in categories.items():
                pct = (c.total_bytes / total_bytes * 100) if total_bytes > 0 else 0
                cat_dict[k] = {
                    'name': c.name,
                    'description': c.description,
                    'file_count': f'{c.file_count:,}',
                    'size_bytes': c.total_bytes,
                    'size_str': format_bytes(c.total_bytes),
                    'percent': f'{pct:.1f}',
                    'is_protected': c.is_protected,
                }

            self._send_json({
                'account': {
                    'id': acc.account_id,
                    'version': acc.version_type,
                    'root_path': str(acc.root_path),
                },
                'total_size_bytes': total_bytes,
                'total_size_str': format_bytes(total_bytes),
                'total_files': f'{sum(c.file_count for c in categories.values()):,}',
                'cleanable_size_bytes': cleanable_bytes,
                'cleanable_size_str': format_bytes(cleanable_bytes),
                'cleanable_ratio': f'{clean_ratio:.1f}',
                'categories': cat_dict,
            })
        elif parsed.path == '/api/dedup_scan':
            query = urllib.parse.parse_qs(parsed.query)
            min_size = query.get('min_size', ['500KB'])[0]
            accounts = discover_accounts(self.custom_path)
            if not accounts:
                self._send_json({'error': '未找到微信账号目录'}, status=404)
                return
            acc = accounts[0]
            categories = scan_account(acc)
            groups = find_duplicates(categories, ['video', 'file', 'attach'], min_size_bytes=parse_size_str(min_size))
            actionable = [g for g in groups if g.wasted_count > 0]
            total_saving = sum(g.saving_bytes for g in actionable)
            total_wasted = sum(g.wasted_count for g in actionable)

            self._send_json({
                'actionable_groups_count': len(actionable),
                'total_wasted_copies': total_wasted,
                'total_saving_bytes': total_saving,
                'total_saving_str': format_bytes(total_saving),
            })
        elif parsed.path == '/api/whitelist':
            wl_mgr = WhiteListManager(self.whitelist_config)
            rules = [r.to_dict() for r in wl_mgr.list_rules()]
            self._send_json({'rules': rules})
        elif parsed.path == '/api/history':
            state_mgr = StateManager(self.state_path)
            log_path = Path.home() / ".wechat_slim" / "audit.log"
            recent_logs = []
            if log_path.exists():
                try:
                    with open(log_path, 'r', encoding='utf-8') as lf:
                        recent_logs = [l.strip() for l in lf.readlines()[-30:]]
                except Exception:
                    pass
            self._send_json({
                'total_runs': state_mgr.total_runs,
                'total_scans': state_mgr.total_scans,
                'total_cleans': state_mgr.total_cleans,
                'total_dedups': state_mgr.total_dedups,
                'total_freed_bytes': state_mgr.total_freed_bytes,
                'total_freed_str': format_bytes(state_mgr.total_freed_bytes),
                'total_protected_bytes': state_mgr.total_protected_bytes,
                'total_protected_str': format_bytes(state_mgr.total_protected_bytes),
                'nps_score': state_mgr.nps_score,
                'history': [h.to_dict() for h in reversed(state_mgr.history[-20:])],
                'recent_logs': recent_logs,
                'audit_log_path': str(log_path),
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length).decode('utf-8')) if length > 0 else {}

        accounts = discover_accounts(self.custom_path)
        if not accounts:
            self._send_json({'error': '未找到微信账号目录'}, status=404)
            return
        acc = accounts[0]
        categories = scan_account(acc)

        if parsed.path == '/api/clean':
            days = int(body.get('days', 90))
            min_size = parse_size_str(body.get('min_size', '0B'))
            types = [t.strip() for t in body.get('types', 'video,file').split(',') if t.strip()]
            dry_run = bool(body.get('dry_run', True))
            archive_to = Path(body['archive_to']) if body.get('archive_to') else None
            wl_mgr = WhiteListManager(self.whitelist_config)

            res = execute_slimming(
                acc, categories, days, min_size, types, dry_run=dry_run, archive_to=archive_to, whitelist_mgr=wl_mgr
            )
            if not dry_run:
                state_mgr = StateManager(self.state_path)
                state_mgr.record_clean(
                    res.freed_count, res.freed_bytes, res.protected_count, res.protected_bytes, is_archive=bool(archive_to)
                )
                _audit_logger.info(
                    f"WebUI: executed clean freed={res.freed_count} ({res.freed_bytes} bytes), "
                    f"protected={res.protected_count} ({res.protected_bytes} bytes)"
                )
            msg = f"[演练完成] 预计影响 {res.freed_count:,} 个文件，可释放 {format_bytes(res.freed_bytes)} 空间" if dry_run else f"[处理完成] 成功处理 {res.freed_count:,} 个文件，释放 {format_bytes(res.freed_bytes)} 空间！"
            if res.protected_count > 0:
                msg += f" (已跳过锁定保护 {res.protected_count:,} 个核心人脉文件，{format_bytes(res.protected_bytes)})"
            self._send_json({
                'count': res.freed_count,
                'freed_bytes': res.freed_bytes,
                'freed_str': format_bytes(res.freed_bytes),
                'protected_count': res.protected_count,
                'protected_bytes': res.protected_bytes,
                'protected_str': format_bytes(res.protected_bytes),
                'message': msg,
            })
        elif parsed.path == '/api/dedup_exec':
            min_size = parse_size_str(body.get('min_size', '500KB'))
            action = body.get('action', 'hardlink')
            groups = find_duplicates(categories, ['video', 'file', 'attach'], min_size_bytes=min_size)
            actionable = [g for g in groups if g.wasted_count > 0]
            count, freed = execute_dedup(actionable, action=action, dry_run=False)
            state_mgr = StateManager(self.state_path)
            state_mgr.record_dedup(count, freed, action=action)
            _audit_logger.info(f"WebUI: executed dedup action={action}, processed={count}, freed={freed}")
            msg = f"[去重完成] 成功转换 {count:,} 个重复副本为 APFS 硬链接，物理释放 {format_bytes(freed)} 磁盘空间！" if action == 'hardlink' else f"[去重完成] 成功移入废纸篓 {count:,} 个重复副本，释放 {format_bytes(freed)} 空间！"
            self._send_json({'count': count, 'freed_bytes': freed, 'freed_str': format_bytes(freed), 'message': msg})
        elif parsed.path == '/api/whitelist/add':
            name = str(body.get('name', '')).strip()
            wxid = str(body.get('wxid', '')).strip()
            if not name or not wxid:
                self._send_json({'error': '名称与微信ID不能为空'}, status=400)
                return
            protect = body.get('protect', 'absolute')
            keywords = [k.strip() for k in str(body.get('keywords', '')).split(',') if k.strip()]
            retain_days = int(body.get('retain_days', 0))
            wl_mgr = WhiteListManager(self.whitelist_config)
            rule = wl_mgr.add(name, wxid, protect=protect, keywords=keywords, retain_days=retain_days)
            _audit_logger.info(f"WebUI: added whitelist rule '{rule.name}' ({rule.wxid})")
            self._send_json({'rule': rule.to_dict(), 'message': f'成功添加白名单规则: {rule.name}'})
        elif parsed.path == '/api/whitelist/remove':
            target = str(body.get('target', '')).strip()
            wl_mgr = WhiteListManager(self.whitelist_config)
            ok = wl_mgr.remove(target)
            if ok:
                _audit_logger.info(f"WebUI: removed whitelist rule '{target}'")
                self._send_json({'ok': True, 'message': f'已移除白名单规则: {target}'})
            else:
                self._send_json({'ok': False, 'message': f'未找到白名单规则: {target}'}, status=404)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """静默默认 HTTP 请求日志，避免刷屏."""
        return


def cmd_web(args: argparse.Namespace) -> None:
    """启动本地轻量 WebUI 大盘."""
    port = getattr(args, 'port', 8080)
    custom_path = getattr(args, 'path', None)
    WeChatSlimWebHandler.custom_path = custom_path
    WeChatSlimWebHandler.whitelist_config = getattr(args, 'whitelist_config', None)
    WeChatSlimWebHandler.state_path = getattr(args, 'state_path', None)

    server = HTTPServer(('127.0.0.1', port), WeChatSlimWebHandler)
    url = f"http://127.0.0.1:{port}"
    print('=' * 66)
    print('       WeChat Slim - 本地可视化图形大盘 (WebUI)')
    print('=' * 66)
    print(f'  • 网页服务已就绪: {url}')
    print('  • 按 Ctrl+C 可停止服务')
    print('-' * 66)

    if not getattr(args, 'no_browser', False):
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[✓] Web 服务已停止。')
        server.server_close()


def interactive_wizard() -> None:
    """极简交互式向导."""
    accounts = discover_accounts()
    if not accounts:
        print('[-] 未发现微信数据目录。')
        return

    acc = accounts[0]
    categories = scan_account(acc)
    total_size = sum(c.total_bytes for c in categories.values())
    cleanable_size = sum(c.total_bytes for k, c in categories.items() if not c.is_protected)

    print('=' * 66)
    print(f'{Colors.BOLD}{Colors.GREEN}       WeChat Slim - 微信智能瘦身与无损归档工具 (Mac版){Colors.RESET}')
    print('=' * 66)
    print(f'{Colors.GREEN}[✓]{Colors.RESET} 自动定位账号: {Colors.BOLD}{acc.account_id}{Colors.RESET} ({acc.version_type})')
    print(f'    总占用: {format_bytes(total_size)} | 瘦身潜力: {Colors.BOLD}{Colors.GREEN}{format_bytes(cleanable_size)}{Colors.RESET}')
    print('-' * 66)

    print('\n请选择要执行的操作:')
    print('  [1] 快速瘦身 (推荐: 清理 90 天前且 >10MB 的视频/文件，移入废纸篓)')
    print('  [2] 极限瘦身 (清理所有 30 天前的缓存、视频与下载文件)')
    print('  [3] 仅清理临时缓存 (仅清理 cache/temp，绝不触碰任何聊天文件)')
    print('  [4] 存储空间详细扫描 (查看各分类占用与白名单保护统计)')
    print('  [5] 智能查重去重 (多群重复转发秒级查重，转换为 APFS 硬链接释放空间)')
    print('  [6] 启动网页大盘 (启动本地现代化 WebUI 并在浏览器中查看)')
    print('  [7] 核心人脉白名单管理 (查看或添加家人、老板、重要客户防删名单)')
    print('  [8] 历史使用统计与审计 (查看累计释放空间与操作记录)')
    print('  [q] 退出')

    choice = input('\n请输入选项 [1-8/q]: ').strip().lower()
    if choice == '1':
        args = argparse.Namespace(
            types='video,file',
            days=90,
            min_size='10MB',
            dry_run=False,
            archive_to=None,
            force=False,
        )
        cmd_clean(args)
    elif choice == '2':
        args = argparse.Namespace(
            types='video,file,cache',
            days=30,
            min_size='0B',
            dry_run=False,
            archive_to=None,
            force=False,
        )
        cmd_clean(args)
    elif choice == '3':
        args = argparse.Namespace(
            types='cache',
            days=0,
            min_size='0B',
            dry_run=False,
            archive_to=None,
            force=False,
        )
        cmd_clean(args)
    elif choice == '4':
        cmd_scan(argparse.Namespace())
    elif choice == '5':
        args = argparse.Namespace(
            types='video,file,attach',
            min_size='500KB',
            action='hardlink',
            dry_run=False,
            force=False,
            path=None,
        )
        cmd_dedup(args)
    elif choice == '6':
        cmd_web(argparse.Namespace(port=8080, path=None, no_browser=False))
    elif choice == '7':
        cmd_tag(argparse.Namespace(add=None, remove=None, clear=False, list=True))
    elif choice == '8':
        cmd_stats(argparse.Namespace(state_path=None))
    else:
        print('已退出。')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='WeChat Slim - 微信智能存储透视与安全瘦身工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='subcommand')

    scan_p = subparsers.add_parser('scan', help='扫描并展示微信存储空间深度分布')
    scan_p.add_argument('--path', default=None, help='指定自定义微信存储目录 (默认: 自动发现系统微信目录)')
    scan_p.add_argument('--whitelist-config', default=None, help=argparse.SUPPRESS)
    scan_p.add_argument('--state-path', default=None, help=argparse.SUPPRESS)

    clean_p = subparsers.add_parser('clean', help='执行文件瘦身或外置归档')
    clean_p.add_argument('--path', default=None, help='指定自定义微信存储目录 (默认: 自动发现系统微信目录)')
    clean_p.add_argument('--days', type=int, default=90, help='清理多少天前的文件 (默认: 90 天，0 为不限时间)')
    clean_p.add_argument('--min-size', default='0B', help='文件最小大小阈值 (例如: 20MB, 10MB，默认: 0B)')
    clean_p.add_argument('--types', default='video,file,cache', help='清理文件类型，逗号分隔 (可选: video,file,attach,cache)')
    clean_p.add_argument('--dry-run', action='store_true', help='模拟预演，只统计不实际移动任何文件')
    clean_p.add_argument('--archive-to', default=None, help='指定外置移动硬盘或备份目录 (将文件安全移动至该目录，而非废纸篓)')
    clean_p.add_argument('-f', '--force', action='store_true', help='跳过确认提示直接执行')
    clean_p.add_argument('--whitelist-config', default=None, help=argparse.SUPPRESS)
    clean_p.add_argument('--state-path', default=None, help=argparse.SUPPRESS)

    dedup_p = subparsers.add_parser('dedup', help='多群转发重复文件智能查重与去重 (Phase 2)')
    dedup_p.add_argument('--path', default=None, help='指定自定义微信存储目录 (默认: 自动发现系统微信目录)')
    dedup_p.add_argument('--types', default='video,file,attach', help='查重类型，逗号分隔 (可选: video,file,attach)')
    dedup_p.add_argument('--min-size', default='500KB', help='查重最小文件大小 (例如: 1MB, 500KB，默认: 500KB)')
    dedup_p.add_argument('--action', choices=['hardlink', 'trash'], default='hardlink', help='去重动作: hardlink (转为硬链接，零风险) 或 trash (移入废纸篓)')
    dedup_p.add_argument('--dry-run', action='store_true', help='模拟预演，只分析展示不实际修改')
    dedup_p.add_argument('-f', '--force', action='store_true', help='跳过确认提示直接执行')
    dedup_p.add_argument('--state-path', default=None, help=argparse.SUPPRESS)

    web_p = subparsers.add_parser('web', help='启动本地可视化大盘 (WebUI Dashboard)')
    web_p.add_argument('--port', type=int, default=8080, help='指定本地网页端口 (默认: 8080)')
    web_p.add_argument('--path', default=None, help='指定自定义微信存储目录 (默认: 自动发现系统微信目录)')
    web_p.add_argument('--no-browser', action='store_true', help='不自动打开默认浏览器')
    web_p.add_argument('--whitelist-config', default=None, help=argparse.SUPPRESS)
    web_p.add_argument('--state-path', default=None, help=argparse.SUPPRESS)

    tag_p = subparsers.add_parser('tag', help='核心人脉与重要会话防删白名单管理')
    tag_p.add_argument('--add', default=None, metavar='NAME', help='受保护人脉/群名称 (如: "老婆", "重要客户")')
    tag_p.add_argument('--wxid', default=None, help='联系人微信号/wxid/群ID (如: "wxid_xxx", "xxx@chatroom")')
    tag_p.add_argument('--protect', choices=['absolute', 'retain_days'], default='absolute', help='保护级别: absolute (绝对保护永不删) 或 retain_days (保留N天内文件)')
    tag_p.add_argument('--keywords', default=None, help='保护文件名关键词，逗号分隔 (如: "合同,宝宝,结婚")')
    tag_p.add_argument('--retain-days', type=int, default=0, help='保留天数 (配合 --protect retain_days 使用)')
    tag_p.add_argument('--remove', default=None, metavar='NAME_OR_WXID', help='移除指定的白名单规则')
    tag_p.add_argument('--list', action='store_true', help='列出所有当前生效的白名单规则')
    tag_p.add_argument('--clear', action='store_true', help='清空所有白名单规则')
    tag_p.add_argument('--whitelist-config', default=None, help=argparse.SUPPRESS)

    stats_p = subparsers.add_parser('stats', help='查看历史累计瘦身统计与操作记录')
    stats_p.add_argument('--state-path', default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.subcommand == 'scan':
        cmd_scan(args)
    elif args.subcommand == 'clean':
        cmd_clean(args)
    elif args.subcommand == 'dedup':
        cmd_dedup(args)
    elif args.subcommand == 'web':
        cmd_web(args)
    elif args.subcommand == 'tag':
        cmd_tag(args)
    elif args.subcommand == 'stats':
        cmd_stats(args)
    else:
        interactive_wizard()


if __name__ == '__main__':
    main()
