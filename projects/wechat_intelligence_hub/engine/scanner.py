"""WeChat storage scanning engine and account discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


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


def _inspect_account_dir(item: Path, version_hint: str = '') -> Optional[AccountProfile]:
    """统一探测任意平台 (Mac / Windows) 账号子目录的文件组织结构."""
    if not item.is_dir() or item.name.startswith('.'):
        return None
    if item.name.lower() in ('all users', 'all_users', 'backup', 'applet', 'wmpf', 'mmkv'):
        return None

    filestorage = item / 'FileStorage'
    db_storage = item / 'db_storage'
    msg_dir = item / 'msg'
    cache_dir = item / 'cache'

    # 1. 微信 4.0+ 现代化跨平台结构 (db_storage/ + msg/ + cache/)
    # 特征: 存在 db_storage/, 或者 msg 包含 video/file/attach 子目录
    if db_storage.is_dir() or (msg_dir / 'video').is_dir() or (msg_dir / 'file').is_dir() or (msg_dir / 'attach').is_dir():
        return AccountProfile(
            account_id=item.name,
            version_type=version_hint or 'v4 (微信 4.0+)',
            root_path=item,
            db_path=db_storage if db_storage.is_dir() else None,
            msg_video_path=msg_dir / 'video' if (msg_dir / 'video').is_dir() else None,
            msg_file_path=msg_dir / 'file' if (msg_dir / 'file').is_dir() else None,
            msg_attach_path=msg_dir / 'attach' if (msg_dir / 'attach').is_dir() else None,
            cache_path=cache_dir if cache_dir.is_dir() else None,
            temp_path=item / 'temp' if (item / 'temp').is_dir() else None,
        )

    # 2. Windows WeChat 传统结构 (FileStorage/ + Msg/)
    msg_win = item / 'Msg'
    if filestorage.is_dir() or (msg_win.is_dir() and any(msg_win.glob('*.db'))):
        return AccountProfile(
            account_id=item.name,
            version_type=version_hint or 'Windows WeChat',
            root_path=item,
            db_path=msg_win if msg_win.is_dir() else None,
            msg_video_path=filestorage / 'Video' if (filestorage / 'Video').is_dir() else None,
            msg_file_path=filestorage / 'File' if (filestorage / 'File').is_dir() else None,
            msg_attach_path=(
                filestorage / 'MsgAttach' if (filestorage / 'MsgAttach').is_dir()
                else (filestorage / 'Image' if (filestorage / 'Image').is_dir() else None)
            ),
            cache_path=filestorage / 'Cache' if (filestorage / 'Cache').is_dir() else None,
            temp_path=filestorage / 'Temp' if (filestorage / 'Temp').is_dir() else None,
        )

    # 3. 微信 4.0+ 宽松兜底 (msg/ 或 cache/ 存在)
    if msg_dir.is_dir() or cache_dir.is_dir():
        return AccountProfile(
            account_id=item.name,
            version_type=version_hint or 'v4 (微信 4.0+)',
            root_path=item,
            db_path=db_storage if db_storage.is_dir() else None,
            msg_video_path=msg_dir / 'video' if (msg_dir / 'video').is_dir() else None,
            msg_file_path=msg_dir / 'file' if (msg_dir / 'file').is_dir() else None,
            msg_attach_path=msg_dir / 'attach' if (msg_dir / 'attach').is_dir() else None,
            cache_path=cache_dir if cache_dir.is_dir() else None,
            temp_path=item / 'temp' if (item / 'temp').is_dir() else None,
        )

    # 4. macOS 微信 3.x 传统结构 (Message/ + Caches/)
    msg_temp = item / 'Message/MessageTemp'
    caches = item / 'Caches'
    if msg_temp.is_dir() or caches.is_dir():
        return AccountProfile(
            account_id=item.name[:8] + '...',
            version_type=version_hint or 'v3 (macOS 微信 3.x)',
            root_path=item,
            msg_attach_path=msg_temp if msg_temp.is_dir() else None,
            cache_path=caches if caches.is_dir() else None,
        )

    return None


def _discover_windows_wechat_bases() -> List[Path]:
    """探测 Windows 系统上所有潜在的 WeChat Files 根目录 (注册表 + 常见盘符)."""
    bases: List[Path] = []
    seen: Set[str] = set()

    # 1. 读取 Windows 注册表 HKCU\Software\Tencent\WeChat\FileSavePath
    try:
        import winreg  # type: ignore[import-not-found]
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat") as key:
            val, _ = winreg.QueryValueEx(key, "FileSavePath")
            if val:
                val_str = str(val).strip()
                if val_str == "MyDocument:" or not val_str:
                    doc_base = Path.home() / 'Documents' / 'WeChat Files'
                    if doc_base.is_dir():
                        bases.append(doc_base)
                        seen.add(str(doc_base.resolve()).lower())
                else:
                    custom_p = Path(val_str)
                    target = custom_p / 'WeChat Files' if (custom_p / 'WeChat Files').is_dir() else custom_p
                    if target.is_dir() and str(target.resolve()).lower() not in seen:
                        bases.append(target)
                        seen.add(str(target.resolve()).lower())
    except Exception:
        pass

    # 2. 常见默认路径扫描 (用户文档、OneDrive 同步文档、常见盘符)
    home = Path.home()
    candidates = [
        home / 'Documents/WeChat Files',
        home / 'OneDrive/Documents/WeChat Files',
        home / 'AppData/Local/Packages/TencentWeChatLimited.WeChatUWP_8v73y9jy5nwvv/LocalCache/Roaming/Tencent/WeChatAppStore/WeChatAppStore Files',
    ]
    for drive in ('D', 'E', 'F', 'G'):
        candidates.append(Path(f"{drive}:/WeChat Files"))
        candidates.append(Path(f"{drive}:/xwechat_files"))

    for c in candidates:
        try:
            if c.is_dir():
                resolved = str(c.resolve()).lower()
                if resolved not in seen:
                    bases.append(c)
                    seen.add(resolved)
        except (OSError, PermissionError):
            continue

    return bases


def discover_accounts(custom_path: Optional[Path] = None) -> List[AccountProfile]:
    """跨平台自动发现或指定微信存储账号路径 (支持 macOS 与 Windows 全版本)."""
    if custom_path:
        cp = Path(custom_path).resolve()
        if cp.is_dir():
            acc = _inspect_account_dir(cp, version_hint='custom (自定义目录)')
            if acc:
                return [acc]
            accs = []
            for sub in cp.iterdir():
                if sub.is_dir() and not sub.name.startswith('.'):
                    sub_acc = _inspect_account_dir(sub, version_hint='custom (自定义目录)')
                    if not sub_acc:
                        sub_acc = AccountProfile(
                            account_id=sub.name,
                            version_type='custom (自定义目录)',
                            root_path=sub,
                            db_path=sub / 'db_storage' if (sub / 'db_storage').exists() else (sub / 'Msg' if (sub / 'Msg').exists() else None),
                            msg_video_path=sub / 'msg/video' if (sub / 'msg/video').exists() else (sub / 'FileStorage/Video' if (sub / 'FileStorage/Video').exists() else None),
                            msg_file_path=sub / 'msg/file' if (sub / 'msg/file').exists() else (sub / 'FileStorage/File' if (sub / 'FileStorage/File').exists() else None),
                            msg_attach_path=sub / 'msg/attach' if (sub / 'msg/attach').exists() else (sub / 'FileStorage/MsgAttach' if (sub / 'FileStorage/MsgAttach').exists() else None),
                            cache_path=sub / 'cache' if (sub / 'cache').exists() else (sub / 'FileStorage/Cache' if (sub / 'FileStorage/Cache').exists() else None),
                            temp_path=sub / 'temp' if (sub / 'temp').exists() else (sub / 'FileStorage/Temp' if (sub / 'FileStorage/Temp').exists() else None),
                        )
                    accs.append(sub_acc)
            if accs:
                return accs
            return [
                AccountProfile(
                    account_id=cp.name,
                    version_type='custom (自定义目录)',
                    root_path=cp,
                    db_path=cp / 'db_storage' if (cp / 'db_storage').exists() else (cp / 'Msg' if (cp / 'Msg').exists() else None),
                    msg_video_path=cp / 'msg/video' if (cp / 'msg/video').exists() else (cp / 'FileStorage/Video' if (cp / 'FileStorage/Video').exists() else None),
                    msg_file_path=cp / 'msg/file' if (cp / 'msg/file').exists() else (cp / 'FileStorage/File' if (cp / 'FileStorage/File').exists() else None),
                    msg_attach_path=cp / 'msg/attach' if (cp / 'msg/attach').exists() else (cp / 'FileStorage/MsgAttach' if (cp / 'FileStorage/MsgAttach').exists() else None),
                    cache_path=cp / 'cache' if (cp / 'cache').exists() else (cp / 'FileStorage/Cache' if (cp / 'FileStorage/Cache').exists() else None),
                    temp_path=cp / 'temp' if (cp / 'temp').exists() else (cp / 'FileStorage/Temp' if (cp / 'FileStorage/Temp').exists() else None),
                )
            ]
        return []

    accounts: List[AccountProfile] = []
    seen_roots: Set[str] = set()
    home = Path.home()

    # 1. macOS 微信 4.0+ 路径
    v4_base = home / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
    if v4_base.is_dir():
        for item in v4_base.iterdir():
            acc = _inspect_account_dir(item, version_hint='v4 (微信 4.0+)')
            if acc:
                key = str(acc.root_path.resolve()).lower()
                if key not in seen_roots:
                    accounts.append(acc)
                    seen_roots.add(key)

    # 2. macOS 微信 3.x 传统路径
    v3_base = home / 'Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat'
    if v3_base.is_dir():
        for ver in v3_base.iterdir():
            if ver.is_dir() and not ver.name.startswith('.'):
                for acc_dir in ver.iterdir():
                    if acc_dir.is_dir() and len(acc_dir.name) == 32:
                        acc = _inspect_account_dir(acc_dir, version_hint=f'v3 ({ver.name})')
                        if acc:
                            key = str(acc.root_path.resolve()).lower()
                            if key not in seen_roots:
                                accounts.append(acc)
                                seen_roots.add(key)

    # 3. Windows 微信全版本路径 (WeChat Files + 注册表自定义路径)
    for win_base in _discover_windows_wechat_bases():
        for item in win_base.iterdir():
            acc = _inspect_account_dir(item, version_hint='Windows WeChat')
            if acc:
                key = str(acc.root_path.resolve()).lower()
                if key not in seen_roots:
                    accounts.append(acc)
                    seen_roots.add(key)

    return accounts


def scan_directory(
    category_name: str,
    desc: str,
    dir_path: Optional[Path],
    is_protected: bool = False,
    collect_files: bool = True,
) -> ScanCategory:
    """递归统计指定目录下的文件数量与总大小 (基于 os.scandir 复用 DirEntry 元数据，受保护目录零内存开销)."""
    cat = ScanCategory(
        name=category_name,
        description=desc,
        path=dir_path or Path('/dev/null'),
        is_protected=is_protected,
    )
    if not dir_path or not dir_path.exists():
        return cat

    stack = [str(dir_path)]
    while stack:
        current_dir = stack.pop()
        try:
            with os.scandir(current_dir) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            cat.file_count += 1
                            cat.total_bytes += st.st_size
                            if collect_files and not is_protected:
                                cat.files.append((Path(entry.path), st.st_size, st.st_mtime))
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            continue

    return cat


def scan_account(acc: AccountProfile, collect_files: bool = True) -> Dict[str, ScanCategory]:
    """对单个账号执行全量存储透视扫描."""
    results: Dict[str, ScanCategory] = {}

    # 1. 核心数据库 (必须保护，零内存缓冲)
    results['db'] = scan_directory(
        'db_storage',
        '核心聊天数据库与文字索引 [🔒 绝对保护，禁止删除]',
        acc.db_path,
        is_protected=True,
        collect_files=collect_files,
    )

    # 2. 视频缓存
    results['video'] = scan_directory(
        'video', '接收与缓存的视频文件 (msg/video)', acc.msg_video_path, collect_files=collect_files
    )

    # 3. 接收文件
    results['file'] = scan_directory(
        'file', '接收的文档与办公文件 (msg/file)', acc.msg_file_path, collect_files=collect_files
    )

    # 4. 聊天图片与多媒体附件
    results['attach'] = scan_directory(
        'attach', '聊天图片、表情与多媒体附件 (msg/attach)', acc.msg_attach_path, collect_files=collect_files
    )

    # 5. 缓存与临时文件
    cache_files: List[Tuple[Path, int, float]] = []
    total_cache_size = 0
    total_cache_count = 0
    for p in [acc.cache_path, acc.temp_path]:
        if p and p.exists():
            c = scan_directory('cache_raw', '', p, collect_files=collect_files)
            if collect_files:
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

    # 6. 容器级共享区黑洞 (跨账号, 极易被漏扫的重度使用体积):
    #    Documents/app_data/{radium, log, xplugin}
    #    - radium: 微信 4.0 渲染引擎缓存 + crashpad 崩溃转储 (重度用户可达数 GB)
    #    - log:    xlog 加密运行日志 (与聊天记录无关, 零保留价值)
    #    - xplugin: 小程序插件包体 (删除后小程序自动重新下载)
    #    结构依赖: root_path = <Documents>/xwechat_files/<account_id>,
    #    Documents = root_path.parent.parent; 自定义 --path 不满足该层级时自然跳过。
    shared_dirs = [
        ('radium', 'radium', '渲染引擎缓存与崩溃转储 (app_data/radium) [可安全清理]'),
        ('logs', 'log', '运行日志 (app_data/log) [可安全清理]'),
        ('xplugin', 'xplugin', '小程序插件包体 (app_data/xplugin) [删除后自动重新下载]'),
    ]
    try:
        documents_dir = acc.root_path.parent.parent
    except (IndexError, AttributeError):
        documents_dir = None
    if documents_dir and documents_dir.name == 'Documents':
        app_data = documents_dir / 'app_data'
        for key, sub, desc in shared_dirs:
            sub_path = app_data / sub
            if sub_path.is_dir():
                results[key] = scan_directory(key, desc, sub_path, collect_files=collect_files)

    return results
