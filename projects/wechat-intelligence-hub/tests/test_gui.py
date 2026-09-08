"""Unit tests for clean_wechat_gui helper functions, asset loader, and filter choices."""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from clean_wechat_gui import (  # noqa: E402
    get_asset_path,
    LARGE_DAYS_CHOICES,
    LARGE_SIZE_CHOICES,
    DEDUP_SIZE_CHOICES,
    CleanYourWechatApp,
    execute_files_to_trash,
)
from engine.common import parse_size_str  # noqa: E402
from engine.whitelist import WhiteListManager  # noqa: E402


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
