"""Phase 2: WhiteList System Tests.

测试范围:
1. Contact 数据模型
2. WhiteListConfig 配置管理
3. WhiteListManager 核心功能 (CRUD)
4. 保护级别逻辑
5. 群聊自动保护机制
6. 边界条件和异常处理

运行方式:
    pytest projects/wechat-intelligence-hub/tests/test_whitelist.py -v
    python3 -m unittest discover projects/wechat-intelligence-hub/tests/ -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import List

# 导入被测模块
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wechat_intelligence_hub.engine.whitelist import (
    ProtectionLevel,
    Contact,
    WhiteListConfig,
    WhiteListManager,
)


class TestProtectionLevel(unittest.TestCase):
    """测试枚举类型 ProtectionLevel."""
    
    def test_enum_values(self):
        """验证枚举值正确性."""
        self.assertEqual(ProtectionLevel.ABSOLUTE.value, "absolute")
        self.assertEqual(ProtectionLevel.FILES_ONLY.value, "files-only")
        
    def test_from_invalid_value(self):
        """验证无效值的处理."""
        with self.assertRaises(ValueError):
            ProtectionLevel("invalid-value")


class TestContact(unittest.TestCase):
    """测试 Contact 数据模型."""
    
    def test_contact_creation(self):
        """测试联系人对象的正常创建."""
        contact = Contact(
            name="张总",
            wxid="wxid_zhang123",
            tags=["client", "vip"],
            protection=ProtectionLevel.FILES_ONLY
        )
        
        self.assertEqual(contact.name, "张总")
        self.assertEqual(contact.wxid, "wxid_zhang123")
        self.assertEqual(contact.tags, ["client", "vip"])
        self.assertEqual(contact.protection, ProtectionLevel.FILES_ONLY)
        
    def test_contact_from_dict_absolute(self):
        """测试从字典创建 ABSOLUTE 保护级别的联系人."""
        data = {
            "name": "老婆",
            "wxid": "wxid_wife999",
            "tags": ["family"],
            "protection": "absolute"
        }
        
        contact = Contact.from_dict(data)
        
        self.assertEqual(contact.name, "老婆")
        self.assertEqual(contact.protection, ProtectionLevel.ABSOLUTE)
        
    def test_contact_from_dict_files_only_default(self):
        """测试从字典创建 FILES_ONLY 默认保护级别的联系人."""
        # 不指定 protection 字段时，应该使用默认值
        data = {
            "name": "李律师",
            "wxid": "wxid_li456",
            "tags": ["legal"]
        }
        
        contact = Contact.from_dict(data)
        
        self.assertEqual(contact.protection, ProtectionLevel.FILES_ONLY)
        
    def test_contact_with_empty_tags(self):
        """测试没有标签的联系人."""
        contact = Contact(
            name="陌生人",
            wxid="wxid_none789",
            tags=[],
            protection=ProtectionLevel.FILES_ONLY
        )
        
        self.assertEqual(contact.tags, [])


class TestWhiteListConfig(unittest.TestCase):
    """测试 WhiteListConfig 配置类."""
    
    def test_empty_config_to_dict(self):
        """测试空配置的序列化."""
        config = WhiteListConfig()
        
        result = config.to_dict()
        
        self.assertEqual(result["protected_contacts"], [])
        self.assertEqual(result["auto_protected_groups"], [])
        
    def test_config_with_data_to_dict(self):
        """测试包含数据的配置序列化."""
        contacts = [
            Contact(
                name="家人",
                wxid="wxid_family1",
                tags=["family"],
                protection=ProtectionLevel.ABSOLUTE
            )
        ]
        
        groups = ["家庭群"]
        config = WhiteListConfig(
            protected_contacts=contacts,
            auto_protected_groups=groups
        )
        
        result = config.to_dict()
        
        self.assertEqual(len(result["protected_contacts"]), 1)
        self.assertEqual(len(result["auto_protected_groups"]), 1)
        
        self.assertEqual(result["auto_protected_groups"][0], "家庭群")
        
    def test_preserve_unicode_in_dict(self):
        """测试中文字符在序列化的保存."""
        config = WhiteListConfig(
            protected_contacts=[
                Contact(
                    name="老婆❤️",
                    wxid="wxid_love",
                    tags=["家人"],
                    protection=ProtectionLevel.ABSOLUTE
                )
            ]
        )
        
        result = config.to_dict()
        
        self.assertIn("老婆", result["protected_contacts"][0]["name"])


class TestWhiteListManager(unittest.TestCase):
    """测试 WhiteListManager 核心功能."""
    
    def setUp(self):
        """每个测试前的准备工作."""
        # 创建临时目录和配置文件路径
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "test_whitelist.yaml"
        
        # 初始化管理器
        self.manager = WhiteListManager(config_path=self.config_path)
        
    def tearDown(self):
        """每个测试后的清理工作."""
        # 删除临时文件
        if self.config_path.exists():
            self.config_path.unlink()
            
        # 清理临时目录
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        
    def test_init_default_path(self):
        """测试默认配置文件路径."""
        manager = WhiteListManager()
        
        expected_path = Path.home() / '.wechat_slim' / 'config' / 'whitelist.yaml'
        
        self.assertEqual(manager.config_path, expected_path)
        
    def test_load_nonexistent_file_returns_empty_config(self):
        """测试加载不存在的文件返回空配置."""
        config = self.manager.load()
        
        self.assertIsNotNone(config)
        self.assertEqual(len(config.protected_contacts), 0)
        self.assertEqual(len(config.auto_protected_groups), 0)
        
    def test_save_creates_parent_directory(self):
        """测试保存时自动创建父目录."""
        nested_path = Path(self.temp_dir) / "nested" / "deeper" / "whitelist.yaml"
        manager = WhiteListManager(config_path=nested_path)
        
        config = WhiteListConfig()
        manager.save(config)
        
        self.assertTrue(nested_path.exists())
        
    def test_add_new_contact(self):
        """测试添加新联系人."""
        config = self.manager.load()
        
        new_contact = Contact(
            name="张总",
            wxid="wxid_zhang123",
            tags=["client"],
            protection=ProtectionLevel.FILES_ONLY
        )
        
        config.protected_contacts.append(new_contact)
        self.manager.save(config)
        
        # 重新加载验证
        reloaded = self.manager.load()
        
        self.assertEqual(len(reloaded.protected_contacts), 1)
        self.assertEqual(reloaded.protected_contacts[0].name, "张总")
        
    def test_update_existing_contact(self):
        """测试更新现有联系人."""
        # 先添加一个联系人
        initial_contact = Contact(
            name="旧名字",
            wxid="wxid_update123",
            tags=["old"],
            protection=ProtectionLevel.FILES_ONLY
        )
        self.manager.load().protected_contacts.append(initial_contact)
        self.manager.save()
        
        # 再更新同一个 wxid 的联系人
        updated_contact = Contact(
            name="新名字",
            wxid="wxid_update123",
            tags=["new", "updated"],
            protection=ProtectionLevel.ABSOLUTE
        )
        self.manager.add_contact(
            name="新名字",
            wxid="wxid_update123",
            tags=["new", "updated"],
            protection=ProtectionLevel.ABSOLUTE
        )
        
        # 验证数量没变 (还是 1 个)
        config = self.manager.load()
        self.assertEqual(len(config.protected_contacts), 1)
        
        # 验证名字已更新
        self.assertEqual(config.protected_contacts[0].name, "新名字")
        
    def test_remove_contact(self):
        """测试删除联系人."""
        # 添加两个联系人
        for i in range(2):
            contact = Contact(
                name=f"联系人{i}",
                wxid=f"wxid_remove{i}",
                tags=["test"],
                protection=ProtectionLevel.FILES_ONLY
            )
            self.manager.add_contact(
                name=f"联系人{i}",
                wxid=f"wxid_remove{i}",
                tags=["test"],
                protection=ProtectionLevel.FILES_ONLY
            )
        
        # 删除其中一个
        wxid_to_remove = "wxid_remove0"
        result = self.manager.remove_contact(wxid_to_remove)
        
        self.assertTrue(result)
        
        # 验证剩余 1 个
        config = self.manager.load()
        self.assertEqual(len(config.protected_contacts), 1)
        self.assertNotIn("wxid_remove0", [c.wxid for c in config.protected_contacts])
        
    def test_remove_nonexistent_contact(self):
        """测试删除不存在的联系人返回 False."""
        result = self.manager.remove_contact("wxid_not_exist_xyz")
        
        self.assertFalse(result)
        
    def test_is_protected_by_wxid(self):
        """测试通过 wxid 查询是否受保护."""
        contact = Contact(
            name="老婆",
            wxid="wxid_wife123",
            tags=["family"],
            protection=ProtectionLevel.ABSOLUTE
        )
        self.manager.load().protected_contacts.append(contact)
        self.manager.save()
        
        # 查询存在的 wxid
        self.assertTrue(self.manager.is_protected("wxid_wife123"))
        
        # 查询不存在的 wxid
        self.assertFalse(self.manager.is_protected("wxid_notexist"))
        
    def test_is_protected_by_name(self):
        """测试通过名称查询是否受保护."""
        contact = Contact(
            name="张总",
            wxid="wxid_zhang456",
            tags=["client"],
            protection=ProtectionLevel.FILES_ONLY
        )
        self.manager.load().protected_contacts.append(contact)
        self.manager.save()
        
        # 查询存在的名称
        self.assertTrue(self.manager.is_protected("张总"))
        
        # 查询不存在的名称
        self.assertFalse(self.manager.is_protected("李总"))
        
    def test_get_protection_level(self):
        """测试获取保护级别."""
        contact = Contact(
            name="老婆",
            wxid="wxid_wife789",
            tags=["family"],
            protection=ProtectionLevel.ABSOLUTE
        )
        self.manager.load().protected_contacts.append(contact)
        self.manager.save()
        
        level = self.manager.get_protection_level("wxid_wife789")
        
        self.assertEqual(level, ProtectionLevel.ABSOLUTE)
        
    def test_is_group_protected(self):
        """测试群聊保护检测."""
        config = WhiteListConfig(
            auto_protected_groups=["公司高层会议", "家庭群"]
        )
        self.manager.save(config)
        
        # 完全匹配
        self.assertTrue(self.manager.is_group_protected("公司高层会议"))
        
        # 前缀匹配
        self.assertTrue(self.manager.is_group_protected("家庭群 - 我们一家"))
        
        # 不匹配
        self.assertFalse(self.manager.is_group_protected("行业交流群"))
        
    def test_list_contacts_returns_copy(self):
        """测试 list 返回的是副本."""
        original_count = len(self.manager.list_contacts())
        
        # 尝试修改返回列表
        contacts = self.manager.list_contacts()
        contacts.clear()  # 清空列表
        
        # 重新获取应该还是原始数量
        fresh_list = self.manager.list_contacts()
        self.assertEqual(len(fresh_list), original_count)
        
    def test_complex_scenario_multiple_contacts(self):
        """复杂场景：多个不同类型联系人."""
        # 添加各种类型的联系人
        contacts_data = [
            ("老婆", "wxid_wife", ProtectionLevel.ABSOLUTE),
            ("孩子", "wxid_child", ProtectionLevel.ABSOLUTE),
            ("张总", "wxid_zhang", ProtectionLevel.FILES_ONLY),
            ("李律师", "wxid_li", ProtectionLevel.FILES_ONLY),
        ]
        
        for name, wxid, level in contacts_data:
            contact = Contact(
                name=name,
                wxid=wxid,
                tags=["important"],
                protection=level
            )
            self.manager.add_contact(
                name=name,
                wxid=wxid,
                tags=["important"],
                protection=level
            )
        
        # 验证所有联系人都被加载
        config = self.manager.load()
        self.assertEqual(len(config.protected_contacts), 4)
        
        # 验证不同类型的保护级别正确
        wife_level = self.manager.get_protection_level("wxid_wife")
        zhang_level = self.manager.get_protection_level("wxid_zhang")
        
        self.assertEqual(wife_level, ProtectionLevel.ABSOLUTE)
        self.assertEqual(zhang_level, ProtectionLevel.FILES_ONLY)


class TestEdgeCasesAndExceptions(unittest.TestCase):
    """测试边界条件和异常情况."""
    
    def setUp(self):
        """创建临时环境."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "edge_case.yaml"
        
    def tearDown(self):
        """清理临时文件."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        
    def test_empty_wxid_string(self):
        """测试空 wxid 字符串的处理."""
        contact = Contact(
            name="无名氏",
            wxid="",
            tags=[],
            protection=ProtectionLevel.FILES_ONLY
        )
        
        # 不应该抛出异常
        self.assertEqual(contact.wxid, "")
        
    def test_special_characters_in_name(self):
        """测试特殊字符名称的处理."""
        special_names = [
            "张总❤️",
            "李老板⭐️",
            "王经理 👤",
            "赵先生 🎯"
        ]
        
        for name in special_names:
            contact = Contact(
                name=name,
                wxid=f"wxid_{name.lower()}",
                tags=["emoji"],
                protection=ProtectionLevel.FILES_ONLY
            )
            # 应该能正常创建
            self.assertIn(name, contact.name)
            
    def test_duplicate_wxid_prevention(self):
        """测试重复 wxid 的防止机制."""
        # 先添加
        contact1 = Contact(
            name="初始名字",
            wxid="wxid_dupe_test",
            tags=["original"],
            protection=ProtectionLevel.FILES_ONLY
        )
        self.manager = WhiteListManager(config_path=self.config_path)
        self.manager.load().protected_contacts.append(contact1)
        self.manager.save()
        
        # 再用相同 wxid 添加不同名字
        contact2 = Contact(
            name="更新名字",
            wxid="wxid_dupe_test",
            tags=["updated"],
            protection=ProtectionLevel.ABSOLUTE
        )
        self.manager.add_contact(
            name="更新名字",
            wxid="wxid_dupe_test",
            tags=["updated"],
            protection=ProtectionLevel.ABSOLUTE
        )
        
        # 验证只有 1 条记录且名字是更新的
        config = self.manager.load()
        self.assertEqual(len(config.protected_contacts), 1)
        self.assertEqual(config.protected_contacts[0].name, "更新名字")
        
    def test_very_long_fields(self):
        """测试超长字段的处理."""
        long_name = "A" * 1000
        long_wxid = "wxid_" + "x" * 500
        
        contact = Contact(
            name=long_name,
            wxid=long_wxid,
            tags=["test"],
            protection=ProtectionLevel.FILES_ONLY
        )
        
        # 应该能正常保存和加载
        self.manager = WhiteListManager(config_path=self.config_path)
        self.manager.load().protected_contacts.append(contact)
        self.manager.save()
        
        loaded = self.manager.load()
        self.assertEqual(len(loaded.protected_contacts[0].name), 1000)


if __name__ == '__main__':
    # 运行所有测试
    unittest.main(verbosity=2)
