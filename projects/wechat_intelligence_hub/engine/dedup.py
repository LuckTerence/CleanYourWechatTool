"""Multi-chat duplicate file detection and APFS hardlink deduplication engine."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import urllib.parse
import webbrowser

try:
    from engine.common import Colors, format_bytes, render_progress, _audit_logger
    from engine.scanner import ScanCategory
except ImportError:
    from .common import Colors, format_bytes, render_progress, _audit_logger
    from .scanner import ScanCategory
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
    fast_hash_buckets: Dict[Tuple[int, str], List[Path]] = defaultdict(list)
    for sz, fps in size_buckets.items():
        if len(fps) <= 1:
            continue
        for fp in fps:
            try:
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


