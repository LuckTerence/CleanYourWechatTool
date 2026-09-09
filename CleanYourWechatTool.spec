# -*- mode: python ; coding: utf-8 -*-
"""CleanYourWechatTool GUI 打包配置 (PyInstaller, 本地与 CI 统一使用).

Info.plist 权限描述用于 macOS TCC 授权:
- 废纸篓清理经 Apple Events 调用 Finder, 需要 NSAppleEventsUsageDescription
- 扫描/归档可能触及桌面/文档/下载与外置卷
"""
import os
import sys
from PyInstaller.utils.hooks import collect_data_files

# 本 spec 位于仓库根目录, 项目根即 spec 所在目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(SPEC))

datas = []
datas += collect_data_files('customtkinter')
datas += collect_data_files('darkdetect')
assets_dir = os.path.join(PROJECT_ROOT, 'assets')
if os.path.exists(assets_dir):
    datas.append((assets_dir, 'assets'))


a = Analysis(
    ['clean_wechat_gui.py'],
    pathex=[PROJECT_ROOT,
            os.path.join(PROJECT_ROOT, 'projects', 'wechat_intelligence_hub'),
            os.path.join(PROJECT_ROOT, 'projects', 'wechat-intelligence-hub')],
    binaries=[],
    datas=datas,
    # PIL 用于窗口图标; 显式声明避免打包环境未自动收集时产物启动即崩
    # (v1.0.5 的 .app 即因缺 PIL 而 ModuleNotFoundError)。
    hiddenimports=['PIL', 'PIL.Image', 'PIL.ImageTk'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

icon_path = None
if sys.platform == 'darwin':
    icns_candidate = os.path.join(PROJECT_ROOT, 'assets', 'app.icns')
    if os.path.exists(icns_candidate):
        icon_path = icns_candidate
elif sys.platform == 'win32':
    ico_candidate = os.path.join(PROJECT_ROOT, 'assets', 'app.ico')
    if os.path.exists(ico_candidate):
        icon_path = ico_candidate

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CleanYourWechatTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='CleanYourWechatTool',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='CleanYourWechatTool.app',
        icon=icon_path,
        bundle_identifier='com.luckterence.cleanyourwechat',
        info_plist={
            'NSAppleEventsUsageDescription': 'CleanYourWechatTool 需要向访达 (Finder) 发送指令，将待清理文件安全移入系统废纸篓。',
            'NSDesktopFolderUsageDescription': 'CleanYourWechatTool 需要访问桌面文件夹，以读取或保存微信文件归档。',
            'NSDocumentsFolderUsageDescription': 'CleanYourWechatTool 需要访问文档文件夹，以读取或保存微信文件归档。',
            'NSDownloadsFolderUsageDescription': 'CleanYourWechatTool 需要访问下载文件夹，以读取或保存微信文件归档。',
            'NSVolumeUsageDescription': 'CleanYourWechatTool 需要访问外接硬盘或 NAS，以便将微信大文件无损归档到外部存储。',
            'NSHighResolutionCapable': True,
        },
    )
