# Release Notes - CleanYourWechatTool v1.0.0 (Official MVP)

**发布日期**: 2026-09-08
**适用平台**: macOS (Apple Silicon & Intel x86_64)，微信 4.0+（3.x 目录结构已兼容识别）
**Python 要求**: Python >= 3.8（零第三方运行时依赖）

---

## 亮点 (Key Highlights)

### 1. 微信存储空间透视 (`cleanyourwechat scan`)
- 自动发现本机全部微信账号，按 `db_storage / video / file / attach / cache` 分类统计空间占用。
- 实测：本机微信 5.6 GB 中，核心文字与数据库仅占约 2.9%（166 MB），**97% 的空间是可安全瘦身的媒体与附件**。

### 2. 废纸篓安全清理 (`cleanyourwechat clean`)
- 所有清理默认通过 macOS Finder 移入**系统废纸篓**（非 `rm`），可随时"放回原处"。
- 支持 `--days / --min-size / --types` 组合过滤与 `--dry-run` 演练模式。
- `--archive-to` 将陈年大文件无损迁移至外接 SSD / NAS，原始目录结构完整保留。

### 3. 多群重复文件去重 (`cleanyourwechat dedup`)
- 解决"同一文件转发到 N 个群占用 N 份磁盘"的痛点。
- 三级指纹（大小分桶 → 稀疏采样 → 全量哈希）快速识别重复文件。
- `--action hardlink` 通过 APFS 硬链接合并重复副本：**微信各会话点击原样打开，物理磁盘只占一份**。

### 4. 防删白名单 (`cleanyourwechat tag`)
- 支持文件名关键词（如"合同、报价、委托书"）与精确路径规则的绝对豁免，命中文件绝不进废纸篓。
- 清理与去重全程一票否决，并在报告中统计保护量。
- **能力边界**：微信 4.0 数据库为 WCDB/SQLCipher 加密，未提供解密密钥时无法按"发送者/会话"维度保护文件（详见 §8 已知限制）。

### 5. 数据库绝对防护
- `db_storage/` 下的 SQLite/WCDB 库在扫描、清理、归档、去重全程被物理隔离，100% 不触碰。

### 6. 本地可视化大盘 (`cleanyourwechat web`)
- 零第三方依赖的内置 WebUI：资产分布、条件瘦身、白名单管理、历史统计与审计日志可视化。

### 7. 统计与审计 (`cleanyourwechat stats`)
- 累计运行/释放/保护量与最近 50 条操作记录持久化到 `~/.wechat_slim/`，全操作写入审计日志。

---

## 质量与测试

- **自动化测试**: 54 项单元与端到端测试全绿。
- **CI**: GitHub Actions 矩阵（Python 3.8–3.12 × macOS / Ubuntu）+ flake8 + mypy + bandit + pip-audit 全绿。
- **真实验证**: 在开发者本机真实微信 4.0 数据（15,000+ 文件 / 5.6 GB）上完成 scan / clean --dry-run / 白名单命中的端到端验证。

---

## 安装

```bash
# 从源码安装
git clone https://github.com/LuckTerence/CleanYourWechatTool.git
cd CleanYourWechatTool
pip install .

# 验证
cleanyourwechat --help

# 或免安装直接运行
python3 clean_wechat.py scan
```

---

## 已知限制（诚实声明）

详见 `docs/wechat-storage-slimming-prd.md` §8：
1. 微信 4.0 数据库加密，未提供密钥时**无法实现"按联系人/发送者防删"**，白名单按文件名关键词生效；
2. `msg/video/`、`msg/attach/` 为哈希命名，无语义信息，只能按时间/大小/类型处理；
3. 本工具不修改、不恢复任何微信聊天内容；清理的是本地媒体文件缓存，**请先自行确认**。

## 规划中（v1.1+）

- AI Agent Skill 包（`skills/`）随正式版发布（开发分支已验证）。
- 解密数据库（需用户密钥）后的"文件 → 发送者"映射，实现真正的联系人级白名单。
