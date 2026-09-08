#!/usr/bin/env python3
"""状态与使用指标管理器测试."""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.state import StateManager


class TestStateManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.state_file = self.temp_dir / "state.json"
        self.mgr = StateManager(self.state_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_state_defaults(self):
        self.assertEqual(self.mgr.total_runs, 0)
        self.assertEqual(self.mgr.total_freed_bytes, 0)
        self.assertIsNone(self.mgr.nps_score)

    def test_record_scan(self):
        self.mgr.record_scan()
        self.assertEqual(self.mgr.total_runs, 1)
        self.assertEqual(self.mgr.total_scans, 1)

    def test_record_clean(self):
        self.mgr.record_clean(freed_count=5, freed_bytes=1024*1024, protected_count=2, protected_bytes=2048)
        self.assertEqual(self.mgr.total_runs, 1)
        self.assertEqual(self.mgr.total_cleans, 1)
        self.assertEqual(self.mgr.total_freed_bytes, 1024*1024)
        self.assertEqual(self.mgr.total_protected_bytes, 2048)
        self.assertEqual(len(self.mgr.history), 1)

    def test_record_dedup(self):
        self.mgr.record_dedup(processed_count=3, freed_bytes=5000)
        self.assertEqual(self.mgr.total_dedups, 1)
        self.assertEqual(self.mgr.total_freed_bytes, 5000)

    def test_persistence(self):
        self.mgr.record_clean(1, 200)
        mgr2 = StateManager(self.state_file)
        self.assertEqual(mgr2.total_runs, 1)
        self.assertEqual(mgr2.total_freed_bytes, 200)

    def test_save_then_reload_consistency(self):
        """save 后重新 load 数据应完全一致（含累计统计与历史）."""
        self.mgr.record_clean(freed_count=3, freed_bytes=1024 * 1024, protected_count=1, protected_bytes=512)
        self.mgr.record_dedup(processed_count=2, freed_bytes=2048)
        self.mgr.record_scan()
        mgr2 = StateManager(self.state_file)
        self.assertEqual(mgr2.total_runs, 3)
        self.assertEqual(mgr2.total_scans, 1)
        self.assertEqual(mgr2.total_cleans, 1)
        self.assertEqual(mgr2.total_dedups, 1)
        self.assertEqual(mgr2.total_freed_bytes, 1024 * 1024 + 2048)
        self.assertEqual(mgr2.total_protected_bytes, 512)
        self.assertEqual(len(mgr2.history), 2)

    def test_corrupted_state_rebuild_and_backup(self):
        """向 state 文件写入垃圾字节后 load 不抛异常，且重置默认值并生成 .corrupted 备份."""
        # 先写入一份正常数据
        self.mgr.record_clean(1, 999)
        self.assertTrue(self.state_file.exists())
        # 用垃圾字节覆盖（模拟写中途断电/崩溃产生的截断 JSON）
        self.state_file.write_bytes(b"\x00\x01corrupted garbage{{{ not json")
        # load 不应抛异常
        mgr2 = StateManager(self.state_file)
        self.assertEqual(mgr2.total_freed_bytes, 0, "损坏后应从默认值重建")
        self.assertEqual(len(mgr2.history), 0)
        # 应生成 .corrupted 备份（保留现场，不删除）
        backups = list(self.temp_dir.glob("state.json.corrupted-*"))
        self.assertEqual(len(backups), 1, "应生成一份 .corrupted 备份")
        self.assertIn(b"corrupted", backups[0].read_bytes())

    def test_nps_trigger_logic(self):
        self.assertFalse(self.mgr.should_trigger_nps())
        for _ in range(10):
            self.mgr.record_scan()
        self.assertTrue(self.mgr.should_trigger_nps())
        self.mgr.record_nps(9)
        self.assertFalse(self.mgr.should_trigger_nps())
        self.assertEqual(self.mgr.nps_score, 9)


if __name__ == "__main__":
    unittest.main()
