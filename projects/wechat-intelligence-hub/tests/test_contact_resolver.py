import sqlite3
import tempfile
import unittest
from pathlib import Path
import shutil

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.contact_resolver import ContactResolver, ContactInfo


class TestContactResolver(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.db_dir = self.test_dir / "db_storage"
        self.db_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_contact_info_display_name(self):
        c1 = ContactInfo(wxid="wxid_1", remark="老婆", nickname="小仙女")
        self.assertEqual(c1.display_name, "老婆")
        self.assertIn("备注: 老婆", c1.to_formatted_str())
        self.assertIn("昵称: 小仙女", c1.to_formatted_str())

        c2 = ContactInfo(wxid="wxid_2", nickname="张三")
        self.assertEqual(c2.display_name, "张三")

        c3 = ContactInfo(wxid="wxid_3")
        self.assertEqual(c3.display_name, "wxid_3")

    def test_resolve_from_sqlite_database(self):
        db_path = self.db_dir / "contact.db"
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE Contact (m_nsUsrName TEXT, m_nsRemark TEXT, m_nsNickName TEXT)")
        cur.execute("INSERT INTO Contact VALUES ('wxid_boss', '老张董事长', '飞翔的鹰')")
        cur.execute("INSERT INTO Contact VALUES ('wxid_wife', '老婆', '晴天')")
        conn.commit()
        conn.close()

        resolver = ContactResolver(root_or_db_path=self.test_dir)
        info_boss = resolver.resolve("wxid_boss")
        self.assertIsNotNone(info_boss)
        self.assertEqual(info_boss.remark, "老张董事长")
        self.assertEqual(info_boss.nickname, "飞翔的鹰")
        self.assertEqual(info_boss.display_name, "老张董事长")

        # Fallback for unknown wxid
        self.assertIsNone(resolver.resolve("wxid_unknown"))
        self.assertEqual(resolver.get_display_name("wxid_unknown", fallback_name="保底名称"), "保底名称")

    def test_encrypted_or_corrupted_database_resilience(self):
        db_path = self.db_dir / "contact.db"
        # Write corrupted/binary blob simulating encrypted SQLCipher page
        db_path.write_bytes(b"\x00\xff\xee\xdd" * 512)

        resolver = ContactResolver(root_or_db_path=self.test_dir)
        # Must fail gracefully and return None
        info = resolver.resolve("wxid_any")
        self.assertIsNone(info)

    def test_nonexistent_and_empty_path(self):
        resolver = ContactResolver(root_or_db_path=Path("/non/existent/path"))
        self.assertIsNone(resolver.resolve("wxid_abc"))
        self.assertEqual(resolver.batch_resolve(["wxid_abc"]), {})


if __name__ == "__main__":
    unittest.main()
