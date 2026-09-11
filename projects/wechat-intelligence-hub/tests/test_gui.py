"""Unit and integration tests for clean_wechat_gui helper functions, asset loader, and full GUI state."""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

try:
    import customtkinter as ctk
    from clean_wechat_gui import (  # noqa: E402
        get_asset_path,
        LARGE_DAYS_CHOICES,
        LARGE_SIZE_CHOICES,
        DEDUP_SIZE_CHOICES,
        CleanYourWechatApp,
        execute_files_to_trash,
    )
    HAS_GUI = True
except (ImportError, Exception):
    HAS_GUI = False

from engine.common import parse_size_str, format_bytes  # noqa: E402
from engine.whitelist import WhiteListManager  # noqa: E402
from engine.dedup import DuplicateGroup  # noqa: E402
from engine.scanner import ScanCategory  # noqa: E402
from engine.cleaner import SlimResult  # noqa: E402


@pytest.mark.skipif(not HAS_GUI, reason="GUI dependencies not installed")
class TestGuiHelpers:
    """Test asset locator, choices definitions and filter value parsers."""

    def test_get_asset_path_real_files(self):
        png = get_asset_path('app_1024.png')
        assert png is not None
        assert png.exists()
        assert png.name == 'app_1024.png'

        icns = get_asset_path('app.icns')
        assert icns is not None
        assert icns.exists()
        assert icns.name == 'app.icns'

    def test_get_asset_path_nonexistent(self):
        res = get_asset_path('non_existent_file_xyz_12345.png')
        assert res is None

    def test_get_asset_path_meipass(self, tmp_path, monkeypatch):
        fake_assets = tmp_path / 'assets'
        fake_assets.mkdir()
        fake_file = fake_assets / 'test_mock.png'
        fake_file.write_text('mock')

        monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
        found = get_asset_path('test_mock.png')
        assert found is not None
        assert found == fake_file

    def test_choices_validity(self):
        for label, days in LARGE_DAYS_CHOICES:
            assert isinstance(label, str) and len(label) > 0
            assert isinstance(days, int) and days >= 0

        for label, size_str in LARGE_SIZE_CHOICES:
            assert isinstance(label, str) and len(label) > 0
            sz = parse_size_str(size_str)
            assert sz > 0

        for label, size_str in DEDUP_SIZE_CHOICES:
            assert isinstance(label, str) and len(label) > 0
            sz = parse_size_str(size_str)
            assert sz > 0

    def test_choice_value_matching(self):
        mock_box = MagicMock()
        mock_box.get.return_value = '90天前 (推荐)'
        val = CleanYourWechatApp._choice_value(mock_box, LARGE_DAYS_CHOICES, default=90)
        assert val == 90

        mock_box.get.return_value = '1年前'
        val = CleanYourWechatApp._choice_value(mock_box, LARGE_DAYS_CHOICES, default=90)
        assert val == 365

        mock_box.get.return_value = '未知选项'
        val = CleanYourWechatApp._choice_value(mock_box, LARGE_DAYS_CHOICES, default=90)
        assert val == 90

    def test_size_choice_bytes_matching(self):
        mock_box = MagicMock()
        mock_box.get.return_value = '> 10MB (推荐)'
        val = CleanYourWechatApp._size_choice_bytes(mock_box, LARGE_SIZE_CHOICES, default_bytes=10 * 1024 * 1024)
        assert val == 10 * 1024 * 1024

        mock_box.get.return_value = '> 1GB'
        val = CleanYourWechatApp._size_choice_bytes(mock_box, LARGE_SIZE_CHOICES, default_bytes=10 * 1024 * 1024)
        assert val == 1024 * 1024 * 1024

        mock_box.get.return_value = '未知'
        val = CleanYourWechatApp._size_choice_bytes(mock_box, LARGE_SIZE_CHOICES, default_bytes=500)
        assert val == 500


class TestExecuteFilesToTrash:
    """Test safety filters and cancellation in trash executor."""

    def test_skip_protected_extensions(self, tmp_path):
        f = tmp_path / 'chat.db'
        f.write_text('important data')
        res = execute_files_to_trash([(f, 100, 0.0)], whitelist_mgr=None)
        assert res.freed_count == 0
        assert f.exists()

    def test_skip_protected_directory(self, tmp_path):
        d = tmp_path / 'runtime'
        d.mkdir()
        f = d / 'some_file.dat'
        f.write_text('data')
        res = execute_files_to_trash([(f, 50, 0.0)], whitelist_mgr=None)
        assert res.freed_count == 0
        assert f.exists()

    def test_whitelist_protection(self, tmp_path):
        f = tmp_path / 'client_contract.pdf'
        f.write_text('contract')
        wl = WhiteListManager(config_path=str(tmp_path / 'wl.yaml'))
        wl.add('重要客户', 'wxid_vip', protect='absolute', keywords=['contract'])

        res = execute_files_to_trash([(f, 200, 0.0)], whitelist_mgr=wl)
        assert res.freed_count == 0
        assert res.protected_count == 1
        assert res.protected_bytes == 200
        assert f.exists()

    def test_cancel_event_aborts_early(self, tmp_path):
        files = []
        for i in range(10):
            p = tmp_path / f'junk_{i}.tmp'
            p.write_text('junk')
            files.append((p, 10, 0.0))

        cancel_evt = threading.Event()
        cancel_evt.set()

        res = execute_files_to_trash(files, whitelist_mgr=None, cancel_event=cancel_evt)
        assert res.freed_count == 0


@pytest.mark.skipif(not HAS_GUI, reason="GUI dependencies not installed")
class TestGuiIntegration:
    """End-to-end headless testing of CleanYourWechatApp state transitions."""

    @classmethod
    def setup_class(cls):
        if not HAS_GUI:
            pytest.skip("GUI dependencies not installed")
        try:
            cls.root = ctk.CTk()
            cls.root.withdraw()
            cls._patch_mb = patch('clean_wechat_gui.messagebox')
            cls._patch_mb.start()
            cls.app = CleanYourWechatApp(cls.root, auto_init=False)
        except Exception as e:
            pytest.skip(f"Display not available or CTk init failed: {e}")

    @classmethod
    def teardown_class(cls):
        try:
            if hasattr(cls, '_patch_mb'):
                cls._patch_mb.stop()
            if hasattr(cls, 'app'):
                cls.app._closing = True
            if hasattr(cls, 'root'):
                cls.root.destroy()
        except Exception:
            pass

    def test_app_initial_widget_hierarchy(self):
        app = self.app
        assert app.header is not None
        assert app.hero_card is not None
        assert app.card_junk is not None
        assert app.card_dedup is not None
        assert app.card_large is not None
        assert app.dedup_min_box.get() == DEDUP_SIZE_CHOICES[1][0]
        # 默认 30 天: 实测 90 天阈值在典型机器上匹配 0 个文件, 首次体验看不到价值
        assert app.large_days_box.get() == LARGE_DAYS_CHOICES[0][0]
        assert app.large_size_box.get() == LARGE_SIZE_CHOICES[1][0]
        assert len(app.large_type_vars) == 3
        assert [k for k, _ in app.large_type_vars] == ['video', 'archive', 'document']
        # 默认勾选安全推荐项 (视频 / 压缩包), 办公文档默认保护不勾选
        defaults = {k: var.get() for k, var in app.large_type_vars}
        assert defaults['document'] is False, '办公文档必须默认受保护'
        assert defaults['video'] is True and defaults['archive'] is True, '默认应勾选可安全清理项'

    def test_toggle_drawers(self):
        app = self.app
        # Initially unpacked
        assert not bool(app.cl_adv.winfo_manager())
        assert not bool(app.cd_adv.winfo_manager())

        # Toggle Card 3
        app._toggle_large_adv()
        assert bool(app.cl_adv.winfo_manager())
        assert app.cl_adv_btn.cget('text') == '筛选条件 ▴'
        app._toggle_large_adv()
        assert not bool(app.cl_adv.winfo_manager())
        assert app.cl_adv_btn.cget('text') == '筛选条件 ▾'

        # Toggle Card 2
        app._toggle_dedup_adv()
        assert bool(app.cd_adv.winfo_manager())
        assert app.cd_adv_btn.cget('text') == '选项 ▴'
        app._toggle_dedup_adv()
        assert not bool(app.cd_adv.winfo_manager())
        assert app.cd_adv_btn.cget('text') == '选项 ▾'

    def test_reclaimable_sum_calculation(self):
        app = self.app
        app.junk_bytes = 1000
        app.dedup_bytes = 2000
        app.large_files_meta = {
            '0': {'size': 3000, 'included': True},
            '1': {'size': 4000, 'included': False},
        }

        # All 3 enabled
        app.junk_switch_var.set(True)
        app.dedup_switch_var.set(True)
        app.large_switch_var.set(True)
        app._update_reclaimable_sum()
        assert app.btn_one_key.cget('state') == 'normal'
        assert format_bytes(6000) in app.btn_one_key.cget('text')
        assert format_bytes(6000) in app.reclaimable_label.cget('text')

        # Disable junk switch
        app.junk_switch_var.set(False)
        app._update_reclaimable_sum()
        assert format_bytes(5000) in app.btn_one_key.cget('text')

        # Disable all
        app.dedup_switch_var.set(False)
        app.large_switch_var.set(False)
        app._update_reclaimable_sum()
        assert app.btn_one_key.cget('state') == 'disabled'
        assert app.reclaimable_label.cget('text') == '当前没有选中可释放项'

    def test_after_diagnose_state_hydration(self):
        app = self.app
        mock_cats = {
            'cache': ScanCategory(name='缓存', description='缓存', path=Path('/a'), files=[(Path('/a'), 100, 1.0)], total_bytes=100),
            'video': ScanCategory(name='视频', description='视频', path=Path('/b'), files=[(Path('/b'), 500, 1.0)], total_bytes=500),
        }
        mock_junk = [(Path('/a'), 100, 1.0)]
        mock_dup = [
            DuplicateGroup(file_hash='h1', file_size=200,
                           files=[Path('/o'), Path('/d1'), Path('/d2')],
                           saving_bytes=400, wasted_count=2)
        ]
        mock_large_res = SlimResult(freed_count=2, freed_bytes=600, protected_count=0, protected_bytes=0, affected_files=[(Path('/f1'), 400, 1.0), (Path('/f2'), 200, 1.0)])

        payload = (mock_cats, mock_junk, mock_dup, mock_large_res, (90, 10 * 1024 * 1024, ['video', 'file']))
        app._after_diagnose(payload)

        assert app.junk_bytes == 100
        assert app.dedup_bytes == 400  # 2 duplicates * 200 bytes
        assert app.large_bytes == 600
        assert len(app.large_files) == 2
        assert app.large_files[0][1] == 400  # Sorted descending
        assert len(app.large_files_meta) == 2
        assert app.large_files_meta['0']['included'] is True

    def test_queue_polling_and_cancel_handling(self):
        app = self.app
        # Progress message
        app.queue.put(('progress', None, '正在清理缓存…'))
        app._poll_queue()
        assert app.status_var.get() == '正在清理缓存…'

        # Cancelled exception
        app.queue.put(('err', None, RuntimeError('Operation cancelled by user')))
        app._poll_queue()
        assert app.status_var.get() == '已取消'
        assert not app._is_busy

        # Normal completion with callback
        called = []
        app.queue.put(('ok', lambda val: called.append(val), 'done_result'))
        app._poll_queue()
        assert called == ['done_result']
        assert app.status_var.get() == '就绪'

    def test_filter_change_debouncer(self):
        app = self.app
        app._is_busy = False
        app._filters_job = None
        app._on_filters_changed()
        assert app._filters_job is not None
        assert not app._filters_dirty

        # When busy, dirty flag is raised
        app._is_busy = True
        app._on_filters_changed()
        assert app._filters_dirty is True
        app._is_busy = False

    def test_large_files_drawer_opening_and_closing(self):
        app = self.app
        # Case 1: empty large_files shows alert
        app.large_files = []
        with patch('clean_wechat_gui.messagebox.showinfo') as mock_info:
            app._open_large_files_drawer()
            mock_info.assert_called_once()

        # Case 2: populated large_files opens modal
        app.large_files = [(Path('/tmp/test_vid.mp4'), 20 * 1024 * 1024, 1.0)]
        app.large_files_meta = {
            '0': {'path': Path('/tmp/test_vid.mp4'), 'size': 20 * 1024 * 1024, 'mtime': 1.0, 'included': True}
        }
        with patch.object(ctk.CTkToplevel, 'grab_set'):
            app._open_large_files_drawer()
            # Find the drawer toplevel child
            drawer = [w for w in app.root.winfo_children() if isinstance(w, ctk.CTkToplevel)][0]
            assert drawer.title() == '历史大文件核对清单'
            drawer.destroy()

    def test_whitelist_modal_opening_and_closing(self):
        app = self.app
        with patch.object(ctk.CTkToplevel, 'grab_set'):
            app._open_whitelist_modal()
            modal = [w for w in app.root.winfo_children() if isinstance(w, ctk.CTkToplevel)][0]
            assert modal.title() == '防删白名单守护'
            modal.destroy()

    def test_after_diagnose_empty_types_safe_label(self):
        app = self.app
        mock_cats = {}
        mock_junk = []
        mock_dup = []
        mock_large_res = SlimResult(0, 0, 0, 0, [])
        payload = (mock_cats, mock_junk, mock_dup, mock_large_res, (90, 10 * 1024 * 1024, []))
        app._after_diagnose(payload)
        assert '未勾选任何文件类型' in app.cl_desc_label.cget('text')
        assert '100% 绝对保护状态' in app.cl_desc_label.cget('text')
        assert app.large_bytes == 0
        assert len(app.large_files) == 0

    def test_classify_file_types(self):
        from engine.cleaner import classify_file_type
        assert classify_file_type(Path('demo.mp4')) == 'video'
        assert classify_file_type(Path('demo.mov')) == 'video'
        assert classify_file_type(Path('archive.zip')) == 'archive'
        assert classify_file_type(Path('installer.dmg')) == 'archive'
        assert classify_file_type(Path('contract.pdf')) == 'document'
        assert classify_file_type(Path('report.docx')) == 'document'
        assert classify_file_type(Path('sheet.xlsx')) == 'document'
        assert classify_file_type(Path('unknown.bin')) == 'other'


def test_gui_type_keys_are_recognized_by_engine():
    import tempfile
    tmp_path = Path(tempfile.mkdtemp())
    """GUI 的文件类型 key 必须能被 execute_slimming 实际识别并匹配到文件。

    背景（一次误判的纠正）: 曾以为 GUI 的 archive/document 是无效 key
    （因为 scan_account 的目录分类里没有它们），并据此"修复"成 file/attach。
    实际引擎有**两层匹配**:
      1) 目录分类命中 (cat_key in selected_types)
      2) 未命中时按内容语义分类再匹配一次 (classify_file_type → video/archive/document)
    因此 archive/document 是有效的精确筛选（可单独清理压缩包而不动文档）。

    本测试用行为验证取代静态集合比对: 为每个 key 构造对应类型的文件，
    断言引擎能匹配到它 —— 若引擎将来调整分类体系, 这里会立刻失败。
    """
    from engine.cleaner import execute_slimming
    from engine.scanner import discover_accounts, scan_account

    # 构造一个最小可用账号: 30 天内、大于 1KB 的 mp4 / zip / pdf
    acc_dir = tmp_path / 'xwechat_files' / 'wxid_type_probe'
    for sub in ('db_storage', 'msg/video/2026-09', 'msg/file/2026-09', 'cache'):
        (acc_dir / sub).mkdir(parents=True, exist_ok=True)
    (acc_dir / 'msg' / 'video' / '2026-09' / 'clip.mp4').write_bytes(b'0' * 4096)
    (acc_dir / 'msg' / 'file' / '2026-09' / 'bundle.zip').write_bytes(b'0' * 4096)
    (acc_dir / 'msg' / 'file' / '2026-09' / 'contract.pdf').write_bytes(b'0' * 4096)
    (acc_dir / 'cache' / 'blob.bin').write_bytes(b'0' * 4096)

    accounts = discover_accounts(custom_path=acc_dir)
    assert accounts, '未能构造测试账号'
    acc = accounts[0]
    cats = scan_account(acc)

    expectations = {'video': 'clip.mp4', 'archive': 'bundle.zip', 'document': 'contract.pdf'}
    for key, sample in expectations.items():
        res = execute_slimming(acc, cats, days=0, min_size_bytes=0,
                               selected_types=[key], dry_run=True)
        names = [p.name for p, _s, _m in res.affected_files]
        assert sample in names, (
            f'类型 key "{key}" 未被引擎识别 (期望匹配 {sample}, 实际匹配 {names}); '
            f'说明该 key 与引擎的分类体系脱节, 用户勾选后将永远清理不到文件'
        )


def test_gui_type_choices_cover_safe_defaults():
    """默认勾选必须是"可安全清理"的项: 视频与压缩包, 办公文档默认不勾。"""
    import re
    gui_src = (_REPO_ROOT / 'clean_wechat_gui.py').read_text(encoding='utf-8')
    m = re.search(r'for label, key, default_on in \((.*?)\):', gui_src, re.S)
    assert m is not None, '未找到 GUI 类型勾选项定义 (结构已变, 请更新本测试)'
    pairs = re.findall(r"\('([^']+)',\s*'(\w+)',\s*(True|False)\)", m.group(1))
    assert pairs, '未解析出类型勾选项'
    defaults = {key: (flag == 'True') for _label, key, flag in pairs}
    assert defaults.get('document') is False, '办公文档必须默认不勾选 (保护用户资产)'
    assert any(defaults.get(k) for k in ('video', 'archive')), '应默认勾选可安全清理的项'
