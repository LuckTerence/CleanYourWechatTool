#!/usr/bin/env python3
"""WeChat WhiteList Manager - 核心联系人与重要会话防删白名单系统.

负责核心人脉（家人、重要客户、重点工作群）的标签化管理与文件保护规则，
支持 Contact 数据模型、ProtectionLevel 枚举、WhiteListRule 规则及群聊自动保护，
提供 100% 零依赖标准库实现与可选 YAML 支持。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import yaml
except ImportError:
    yaml = None


class ProtectionLevel(Enum):
    """保护级别枚举 (兼容 Contact 模型)."""
    ABSOLUTE = "absolute"      # 所有文件都保护
    FILES_ONLY = "files-only"  # 只保护文件，不保护视频


@dataclass
class Contact:
    """联系人数据模型."""
    name: str
    wxid: str
    tags: List[str] = field(default_factory=list)
    protection: ProtectionLevel = ProtectionLevel.FILES_ONLY

    @classmethod
    def from_dict(cls, data: dict) -> Contact:
        prot_val = data.get('protection', 'files-only')
        if isinstance(prot_val, ProtectionLevel):
            prot_enum = prot_val
        else:
            try:
                prot_enum = ProtectionLevel(prot_val)
            except ValueError:
                prot_enum = ProtectionLevel.FILES_ONLY
        return cls(
            name=data.get('name', ''),
            wxid=data.get('wxid', ''),
            tags=data.get('tags', []),
            protection=prot_enum,
        )


@dataclass
class WhiteListConfig:
    """白名单配置 (兼容 Contact/Group 模型)."""
    protected_contacts: List[Contact] = field(default_factory=list)
    auto_protected_groups: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'protected_contacts': [
                {
                    'name': c.name,
                    'wxid': c.wxid,
                    'tags': c.tags,
                    'protection': c.protection.value if isinstance(c.protection, ProtectionLevel) else str(c.protection),
                }
                for c in self.protected_contacts
            ],
            'auto_protected_groups': self.auto_protected_groups,
        }


@dataclass
class WhiteListRule:
    """白名单规则项 (规则模式)."""
    name: str                                           # 联系人或群聊名称 (如: "老婆", "重要客户A")
    wxid: str                                           # 微信 ID 或群聊 ID (如: "wxid_xxx", "xxx@chatroom")
    protect: str = "absolute"                           # 保护级别: "absolute" 或 "retain_days"
    keywords: List[str] = field(default_factory=list)   # 文件名关键词匹配 (如: ["合同", "宝宝", "结婚"])
    retain_days: int = 0                                # 当 protect 为 retain_days 时的有效天数 (0 为不限制)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WhiteListRule:
        return cls(
            name=data.get("name", ""),
            wxid=data.get("wxid", ""),
            protect=data.get("protect", "absolute"),
            keywords=data.get("keywords", []),
            retain_days=int(data.get("retain_days", 0)),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


class WhiteListManager:
    """统一白名单管理器，同时兼容 WhiteListRule 规则引擎与 Contact/Group 配置模型."""

    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        if config_path:
            self.config_path = Path(config_path).expanduser().resolve()
        else:
            self.config_path = Path.home() / ".wechat_slim" / "config" / "whitelist.yaml"

        self.rules: Dict[str, WhiteListRule] = {}
        self.name_index: Dict[str, str] = {}
        self._config: Optional[WhiteListConfig] = None
        self.load()

    def load(self) -> WhiteListConfig:
        """从配置文件加载白名单 (支持 JSON 与 YAML)."""
        self.rules.clear()
        self.name_index.clear()

        target_file = self.config_path
        if not target_file.exists():
            fallback_json = Path.home() / ".wechat_slim_whitelist.json"
            if fallback_json.exists():
                target_file = fallback_json
            else:
                self._config = WhiteListConfig()
                return self._config

        try:
            content = target_file.read_text(encoding="utf-8")
            data: Dict[str, Any] = {}

            if (self.config_path.suffix in [".yaml", ".yml"]) and yaml is not None:
                data = yaml.safe_load(content) or {}
            else:
                try:
                    data = json.loads(content)
                except Exception:
                    if yaml is not None:
                        data = yaml.safe_load(content) or {}

            # 1. 解析 WhiteListRule 规则列表
            rules_list = data.get("rules", [])
            for r_data in rules_list:
                rule = WhiteListRule.from_dict(r_data)
                key = rule.wxid.strip().lower()
                if key:
                    self.rules[key] = rule
                    if rule.name:
                        self.name_index[rule.name.strip().lower()] = key

            # 2. 解析 Contact/Group 配置列表
            contacts = [Contact.from_dict(c) for c in data.get("protected_contacts", [])]
            groups = data.get("auto_protected_groups", [])
            self._config = WhiteListConfig(protected_contacts=contacts, auto_protected_groups=groups)

            # 将 contacts 同步映射到 rules 中供统一查询
            for c in contacts:
                prot_str = "absolute" if c.protection == ProtectionLevel.ABSOLUTE else "files-only"
                key = c.wxid.strip().lower()
                if key not in self.rules:
                    rule = WhiteListRule(name=c.name, wxid=c.wxid, protect=prot_str, keywords=c.tags)
                    self.rules[key] = rule
                    if c.name:
                        self.name_index[c.name.strip().lower()] = key

            return self._config
        except Exception:
            self._config = WhiteListConfig()
            self.rules = {}
            self.name_index = {}
            return self._config

    def save(self, config: Optional[WhiteListConfig] = None) -> None:
        """持久化保存白名单到文件."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            if config is not None:
                self._config = config

            if self._config is not None and (self._config.protected_contacts or self._config.auto_protected_groups):
                # Contact 配置存储模式
                out_data = self._config.to_dict()
                if self.rules:
                    out_data["rules"] = [r.to_dict() for r in self.rules.values()]

                if self.config_path.suffix in [".yaml", ".yml"] and yaml is not None:
                    with open(self.config_path, "w", encoding="utf-8") as f:
                        yaml.dump(out_data, f, allow_unicode=True)
                else:
                    with open(self.config_path, "w", encoding="utf-8") as f:
                        json.dump(out_data, f, ensure_ascii=False, indent=2)
                print(f"[✓] 白名单配置已保存到：{self.config_path}")
            else:
                # 纯规则存储模式
                data = {
                    "version": "1.0",
                    "updated_at": datetime.now().isoformat(),
                    "rules": [r.to_dict() for r in self.rules.values()],
                }
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # --- WhiteListRule 规则 API ---
    def add(
        self,
        name: str,
        wxid: str,
        protect: str = "absolute",
        keywords: Optional[List[str]] = None,
        retain_days: int = 0,
    ) -> WhiteListRule:
        """添加或更新白名单规则."""
        clean_wxid = wxid.strip()
        clean_name = name.strip()
        if not clean_wxid:
            raise ValueError("wxid 不能为空")
        if not clean_name:
            clean_name = clean_wxid

        key = clean_wxid.lower()
        rule = WhiteListRule(
            name=clean_name,
            wxid=clean_wxid,
            protect=protect,
            keywords=[k.strip() for k in (keywords or []) if k.strip()],
            retain_days=retain_days,
        )
        self.rules[key] = rule
        self.name_index[clean_name.lower()] = key
        self.save()
        return rule

    def remove(self, identifier: str) -> bool:
        """按 wxid 或名称移除白名单规则."""
        target_key = identifier.strip().lower()
        if target_key in self.rules:
            rule = self.rules.pop(target_key)
            if rule.name.lower() in self.name_index:
                del self.name_index[rule.name.lower()]
            self.save()
            return True

        if target_key in self.name_index:
            real_wxid = self.name_index.pop(target_key)
            if real_wxid in self.rules:
                del self.rules[real_wxid]
            self.save()
            return True

        return False

    def get(self, identifier: str) -> Optional[WhiteListRule]:
        """按 wxid 或 name 获取规则."""
        target_key = identifier.strip().lower()
        if target_key in self.rules:
            return self.rules[target_key]
        if target_key in self.name_index:
            return self.rules.get(self.name_index[target_key])
        return None

    def list_rules(self) -> List[WhiteListRule]:
        """列出所有白名单规则."""
        return list(self.rules.values())

    def clear(self) -> None:
        """清空所有白名单规则."""
        self.rules.clear()
        self.name_index.clear()
        if self._config:
            self._config.protected_contacts.clear()
            self._config.auto_protected_groups.clear()
        self.save()

    # --- Contact / Group 配置 API ---
    def add_contact(
        self,
        name: str,
        wxid: str,
        tags: Optional[List[str]] = None,
        protection: ProtectionLevel = ProtectionLevel.FILES_ONLY,
    ) -> None:
        """添加联系人到白名单 (兼容 Contact API)."""
        config = self.load()
        tags = tags or []
        new_contact = Contact(name=name, wxid=wxid, tags=tags, protection=protection)

        for i, contact in enumerate(config.protected_contacts):
            if contact.wxid == wxid:
                config.protected_contacts[i] = new_contact
                print(f"[✓] 已更新联系人：{name} ({wxid})")
                break
        else:
            config.protected_contacts.append(new_contact)
            print(f"[✓] 已添加联系人：{name} ({wxid})")

        # 同步更新 rules 字典
        prot_str = "absolute" if protection == ProtectionLevel.ABSOLUTE else "files-only"
        self.rules[wxid.lower()] = WhiteListRule(name=name, wxid=wxid, protect=prot_str, keywords=tags)
        self.name_index[name.lower()] = wxid.lower()
        self.save(config)

    def remove_contact(self, wxid: str) -> bool:
        """从白名单中移除联系人 (兼容 Contact API)."""
        config = self.load()
        original_len = len(config.protected_contacts)
        config.protected_contacts = [c for c in config.protected_contacts if c.wxid != wxid]

        if wxid.lower() in self.rules:
            r = self.rules.pop(wxid.lower())
            if r.name.lower() in self.name_index:
                del self.name_index[r.name.lower()]

        if len(config.protected_contacts) < original_len:
            print(f"[✓] 已从白名单移除：{wxid}")
            self.save(config)
            return True
        else:
            print(f"[-] 未找到联系人：{wxid}")
            return False

    def list_contacts(self) -> List[Contact]:
        """列出所有受保护的联系人."""
        config = self.load()
        return config.protected_contacts.copy()

    def get_protection_level(self, wxid_or_name: str) -> Optional[ProtectionLevel]:
        """获取联系人的保护级别."""
        config = self.load()
        for contact in config.protected_contacts:
            if contact.wxid == wxid_or_name or contact.name == wxid_or_name:
                return contact.protection
        return None

    def is_group_protected(self, group_name: str) -> bool:
        """检查群聊是否在自动保护列表中."""
        config = self.load()
        for group in config.auto_protected_groups:
            if group_name == group or group_name.startswith(group):
                return True
        return False

    # --- 统一防删判断逻辑 ---
    def is_protected(
        self,
        target: Union[str, Path],
        mtime: Optional[float] = None,
    ) -> Union[bool, Tuple[bool, Optional[str]]]:
        """检查指定文件路径或联系人是否受到白名单保护.

        当以 `is_protected(wxid_or_name)` 调用时，返回布尔值 `bool`；
        当以 `is_protected(file_path, mtime)` 调用时，返回元组 `(bool, reason)`。
        """
        # 联系人/群聊名称维度判断 (如 test_whitelist.py 调用)
        if mtime is None and isinstance(target, str) and not ("/" in target or "\\" in target):
            config = self.load()
            for contact in config.protected_contacts:
                if contact.wxid == target or contact.name == target:
                    return True
            target_key = target.strip().lower()
            return target_key in self.rules or target_key in self.name_index

        # 文件路径与修改时间维度判断 (如 wechat_slim.py 清理时调用)
        if not self.rules and not (self._config and self._config.protected_contacts):
            return False, None

        fp = Path(target)
        path_str = str(fp).lower()
        filename = fp.name.lower()
        parts = [p.lower() for p in fp.parts]
        now_ts = datetime.now().timestamp()
        file_mtime = mtime if mtime is not None else 0.0

        for rule in self.rules.values():
            r_wxid = rule.wxid.lower()
            r_name = rule.name.lower()

            matched = False
            if r_wxid in parts or r_wxid in path_str:
                matched = True
            elif r_name and (r_name in parts or r_name in filename):
                matched = True
            elif rule.keywords:
                for kw in rule.keywords:
                    if kw.lower() in filename:
                        matched = True
                        break

            if matched:
                if rule.protect in ["absolute", "files-only"]:
                    return True, f"白名单保护: {rule.name} ({rule.wxid}) [绝对保护]"
                elif rule.protect == "retain_days" and rule.retain_days > 0:
                    file_age_days = (now_ts - file_mtime) / 86400 if file_mtime > 0 else 0
                    if file_age_days <= rule.retain_days:
                        return True, f"白名单保护: {rule.name} ({rule.wxid}) [保留 {rule.retain_days} 天内文件]"

        return False, None
