"""WeChat file slimming, safe trash movement, and archive engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from engine.common import render_progress, _audit_logger
    from engine.scanner import AccountProfile, ScanCategory
    from engine.whitelist import WhiteListManager
except ImportError:
    from .common import render_progress, _audit_logger
    from .scanner import AccountProfile, ScanCategory
    from .whitelist import WhiteListManager


def move_to_trash(file_path: Path) -> bool:
    """安全将文件移入系统回收站/废纸篓 (支持随时放回原处，高性能零卡顿)."""
    try:
        from send2trash import send2trash
        send2trash(str(file_path))
        return True
    except Exception as e:
        _audit_logger.debug(f"send2trash failed for {file_path}: {e}")

    # Windows 原生 Win32 API 兜底 (SHFileOperationW with FOF_ALLOWUNDO: 移入回收站而非物理删除)
    if sys.platform == 'win32':  # pragma: no cover
        try:
            import ctypes
            from ctypes import wintypes

            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", wintypes.WORD),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", wintypes.LPVOID),
                    ("lpszProgressTitle", wintypes.LPCWSTR),
                ]

            FO_DELETE = 0x0003
            FOF_ALLOWUNDO = 0x0040
            FOF_NOCONFIRMATION = 0x0010
            FOF_SILENT = 0x0004
            FOF_NOERRORUI = 0x0400

            file_str = str(file_path.resolve()) + '\0\0'
            fileop = SHFILEOPSTRUCTW()
            fileop.wFunc = FO_DELETE
            fileop.pFrom = file_str
            fileop.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

            res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(fileop))
            if res == 0 and not fileop.fAnyOperationsAborted:
                return True
        except Exception as e:
            _audit_logger.warning(f"Windows SHFileOperationW failed for {file_path}: {e}")

    # macOS 备用兜底: ~/.Trash
    try:
        trash_dir = Path.home() / ".Trash"
        if trash_dir.is_dir():
            target = trash_dir / file_path.name
            if target.exists():
                target = trash_dir / f"{file_path.stem}_{int(time.time() * 1000)}{file_path.suffix}"
            shutil.move(str(file_path), str(target))
            return True
    except Exception as e:
        _audit_logger.debug(f"~/.Trash fallback failed for {file_path}: {e}")

    _audit_logger.error(f"Failed to move file to trash via all methods: {file_path}")
    return False


# ---------- 防御死线 (物理层兜底, 与白名单/过滤规则无关, 任何模式都不可触碰) ----------
# 这些后缀几乎不可能是微信聊天产生的可清理媒体; 一旦误删会破坏微信本体或系统库。
SAFE_SKIP_EXTS = {
    '.db', '.sqlite', '.sqlite3', '.db-shm', '.db-wal', '.wcdb',
    '.ldb', '.sst',
    '.dll', '.exe', '.sys', '.pyd', '.dylib', '.pak',
}
# 这些目录名是微信/系统运行时组件所在, 清空会导致微信无法启动或功能缺失。
PROTECTED_DIR_NAMES = {'bin', 'runtime', 'runtimes', 'plugin', 'module', 'frameworks', 'resources'}


def is_hard_protected(fp: Path, account_root: Optional[Path] = None) -> bool:
    """物理死线判定: 命中即任何清理/归档规则都不可触碰。

    **单一事实来源**: 执行路径 (execute_slimming)、GUI 的清理路径
    (execute_files_to_trash) 与界面预估必须都调用本函数, 以保证
    「界面承诺的释放量」与「实际可清理量」完全一致 —— 此前两处独立实现
    且预估未过滤, 导致界面显示 304 MB 而实际仅能释放约 238 MB (虚高 22%)。

    覆盖三类:
    1. 敏感后缀: 数据库与程序组件 (.db/.db-wal/.sqlite/.dll/.pak 等);
    2. 数据库目录: db_storage 及其子项、msg 目录的直属文件;
    3. 账号根目录**第一层**的运行时目录 (bin/runtime/frameworks/resources...)。
       仅限第一层 —— 缓存内部的同名子目录 (如
       radium/.../xworker/liteapp/resources/) 属于缓存结构, 应当允许清理,
       否则会白留可释放空间并造成预估虚高。

    account_root 未提供时, 第 3 条退化为「任意层级命中即拦截」的保守判定,
    避免调用方遗漏参数时削弱安全性。
    """
    parts_lower = [p.lower() for p in fp.parts]
    if fp.suffix.lower() in SAFE_SKIP_EXTS:
        return True
    if 'db_storage' in parts_lower:
        return True
    if fp.parent.name.lower() == 'msg':
        return True
    if account_root is None:
        # 无账号上下文: 保守处理 (旧行为, 任意层级命中即拦)
        return any(p in PROTECTED_DIR_NAMES for p in parts_lower)
    try:
        rel_parts = fp.relative_to(account_root).parts
    except ValueError:
        # 不在账号目录内 (如容器级缓存 app_data/radium): 本条第 3 类不适用
        return False
    return bool(rel_parts) and rel_parts[0].lower() in PROTECTED_DIR_NAMES


VIDEO_EXTS = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.flv', '.rmvb', '.3gp', '.wmv'}
ARCHIVE_EXTS = {'.dmg', '.zip', '.pkg', '.tar', '.gz', '.7z', '.rar', '.iso', '.tgz', '.bz2'}
DOCUMENT_EXTS = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.key', '.pages', '.numbers', '.txt', '.csv', '.rtf', '.epub', '.mobi'
}


def classify_file_type(fp: Path) -> str:
    """按文件安全等级划分类型: video (大视频), archive (安装包/压缩包), document (办公文档), other (其他)."""
    s = fp.suffix.lower()
    if s in VIDEO_EXTS:
        return 'video'
    if s in ARCHIVE_EXTS:
        return 'archive'
    if s in DOCUMENT_EXTS:
        return 'document'
    return 'other'


@dataclass
class SlimResult:
    """瘦身执行统计结果 (支持解构赋值 (freed_count, freed_bytes) 保持向下兼容)."""
    freed_count: int
    freed_bytes: int
    protected_count: int = 0
    protected_bytes: int = 0
    # 本次命中待处理文件的完整清单 [(path, size, mtime)]——
    # 让用户在按下确认键之前能逐个看到"到底会动哪些文件"，
    # 这是"绝不误删"承诺在交互层的关键一环，不能只给统计数字。
    affected_files: List[Tuple[Path, int, float]] = field(default_factory=list)

    def __iter__(self):
        return iter((self.freed_count, self.freed_bytes))


def execute_slimming(
    acc: AccountProfile,
    categories: Dict[str, ScanCategory],
    days: int,
    min_size_bytes: int,
    selected_types: Optional[List[str]] = None,
    dry_run: bool = False,
    archive_to: Optional[Path] = None,
    whitelist_mgr: Optional[WhiteListManager] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[Any] = None,
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
    affected_files: List[Tuple[Path, int, float]] = []
    archived_entries: List[Dict[str, Any]] = []

    # 显式传入空列表: 100% 阻断，绝对不触碰任何文件
    if selected_types is not None and len(selected_types) == 0:
        return SlimResult(0, 0, 0, 0, [])

    if selected_types is None:
        selected_types = [k for k, c in categories.items() if not c.is_protected]

    if archive_to:
        archive_to = archive_to.resolve()
        if not dry_run:
            archive_to.mkdir(parents=True, exist_ok=True)

    candidate_files: List[Tuple[Path, int, float, str]] = []
    for cat_key, cat in categories.items():
        if not cat or cat.is_protected:
            continue
        for fp, size, mtime in cat.files:
            # 1. 粗粒度分类匹配 (CLI 兼容: 传入 'file', 'cache', 'attach' 等原始分类键)
            if cat_key in selected_types:
                candidate_files.append((fp, size, mtime, cat_key))
                continue

            # 2. 细粒度类型匹配 (GUI 智能分类: 'video', 'archive', 'document')
            ft = classify_file_type(fp)
            eff_type = 'video' if (cat_key == 'video' and ft != 'document') else ft
            if eff_type in selected_types:
                candidate_files.append((fp, size, mtime, cat_key))

    total_target_files = len(candidate_files)
    cur_idx = 0

    for fp, size, mtime, cat_key in candidate_files:
        cur_idx += 1
        if cancel_event is not None and cancel_event.is_set():
            # 取消以异常上抛 (而非静默返回部分结果): GUI 统一识别为"已取消",
            # 避免把半程结果当成完成渲染误导用户
            raise RuntimeError('cancelled by user')
        if progress_cb is not None and cur_idx % 500 == 0:
            progress_cb(f'已检查 {cur_idx:,}/{total_target_files:,} 个文件，命中 {freed_count:,} 个')
        if not dry_run and total_target_files > 50 and cur_idx % 20 == 0:
            render_progress(cur_idx, total_target_files, prefix="正在瘦身处理")

        # 绝对安全护栏 1：绝不处理数据库文件与敏感系统/组件 (大小写不敏感物理死线)
        # 统一走 is_hard_protected (单一事实来源), 与界面预估口径完全一致
        if is_hard_protected(fp, acc.root_path if acc else None):
            continue
        # 护栏 1b (额外保险): 账号数据库目录下的任何文件绝不触碰
        if acc and acc.db_path and (acc.db_path == fp or acc.db_path in fp.parents):
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
        if dry_run:
            freed_count += 1
            freed_bytes += size
            affected_files.append((fp, size, mtime))
            continue

        try:
            if archive_to:
                # 归档模式：计算相对路径并安全移动到外置目录
                try:
                    rel_path = fp.relative_to(acc.root_path)
                except ValueError:
                    rel_path = Path(cat_key) / fp.name
                dest_path = archive_to / rel_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                # 外置归档已存在同名文件：静默覆盖会丢数据，改为生成不冲突名 (保留扩展名)
                if dest_path.exists():
                    dest_suffix = dest_path.suffix
                    dest_stem = dest_path.stem
                    collide_counter = 2
                    renamed_dest = dest_path.with_name(f"{dest_stem}_{collide_counter}{dest_suffix}")
                    while renamed_dest.exists():
                        collide_counter += 1
                        renamed_dest = dest_path.with_name(f"{dest_stem}_{collide_counter}{dest_suffix}")
                    _audit_logger.warning(
                        f"归档冲突: 外置目录已存在同名文件 [{dest_path.name}]，"
                        f"已重命名为 [{renamed_dest.name}] 以避免静默覆盖旧归档"
                    )
                    dest_path = renamed_dest
                shutil.move(str(fp), str(dest_path))
                freed_count += 1
                freed_bytes += size
                affected_files.append((fp, size, mtime))
                # 归档元数据: 记录 原始路径 <-> 归档路径, 供未来一键恢复
                archived_entries.append({
                    'original_path': str(fp),
                    'archived_path': str(dest_path),
                    'size': size,
                    'mtime': mtime,
                    'archived_at': datetime.now().isoformat(timespec='seconds'),
                })
            else:
                # 默认安全清理：移至 macOS 废纸篓
                if move_to_trash(fp):
                    freed_count += 1
                    freed_bytes += size
                    affected_files.append((fp, size, mtime))
                else:
                    _audit_logger.warning(f"移入废纸篓失败: {fp}")
        except (OSError, PermissionError, shutil.Error) as e:
            _audit_logger.error(f"Failed to process file {fp}: {e}")
            continue

    if not dry_run and total_target_files > 50:
        render_progress(total_target_files, total_target_files, prefix="正在瘦身处理")

    # 归档模式: 将本次与历史归档元数据合并写入 manifest (企业级无损归档的恢复依据)
    if archive_to and not dry_run and archived_entries:
        try:
            _write_archive_manifest(archive_to, archived_entries)
        except (OSError, ValueError) as e:
            _audit_logger.error(f"写入归档清单失败: {e}")

    return SlimResult(freed_count, freed_bytes, protected_count, protected_bytes, affected_files)


MANIFEST_NAME = 'archive_manifest.json'


def _write_archive_manifest(archive_to: Path, new_entries: List[Dict[str, Any]]) -> Path:
    """将归档元数据合并写入 archive_to/archive_manifest.json (多次归档追加合并)."""
    manifest_path = Path(archive_to) / MANIFEST_NAME
    entries: List[Dict[str, Any]] = []
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding='utf-8'))
            if isinstance(existing, dict) and isinstance(existing.get('entries'), list):
                entries = existing['entries']
        except (ValueError, OSError):
            # 清单损坏则重建 (entries 与实际文件由用户自行核对)
            _audit_logger.warning('归档清单损坏, 已重建')
    known = {e.get('archived_path') for e in entries if isinstance(e, dict)}
    for e in new_entries:
        if e['archived_path'] not in known:
            entries.append(e)
    manifest = {
        'version': 1,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'count': len(entries),
        'entries': entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    _audit_logger.info(f'归档清单已更新: {manifest_path} (共 {len(entries)} 条)')
    return manifest_path


def restore_from_manifest(manifest_path: Path, overwrite: bool = False) -> Tuple[int, int, int]:
    """按归档清单将文件恢复回微信原始位置.

    返回 (restored, skipped_existing, missing_archived)。
    恢复语义: 目标位置已存在同名文件时默认跳过 (不覆盖), overwrite=True 时才覆盖。
    """
    manifest_path = Path(manifest_path)
    restored = skipped = missing = 0
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    entries = data.get('entries', []) if isinstance(data, dict) else []
    for e in entries:
        archived = Path(e['archived_path'])
        original = Path(e['original_path'])
        if not archived.exists():
            missing += 1
            continue
        if original.exists() and not overwrite:
            skipped += 1
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(archived), str(original))
        restored += 1
    return restored, skipped, missing
