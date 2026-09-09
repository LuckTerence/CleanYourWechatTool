"""Multi-chat duplicate file detection and APFS hardlink deduplication engine."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    from engine.common import render_progress, _audit_logger
    from engine.scanner import ScanCategory
    from engine.cleaner import move_to_trash
    from engine.whitelist import WhiteListManager
except ImportError:
    from .common import render_progress, _audit_logger
    from .scanner import ScanCategory
    from .cleaner import move_to_trash
    from .whitelist import WhiteListManager


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
    # usedforsecurity 仅 Python 3.9+ 支持; 3.8 下需降级为普通 md5 (内容指纹非安全用途)
    try:
        hasher = hashlib.md5(usedforsecurity=False)  # nosec B324
    except TypeError:  # Python 3.8
        hasher = hashlib.md5()  # nosec B324
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


def compute_full_hash(fp: Path, chunk_size: int = 524288) -> str:
    """全量 MD5 计算完整文件校验和 (512KB 缓冲区大幅减少 read 系统调用)."""
    # usedforsecurity 仅 Python 3.9+ 支持 (见 compute_fast_hash 同款兼容处理)
    try:
        hasher = hashlib.md5(usedforsecurity=False)  # nosec B324
    except TypeError:  # Python 3.8
        hasher = hashlib.md5()  # nosec B324
    try:
        with open(fp, 'rb') as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)
    except (OSError, PermissionError):
        return ''
    return hasher.hexdigest()


def find_duplicates(
    categories: Dict[str, ScanCategory],
    selected_types: List[str],
    min_size_bytes: int = 1024,
    whitelist_mgr: Optional[WhiteListManager] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[Any] = None,
) -> List[DuplicateGroup]:
    """三级流水线快速查找重复文件 (大小桶分流 -> 稀疏哈希 -> 全量哈希，集成 Inode 缓存与分步剪枝).

    若传入 whitelist_mgr，命中白名单的文件在「收集阶段」即被剔除，不参与后续查重，
    因此也不会被计入任何重复组的 wasted_count / saving_bytes（满足 PRD「命中白名单的文件绝不被触碰」）。
    """
    # 1. 收集文件并按文件精确大小归类 (大小不同的文件绝不可能是重复文件)
    size_buckets: Dict[int, List[Path]] = defaultdict(list)
    collected = 0
    for t in selected_types:
        cat = categories.get(t)
        if not cat or cat.is_protected:
            continue
        for fp, size, mtime in cat.files:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('cancelled by user')
            collected += 1
            if progress_cb is not None and collected % 500 == 0:
                progress_cb(f'已收集 {collected:,} 个文件…')
            if fp.suffix in ['.db', '.db-wal', '.db-shm', '.sqlite', '.wcdb'] or 'db_storage' in fp.parts:
                continue
            if size > 0 and size >= min_size_bytes:
                # 白名单防御：命中白名单的文件直接跳过收集，绝不参与查重
                if whitelist_mgr is not None:
                    is_prot, _ = whitelist_mgr.is_protected(fp, mtime)
                    if is_prot:
                        continue
                size_buckets[size].append(fp)

    # 阶段 1 内存就地剪枝：移除唯一大小文件
    candidate_sizes = [sz for sz, fps in size_buckets.items() if len(fps) > 1]
    if not candidate_sizes:
        return []

    # Inode 缓存：记录 inode_key -> (fast_hash, full_hash) 消除同 Inode 硬链接的重复磁盘 I/O
    inode_fast_cache: Dict[Any, str] = {}
    inode_full_cache: Dict[Any, str] = {}
    file_stat_cache: Dict[Path, Tuple[Any, float]] = {}

    def get_file_info(p: Path) -> Optional[Tuple[Any, float]]:
        if p in file_stat_cache:
            return file_stat_cache[p]
        try:
            st = p.stat()
            # Windows 或非 Inode 文件系统下 st_ino 可能为 0; 若为 0 则回退至文件绝对路径,
            # 坚决杜绝因 (st_dev, 0) 键相同导致全盘所有文件错误复用同一文件哈希
            key = (st.st_dev, st.st_ino) if st.st_ino != 0 else str(p.resolve())
            info = (key, st.st_mtime)
            file_stat_cache[p] = info
            return info
        except (OSError, PermissionError):
            return None

    # 2. 仅对存在相同大小的文件进行快速哈希初筛
    fast_hash_buckets: Dict[Tuple[int, str], List[Path]] = defaultdict(list)
    total_candidates = sum(len(size_buckets[sz]) for sz in candidate_sizes)
    hashed = 0
    for sz in candidate_sizes:
        fps = size_buckets[sz]
        for fp in fps:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('cancelled by user')
            hashed += 1
            if progress_cb is not None and hashed % 50 == 0:
                progress_cb(f'指纹初筛 {hashed:,}/{total_candidates:,} 个文件…')
            info = get_file_info(fp)
            if not info:
                continue
            inode_key, _ = info
            if inode_key in inode_fast_cache:
                fh = inode_fast_cache[inode_key]
            else:
                try:
                    fh = compute_fast_hash(fp, sz)
                    if fh:
                        inode_fast_cache[inode_key] = fh
                except (OSError, PermissionError):
                    continue
            if fh:
                fast_hash_buckets[(sz, fh)].append(fp)

    del size_buckets  # 及时释放第一阶段大字典内存

    # 阶段 2 内存就地剪枝：移除快速哈希无碰撞的单例文件
    candidate_fast_keys = [k for k, fps in fast_hash_buckets.items() if len(fps) > 1]
    if not candidate_fast_keys:
        return []

    # 3. 仅对稀疏哈希碰撞的文件进行全量 MD5 确认 (结合 Inode 缓存免除硬链接文件的重复磁盘 I/O)
    full_hash_groups: Dict[str, Tuple[int, List[Path]]] = defaultdict(lambda: (0, []))
    total_full = sum(len(fast_hash_buckets[k]) for k in candidate_fast_keys)
    full_done = 0
    for key in candidate_fast_keys:
        size = key[0]
        fps = fast_hash_buckets[key]
        for fp in fps:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('cancelled by user')
            full_done += 1
            if progress_cb is not None and full_done % 20 == 0:
                progress_cb(f'全量校验 {full_done:,}/{total_full:,} 个碰撞文件…')
            info = get_file_info(fp)
            if not info:
                continue
            inode_key, _ = info
            if inode_key in inode_full_cache:
                full_h = inode_full_cache[inode_key]
            else:
                try:
                    full_h = compute_full_hash(fp)
                    if full_h:
                        inode_full_cache[inode_key] = full_h
                except (OSError, PermissionError):
                    continue
            if full_h:
                prev_size, prev_list = full_hash_groups[full_h]
                full_hash_groups[full_h] = (size, prev_list + [fp])

    del fast_hash_buckets  # 及时释放第二阶段字典内存

    duplicate_groups: List[DuplicateGroup] = []
    for fhash, (size, fps) in full_hash_groups.items():
        if len(fps) > 1:
            # 检查是否有文件已经互为硬链接 (复用已获取的 inode_key)
            inodes_seen: Set[Tuple[int, int]] = set()
            for fp in fps:
                info = get_file_info(fp)
                if info:
                    inodes_seen.add(info[0])

            if len(inodes_seen) > 1:
                wasted_count = len(inodes_seen) - 1
                saving = wasted_count * size
            else:
                wasted_count = 0
                saving = 0

            # 按修改时间排序，保留最早或最基础的文件为主副本
            # 注意: 必须单次调用并显式判空，mypy 才能正确 narrow Optional；
            # 直接在 lambda 里写 `get_file_info(p)[1] if get_file_info(p) else 0`
            # 会被判定为 "Optional 不可索引"，且会重复 stat 两次。
            def _mtime_of(p: Path) -> float:
                info = get_file_info(p)
                return info[1] if info else 0.0

            fps.sort(key=_mtime_of)
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
    whitelist_mgr: Optional[WhiteListManager] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[Any] = None,
) -> Tuple[int, int]:
    """执行重复文件去重.

    action='hardlink': (推荐) 将重复文件原子替换为系统硬链接，原路径原文件名完全保留，
                       微信内各群聊仍可正常读取，但在 macOS APFS 物理磁盘仅占一份空间！
    action='trash':    将冗余副本直接移至 macOS 废纸篓。

    若传入 whitelist_mgr，进行防御性二次过滤：处理每个副本前若 is_protected 命中则跳过该副本
    （不跳过整组）；若主副本被保护，则顺延选择未被保护的第一个作为新主副本；若整组均被保护则跳过整组。
    """
    processed_count = 0
    freed_bytes = 0
    done_copies = 0
    total_copies = sum(len(grp.files) - 1 for grp in groups if grp.wasted_count > 0 and len(grp.files) >= 2)

    for grp in groups:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError('cancelled by user')
        if grp.wasted_count == 0 or len(grp.files) < 2:
            continue

        # 防御性二次白名单过滤：剔除被保护的副本，并据此重新确定主副本（保留最早 mtime 者）
        unprotected: List[Tuple[Path, Any]] = []
        for fp in grp.files:
            try:
                st = fp.stat()
            except OSError:
                continue
            if whitelist_mgr is not None:
                is_prot, _ = whitelist_mgr.is_protected(fp, st.st_mtime)
                if is_prot:
                    continue
            unprotected.append((fp, st))

        if len(unprotected) < 2:
            # 整组被保护或仅剩单一文件，跳过整组
            continue

        primary, prim_st = unprotected[0]
        prim_ino_key = (prim_st.st_dev, prim_st.st_ino)

        for dup, dup_st in unprotected[1:]:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('cancelled by user')
            try:
                if prim_st.st_ino != 0 and dup_st.st_ino != 0:
                    if (dup_st.st_dev, dup_st.st_ino) == prim_ino_key:
                        continue
                elif os.path.samefile(primary, dup):
                    continue
            except (OSError, ValueError):
                pass
            if progress_cb is not None and done_copies % 20 == 0:
                progress_cb(f'去重进度 {done_copies:,}/{total_copies:,} 个副本…')

            if dry_run:
                processed_count += 1
                freed_bytes += grp.file_size
                continue

            if action == 'hardlink':
                tmp_link = dup.with_name(f".tmp_link_{os.getpid()}_{dup.name}")
                try:
                    # 使用临时硬链接原子替换，确保过程安全
                    os.link(primary, tmp_link)
                    os.replace(tmp_link, dup)
                    processed_count += 1
                    done_copies += 1
                    freed_bytes += grp.file_size
                except Exception as e:
                    _audit_logger.warning(f"硬链接去重失败 {dup}: {e}")
                    if tmp_link.exists():
                        try:
                            tmp_link.unlink()
                        except Exception:
                            pass
                    continue
            elif action == 'trash':
                try:
                    if move_to_trash(dup):
                        processed_count += 1
                        done_copies += 1
                        freed_bytes += grp.file_size
                except Exception as e:
                    _audit_logger.warning(f"废纸篓去重失败 {dup}: {e}")
                    continue

            if total_copies > 10 and processed_count % 5 == 0:
                render_progress(processed_count, total_copies, prefix="正在去重处理")

    if not dry_run and total_copies > 10:
        render_progress(total_copies, total_copies, prefix="正在去重处理")

    return processed_count, freed_bytes
