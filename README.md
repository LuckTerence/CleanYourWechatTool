# WeChat Slim - 微信智能无损瘦身与人脉透视工具 (Mac 版)

<p align="center">
  <img src="https://img.shields.io/badge/Platform-macOS%20(Apple%20Silicon%20%26%20Intel)-000000?logo=apple&style=for-the-badge" alt="macOS" />
  <img src="https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white&style=for-the-badge" alt="Python 3.8+" />
  <img src="https://img.shields.io/badge/Dependencies-Zero%20(Standard%20Lib)-2ea44f?style=for-the-badge" alt="Zero Dependencies" />
  <img src="https://img.shields.io/badge/Tests-168%20Passing-brightgreen?style=for-the-badge" alt="Tests" />
  <img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="License: MIT" />
</p>

> 💡 **让 Mac 微信瞬间释放数十 GB 存储空间，绝对不误删重要文件与聊天记录！**  
> 专为 macOS 深度定制，融合 **APFS 原生硬链接去重**、**核心人脉防删白名单**、**联系人数据库智能反解**、**移动硬盘无损归档** 与 **本地轻量级 WebUI 可视化大盘**。

---

## 🌟 痛点与核心价值

随着微信深度绑定日常工作与生活，Mac 微信动辄吞噬 **50GB ~ 100GB+** 宝贵的固态硬盘空间：
- ❌ **传统清理工具粗暴抹除**：一键“清理缓存”往往误伤客户合同、报价单、珍贵家庭照片；
- ❌ **“多群转发”冗余严重**：同一个 100MB 视频被转发到 10 个群，微信就会保存 10 份物理副本，白白浪费 1GB 磁盘；
- ❌ **外部依赖臃肿**：很多工具需要安装庞大的第三方环境，存在隐私泄露风险。

**WeChat Slim (微信智能瘦身)** 专为解决上述痛点而生：
1. **APFS 秒级硬链接去重 (杀手锏 🔥)**：同一个文件转发再多群，在 macOS APFS 底层合并为共享 Inode。**只占 1 份物理磁盘空间，微信内各个聊天窗口依然能原样点击秒开，无需重新下载**。
2. **核心人脉与 VIP 会话防删白名单 (🛡️ VIP Protection)**：可将重要客户、领导、家人（老婆、孩子）设为保护对象；**即使执行全盘大清理，白名单会话中的合同与文件拥有一票否决权，绝对免疫误删**。
3. **联系人/群聊昵称自动智能反解**：通过 SQLite 只读连接池无损反解加密会话中的真实微信昵称与备注名，告别晦涩难懂的哈希串与 `wxid`。
4. **100% 数据库绝对隔离防护**：所有核心聊天记录数据库 (`db_storage`, `*.db`, `*.sqlite`, `*.wcdb`) 在底层直接硬编码隔离，绝不读写、修改或删除任何数据库文件。
5. **安全废纸篓与外置移动硬盘无损归档**：非毁灭性清理，清理文件默认安全移入 macOS 废纸篓（可随时放回原处），或按原完整目录树一键转存归档至外置 SSD/NAS。
6. **真正的零依赖 (Zero External Dependencies)**：核心能力 **100% 基于 Python 3.8+ 标准库** 实现，无须编译任何 C 扩展。环境检测到支持时自动增强 Rich 彩色终端流式输出与动态进度条，无感知优雅降级。
7. **原生本地 WebUI 可视化大盘**：内置标准库 HTTP 服务，无需 Node.js 或前端编译，一键启动本地浏览器看板，直观掌控空间分布与白名单规则。

---

## 🚀 快速上手

### 1. 安装方式

#### 方式 A：通过 pip 本地安装 (推荐，全局注册 `wechat-slim` 命令)
```bash
git clone https://github.com/LuckTerence/CleanYourWechatTool.git
cd CleanYourWechatTool
pip install .
```

#### 方式 B：免安装直接脚本运行
无需安装任何包，克隆后直接使用系统的 Python 运行：
```bash
python3 wechat_slim.py --help
```

---

## 🎮 极简交互式向导模式

如果你不想记任何参数，直接在终端输入命令回车，即可启动贴心的交互式向导：
```bash
wechat-slim
```
向导将自动探测微信账号、展示存储健康度，引导你选择进行“智能透视”、“硬链接去重”或“安全瘦身”。

---

## 🛠️ 六大核心命令详解

### 1. `scan` - 存储空间智能透视与人脉分布
自动识别 macOS 微信 3.x 与 4.0+ 存储容器，深度统计视频、文件、附件、缓存与白名单保护资产。

```bash
# 自动发现本机微信账号并扫描
wechat-slim scan

# 指定自定义微信路径进行扫描
wechat-slim scan --path ~/CustomWeChatDir
```

**终端输出示例：**
```text
==================================================================
  WeChat Slim - 微信智能存储透视器
==================================================================
  账号 [89ab32...] - v4 (微信 4.0+)
  路径: /Users/username/Library/Containers/com.tencent.xinWeChat/...
------------------------------------------------------------------
  • db_storage   :   166.1 MB     (89 个文件)    2.9%  [🔒 数据库绝对保护]
  • video        :     1.1 GB    (358 个文件)   19.9%  [可瘦身]
  • file         :     1.1 GB    (140 个文件)   19.8%  [可瘦身]
  • attach       :     2.8 GB (11,000 个文件)   49.7%  [可瘦身]
  • cache        :   437.9 MB  (3,857 个文件)    7.6%  [可瘦身]
------------------------------------------------------------------
  总空间占用   : 5.6 GB
  可瘦身潜力   : 5.4 GB (97.1% 的空间可被安全瘦身/转存)
------------------------------------------------------------------
  🛡️ 核心人脉白名单保护:
  • 活跃白名单规则 : 2 条 (老婆 [宝贝], 战略合作群)
  • 已锁定保护文件 : 42 个文件 (380.5 MB 空间受白名单绝对保护，绝不误删)
==================================================================
```

---

### 2. `dedup` - 多群重复文件 APFS 硬链接去重 (杀手锏 🔥)
三级分流查重架构（文件大小桶预筛 -> 快速稀疏首尾哈希 -> 全量 MD5 指纹），精准锁定多群转发文件。利用 macOS APFS 文件系统的 Hardlink 特性实现秒级去重，**释放物理磁盘空间，微信各群聊天记录文件完整可用**。

```bash
# 第一步: 演练模式 (Dry-Run)，仅计算查重节省空间，不修改磁盘
wechat-slim dedup --dry-run

# 第二步: 正式执行 APFS 硬链接去重 (推荐，无损立省数十GB)
wechat-slim dedup --action hardlink -f

# 可选: 仅对超过 5MB 的视频和文件执行查重
wechat-slim dedup --types video,file --min-size 5MB -f

# 可选: 将多余副本直接移入废纸篓 (不保留硬链接)
wechat-slim dedup --action trash -f
```

---

### 3. `tag` - VIP 核心人脉防删白名单管理
支持设置绝对防删 (`absolute`)、限制保留时间 (`retain_days`) 或文件名关键字过滤 (`keywords`)。配合 `ContactResolver`，自动反解联系人微信名称。

```bash
# 1. 添加绝对保护人脉 (该联系人的所有文件在任何清理中均被锁定保护)
wechat-slim tag --add "老婆" --wxid "wxid_wife123" --protect absolute

# 2. 添加重要商务客户 (仅重点保护合同、发票等关键凭证)
wechat-slim tag --add "战略合作方" --wxid "client_corp" --keywords "合同,协议,报价,发票"

# 3. 查看当前所有生效的白名单规则 (自动关联显示微信备注名)
wechat-slim tag --list

# 4. 移除指定的白名单规则
wechat-slim tag --remove "wxid_wife123"
```

---

### 4. `clean` - 安全瘦身与外置移动硬盘无损归档
支持自由组合时间过滤、类型过滤与大小阈值，提供废纸篓暂存与外置硬盘完整归档双重策略。

```bash
# 模式 A: 演练模式 (Dry-Run)，安全评估将清理哪些文件
wechat-slim clean --dry-run --days 90 --min-size 10MB --types video,file

# 模式 B: 安全清理 (移入 macOS 系统废纸篓，可在访达中随时一键撤销放回)
wechat-slim clean --days 90 --min-size 10MB --types video,file

# 模式 C: 外置移动硬盘/NAS 无损完整转存 (保留原相对目录树结构)
wechat-slim clean \
  --archive-to "/Volumes/MyExternalSSD/WeChat_Archive" \
  --days 180 \
  --min-size 20MB \
  -f
```

---

### 5. `stats` - 历史累计瘦身统计与审计大盘
实时持久化跟踪历史累计运行次数、扫描量、去重量、清理释放体积与白名单保护数据，并内置审计流水日志。

```bash
# 查看累计释放空间与历史大盘
wechat-slim stats

# 查看历史操作审计流水记录
wechat-slim stats --history
```

---

### 6. `web` - 本地轻量可视化看板 (WebUI Dashboard)
基于 Python 内置 HTTP 引擎实现的现代化单页看板，无需任何前端环境，浏览器直观操作。

```bash
# 启动本地看板 (默认自动唤起系统浏览器打开 http://127.0.0.1:8080)
wechat-slim web

# 指定自定义端口并禁止自动开窗
wechat-slim web --port 9090 --no-browser
```

---

## 🏗️ 模块化工程架构

项目遵循**高内聚、低耦合与单一职责设计**，整体代码分层如下：

```
.
├── wechat_slim.py              # CLI 命令行调度入口、参数解析与交互向导
└── engine/                     # 底层可复用核心引擎
    ├── common.py               # ANSI 配色、格式化计算、审计日志与 Rich 适配器
    ├── scanner.py              # 多版本微信目录自动感知与分类扫描引擎
    ├── cleaner.py              # 规则过滤、白名单防删校验、废纸篓与归档引擎
    ├── dedup.py                # 分级哈希计算与 APFS 硬链接去重引擎
    ├── web.py                  # 本地 WebUI 仪表盘与原生 HTTP API 处理
    ├── whitelist.py            # VIP 白名单管理器 (单源真理，支持 JSON/YAML)
    ├── contact_resolver.py     # SQLite 只读连接池联系人/群聊昵称反解引擎
    └── state.py                # 历史指标持久化与审计流水管理器
```

---

## 🤖 AI Agent 深度联动 (Antigravity / Codex / Claude)

本项目自带标准的 AI Agent Skill 配置，路径位于 `skills/wechat-slim/SKILL.md`。  
在任何支持 Agentic AI 助理的工作区中，你只需输入日常自然语言即可完成专业级清理与维护：

- 🗣️ *“帮我查一下我的微信占了多少空间，有没有很多重复转发的文件？”*
- 🗣️ *“把微信里多群转发的视频用 APFS 硬链接去重一下。”*
- 🗣️ *“把老婆和领导加入防删白名单，然后把半年前大于 50MB 的冗余视频归档到外接移动硬盘。”*

---

## 🛡️ 安全底线原则

| 准则 | 实现机制 |
|---|---|
| **数据库 100% 免疫** | `db_storage` 及 `*.db`, `*.sqlite`, `*.wcdb` 等数据库文件底层物理跳过，只读模式连接 |
| **非毁灭性操作** | 默认采用 macOS 苹果原生 `Finder Trash` 废纸篓机制，杜绝粗暴 `rm -rf`，随时可放回原处 |
| **白名单一票否决** | 只要命中核心人脉规则或关键词，在瘦身流程中享有最高优先级绝对豁免权 |
| **状态损坏自愈** | 配置文件与状态数据库内置防御式容错机制，遇异常自动回退安全默认值 |

---

## 🧪 严苛测试验证

项目内置完整、真实的端到端模拟测试体系，覆盖扫描、去重、硬链接创建、白名单过滤拦截、WebUI API 与状态持久化：

```bash
# 执行全量单元测试与集成测试
python3 -m unittest discover projects/wechat-intelligence-hub/tests
```
> **当前测试状态**：**168 项测试用例 100% 通过**，执行时间 < 3.0s。

---

## 📄 开源协议与贡献

本项目基于 [MIT License](LICENSE) 协议开源。  
欢迎提交 [Issues](https://github.com/LuckTerence/CleanYourWechatTool/issues) 反馈建议或提出 Pull Request，让每个人的 Mac 都能轻松瘦身！
