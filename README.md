# WeChat Slim - 微信智能无损瘦身工具 (Mac 版)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![macOS: Tested](https://img.shields.io/badge/macOS-Apple%20Silicon%20%26%20Intel-success.svg)](https://apple.com)
[![Zero Dependency](https://img.shields.io/badge/Dependencies-Zero%20(Standard%20Lib)-green.svg)](https://docs.python.org/3/library/)
[![Tests](https://img.shields.io/badge/Tests-164%20Passing-brightgreen.svg)](https://github.com/LuckTerence/CleanYourWechatTool)

> 💡 **让 Mac 微信瞬间释放数十 GB 存储空间，绝对不误删重要文件与聊天记录！**  
> 专为 macOS 设计，采用 APFS 原生硬链接去重、核心人脉防删白名单与外置移动硬盘无损归档机制。

---

## 🌟 为什么选择 WeChat Slim？

Mac 微信往往占据 50GB~100GB+ 磁盘空间，传统清理工具要么“一刀切”误删重要工作文件，要么无法处理“多群重复转发”的冗余垃圾。

WeChat Slim 带来三大核心突破：
1. **APFS 秒级硬链接去重**：同一个文件转发到 10 个群，只占 1 份物理磁盘空间。在文件系统底层共享同一 Inode，**微信聊天窗口文件永不断链、无需重新下载，立省数十 GB**。
2. **核心人脉与 VIP 会话白名单**：支持将重要客户、领导、家人（老婆、孩子）设为保护对象。即使执行清理，其相关聊天文件和合同凭证绝不误删。
3. **100% 数据库隔离防护**：核心 SQLite/WCDB 数据库、聊天记录索引受到底层绝对保护，全流程只对冗余视频、缓存和附件进行安全操作。
4. **真正的零依赖 (Zero-Dependency)**：基于 Python 3.8+ 标准库打造，无需额外 `pip install` 繁琐依赖，即下即用。

---

## 🚀 快速开始

### 1. 安装方式

```bash
# 方式 A: 克隆仓库并直接本地安装
git clone https://github.com/LuckTerence/CleanYourWechatTool.git
cd CleanYourWechatTool
pip install .

# 方式 B: 或无需安装直接以脚本运行
python3 wechat_slim.py --help
```

---

## 🛠️ 六大核心命令使用指南

### 1. `scan` - 存储空间智能透视
自动识别 macOS 微信 3.x 与 4.0+ 存储容器，深度统计各类型文件体积与可瘦身潜力。

```bash
# 自动发现微信账号并扫描
wechat-slim scan

# 指定自定义微信路径扫描
wechat-slim scan --path ~/CustomWeChatDir
```

**输出示例：**
```text
==================================================================
       WeChat Slim - 微信智能存储透视器
==================================================================

[账号 1] ID: 89ab32... | 版本: v4 (微信 4.0+)
路径: /Users/username/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/...
------------------------------------------------------------------
  • db_storage   :   166.1 MB      (89 个文件)    2.9%  [🔒 数据库绝对保护]
  • video        :     1.1 GB     (358 个文件)   19.9%  [可瘦身]
  • file         :     1.1 GB     (140 个文件)   19.8%  [可瘦身]
  • attach       :     2.8 GB  (11,000 个文件)   49.7%  [可瘦身]
  • cache        :   437.9 MB   (3,857 个文件)    7.6%  [可瘦身]
------------------------------------------------------------------
  总空间占用   : 5.6 GB
  可瘦身潜力   : 5.4 GB (97.1% 的空间可被安全瘦身/转存)
  白名单保护   : 已加载 2 条核心人脉规则 [已启用绝对防删保护]
==================================================================
```

---

### 2. `dedup` - 多群转发重复文件 APFS 硬链接去重 (杀手锏 🔥)
三级分流流水线（精确大小桶 -> 头部/尾部稀疏哈希 -> 全量 MD5），精准锁定多群转发文件。利用 macOS APFS 文件系统原生 Hardlink 特性实现秒级去重。

```bash
# 第一步: 演练模式 (Dry-Run)，仅查重不修改任何文件
wechat-slim dedup --dry-run

# 第二步: 执行硬链接去重 (立即释放空间，微信会话内文件完全正常打开)
wechat-slim dedup --action hardlink -f

# 进阶参数: 指定仅对大于 5MB 的视频和文件去重
wechat-slim dedup --types video,file --min-size 5MB -f
```

---

### 3. `tag` - VIP 核心人脉防删白名单
将关键联系人（如家人、老板、重要合作方）加入白名单，指定保护级别或关键词过滤。

```bash
# 添加绝对保护人脉 (老婆的任何聊天文件在任何清理中均被锁定保护)
wechat-slim tag --add "老婆" --wxid "wxid_wife123" --protect absolute

# 添加带关键凭证保护的客户
wechat-slim tag --add "战略客户A" --wxid "client_corp" --keywords "合同,协议,报价,发票"

# 查看当前白名单保护列表
wechat-slim tag --list

# 移除白名单保护
wechat-slim tag --remove "wxid_wife123"
```

---

### 4. `clean` - 安全瘦身与外置移动硬盘无损归档
支持过滤时间范围、文件类型和文件体积，提供系统废纸篓安全删除与外置归档两大模式。

```bash
# 模式 A: 演练预览 (Dry-Run，安全无副作用)
wechat-slim clean --dry-run --days 90 --min-size 20MB --types video,file

# 模式 B: 安全清理 (移入 macOS 废纸篓，可在 Finder 中随时放回原处)
wechat-slim clean --days 90 --min-size 20MB --types video,file

# 模式 C: 外置硬盘/NAS 无损归档 (转存大文件，原目录保留目录结构)
wechat-slim clean \
  --archive-to "/Volumes/MyExternalSSD/WeChat_Archive" \
  --days 180 \
  --min-size 10MB \
  -f
```

---

### 5. `stats` - 历史累计瘦身大盘与审计记录
跟踪记录每次扫描、去重和清理的释放体积，评估微信存储健康度。

```bash
# 查看累计释放统计与系统健康评级
wechat-slim stats

# 查看完整的历史操作审计日志
wechat-slim stats --history
```

**输出示例：**
```text
==================================================================
       WeChat Slim - 历史累计瘦身统计与审计大盘
==================================================================
  • 累计运行次数 : 8 次
  • 累计瘦身清理 : 5 次
  • 累计硬链接去重: 3 次
  • 累计释放空间 : 42.8 GB
  • 存储健康评级 : A (优良)
------------------------------------------------------------------
  最近操作记录:
    [2026-09-07 15:20:10] clean | 外置归档: /Volumes/SSD | 14.2 GB (820 个文件)
    [2026-09-07 14:10:05] dedup | hardlink 去重 | 28.6 GB (3,410 个文件)
==================================================================
```

---

### 6. `web` - 本地轻量可视化看板 (WebUI)
纯 Python 标准库内置 HTTP 服务器，无需安装任何前端工具链，浏览器直观管理白名单与查看大盘。

```bash
# 启动本地看板 (默认自动在浏览器打开 http://127.0.0.1:8080)
wechat-slim web

# 指定端口与静默模式
wechat-slim web --port 9090 --no-browser
```

---

## 🤖 AI Agent 联动 (Antigravity / Codex / Claude)

本项目自带符合 Agent Skills 规范的技能配置：`skills/wechat-slim/SKILL.md`。  
在支持 Agentic AI 助理的环境下，直接下达自然语言指令即可触发本工具：

- *"帮我看看我的微信占用了多少空间，有哪些可以清理？"*
- *"把微信里转发重复的视频用 APFS 硬链接去重一下。"*
- *"把老婆和领导加入防删白名单，然后把半年前大于 50MB 的视频归档到外接硬盘。"*

---

## 🛡️ 安全承诺与设计底线

1. **数据库 100% 免疫**：`db_storage` 目录及 `*.db`, `*.sqlite`, `*.wcdb` 等数据库文件在底层直接硬编码跳过，不受任何命令影响。
2. **非毁灭性操作**：清理默认走 macOS 废纸篓 (`trash`)，杜绝 `rm -rf` 直接抹除风险；外置归档完整保留目录结构。
3. **白名单一票否决权**：命中白名单规则的文件在清理阶段拥有绝对豁免权。
4. **配置损坏自愈**：白名单与状态记录采用防御性加载机制，遇损坏自动恢复安全初始状态。

---

## 🧪 测试套件与质量保证

项目具备极其严格的自动化测试体系：
- **164 项测试 100% 通过**，覆盖端到端 CLI 调用、APFS 硬链接验证、白名单拦截、状态持久化与 WebUI 接口。

```bash
# 运行全量单元测试
python3 -m unittest discover projects/wechat-intelligence-hub/tests
```

---

## 📈 版本路线图

- [x] **v1.0.0 (当前版本)**:
  - 核心存储深度透视 (`scan`)
  - 废纸篓安全删除与外置硬盘无损归档 (`clean`)
  - APFS 原生硬链接多群文件去重 (`dedup`)
  - VIP 核心人脉防删白名单 (`tag`)
  - 累计统计与审计大盘 (`stats`)
  - 本地零依赖可视化看板 (`web`)
  - Agent Skill 联动支持
- [ ] **v1.1.0**:
  - 引入 Rich 彩色终端流式输出与进度条
  - 微信 4.0 联系人数据库智能反解好友昵称
- [ ] **v2.0.0**:
  - 跨平台支持 (Windows 原生 NTFS 硬链接支持)

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源发布。
欢迎提交 Issue 与 PR 共同完善！
