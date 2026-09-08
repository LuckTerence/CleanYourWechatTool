"""WeChat file slimming, safe trash movement, and archive engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
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
    """安全将文件移入 macOS 废纸篓 (优先使用 macOS 原生 Cocoa API，支持随时放回原处，高性能零卡顿)."""
    try:
        from send2trash import send2trash
        send2trash(str(file_path))
        return True
    except Exception:
        pass

    try:
        trash_dir = Path.home() / ".Trash"
        if trash_dir.is_dir():
            target = trash_dir / file_path.name
            if target.exists():
                target = trash_dir / f"{file_path.stem}_{int(time.time() * 1000)}{file_path.suffix}"
            shutil.move(str(file_path), str(target))
            return True
    except Exception:
        pass
    return False


# ---------- 防御死线 (物理层兜底, 与白名单/过滤规则无关, 任何模式都不可触碰) ----------
# 这些后缀几乎不可能是微信聊天产生的可清理媒体; 一旦误删会破坏微信本体或系统库。
SAFE_SKIP_EXTS = {
    '.db', '.sqlite', '.sqlite3', '.db-shm', '.db-wal',
    '.ldb', '.sst',
    '.dll', '.exe', '.sys', '.pyd', '.dylib', '.pak',
}
# 这些目录名是微信/系统运行时组件所在, 清空会导致微信无法启动或功能缺失。
PROTECTED_DIR_NAMES = {'bin', 'runtime', 'runtimes', 'plugin', 'module', 'frameworks', 'resources'}


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

    if selected_types is None:
        selected_types = [k for k, c in categories.items() if not c.is_protected]

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
            if cancel_event is not None and cancel_event.is_set():
                # 取消以异常上抛 (而非静默返回部分结果): GUI 统一识别为"已取消",
                # 避免把半程结果当成完成渲染误导用户
                raise RuntimeError('cancelled by user')
            if progress_cb is not None and cur_idx % 500 == 0:
                progress_cb(f'已检查 {cur_idx:,}/{total_target_files:,} 个文件，命中 {freed_count:,} 个')
            if not dry_run and total_target_files > 50 and cur_idx % 20 == 0:
                render_progress(cur_idx, total_target_files, prefix="正在瘦身处理")

            # 绝对安全护栏 1：绝不处理数据库文件
            if fp.suffix in ['.db', '.db-wal', '.db-shm', '.sqlite', '.wcdb'] or 'db_storage' in fp.parts:
                continue

            # 绝对安全护栏 1b (防御死线): 敏感后缀与运行时/组件目录, 物理层兜底
            if fp.suffix.lower() in SAFE_SKIP_EXTS:
                continue
            if any(part.lower() in PROTECTED_DIR_NAMES for part in fp.parts):
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
                        rel_path = Path(cat.name) / fp.name
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
