# CleanYourWechatTool (微信存储空间优化工具)

```text
  ____ _               __        __    ____ _           _   
 / ___| | ___  __ _ _ _\ \      / /__ / ___| |__   __ _| |_ 
| |   | |/ _ \/ _` | '_ \ \ /\ / / _ \ |   | '_ \ / _` | __|
| |___| |  __/ (_| | | | \ V  V /  __/ |___| | | | (_| | |_ 
 \____|_|\___|\__,_|_| |_|\_/\_/ \___|\____|_| |_|\__,_|\__|
```

<p align="center">
  <strong>macOS 微信存储空间优化工具 · APFS 原生硬链接去重 · 白名单与废纸篓保护</strong>
</p>

<p align="center">
  <a href="#背景与问题"><img src="https://img.shields.io/badge/macOS-Apple%20Silicon%20%26%20Intel-black?style=flat-square&logo=apple" alt="macOS" /></a>
  <a href="#快速开始"><img src="https://img.shields.io/badge/Python-3.8%2B%20Zero--Dependency-blue?style=flat-square&logo=python" alt="Python" /></a>
  <a href="#方案对比"><img src="https://img.shields.io/badge/APFS-Hardlink%20Deduplication-success?style=flat-square" alt="APFS" /></a>
  <a href="#测试覆盖与验证"><img src="https://img.shields.io/badge/Tests-54%20Passed-brightgreen?style=flat-square" alt="Tests" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-orange?style=flat-square" alt="License" /></a>
</p>

---

## 背景与问题

macOS 微信长期使用后通常占用数十至上百 GB 存储空间，主要成因及现有清理方案的局限：

1. **多群转发存储冗余**：同一份 100MB 视频或文档转发至 8 个群聊后，微信在各群对应目录分别写入独立文件，产生 800MB 物理磁盘占用。
2. **内置清理缺乏细粒度控制**：微信自带存储管理主要提供按会话批量删除聊天记录的选项，无法在保留文件索引的同时释放重复空间。
3. **通用清理工具破坏文件可用性**：第三方清理软件直接删除文件实体后，微信聊天窗口中的对应文件显示为“已失效或已被清理”，无法再次调阅。

**CleanYourWechatTool** 通过 APFS 文件系统特性与本地规则配置，在保留聊天记录文件可访问性的前提下释放重复存储占用。

---

## 工作原理与核心机制

### 1. APFS 原生硬链接去重 (Hardlink)
利用 macOS APFS 文件系统特性处理多群重复接收的文件：
- 计算文件内容哈希，将内容相同的重复附件合并为指向同一磁盘数据块（inode）的硬链接；
- 仅保留一份物理空间占用，释放冗余数据块；
- 各群聊路径及文件名保持不变，聊天记录内点击仍可正常预览与打开，无需重新下载。

### 2. 白名单保护
- **联系人与群聊防护**：支持按联系人备注、昵称或 wxid 配置保护规则，指定对象发送的文件不参与清理；
- **关键词匹配**：支持按文件名关键词（如“合同”、“报价”、“发票”）配置过滤条件；
- **本地昵称反解**：只读解析本地联系人 SQLite 数据库，支持直接使用可见昵称配置规则，无需手动检索内部 ID；
- **白名单优先判定**：清理调度中优先执行白名单过滤，命中规则的文件直接跳过。

### 3. 系统废纸篓与外置存储归档
- **废纸篓保护**：清理操作默认调用 macOS 原生系统废纸篓 API，支持在废纸篓中通过“放回原处”恢复误删文件；
- **外置目录归档**：支持将超过指定天数与体积阈值的文件迁移至外部介质（如 SSD 或 NAS），保留原有相对目录树结构。

### 4. 数据库保护与无侵入设计
- **索引数据库硬编码排除**：扫描与操作逻辑中显式忽略 `db_storage` 及 `*.db`, `*.sqlite`, `*.wcdb` 等聊天记录文字索引库；
- **纯文件层操作**：不解析私聊正文内容，不修改数据库，不注入运行时内存，不挂钩（Hook）微信进程。

---

## 方案对比

| 评估维度 | 微信内置“存储空间清理” | 常见第三方清理软件 | **CleanYourWechatTool (本项目)** |
|---|:---:|:---:|:---:|
| **多群重复文件去重** | 不识别，按群分别保留副本 | 直接删除文件 | **APFS 原生硬链接，释放物理冗余** |
| **聊天窗口文件可用性** | 删除后无法打开 | 提示“文件已过期或被清理” | **保留原始路径，可正常预览** |
| **保护规则配置** | 不支持 | 仅支持按大类目录勾选 | **支持按联系人、群聊、关键词过滤** |
| **误删恢复机制** | 不可恢复 | 部分直接粉碎 | **默认移入系统废纸篓，支持放回原处** |
| **外置存储归档** | 不支持 | 不支持 | **支持保留目录层级迁移至外部存储** |
| **联系人昵称映射** | 仅应用内展示 | 不提供联系人归属 | **只读解析本地 SQLite 映射备注与群名** |
| **系统依赖** | 官方内置 | 常驻后台服务 | **基于 Python 3.8+ 标准库，零外部依赖** |

---

## 快速开始

本项目基于 Python 3.8+ 标准库实现，无需安装外部包依赖。

### 方式 A：下载图形界面 (推荐，无需终端)

从 [Releases](https://github.com/LuckTerence/CleanYourWechatTool/releases) 按机型下载并双击打开：

- Apple Silicon (M1/M2/M3/M4)：`CleanYourWechatTool-arm64.dmg`
- Intel 芯片：`CleanYourWechatTool-x86_64.dmg`

打开 dmg 后将 `CleanYourWechatTool.app` 拖入"应用程序"目录。

**首次打开提示"已损坏，无法打开"或"无法验证开发者"？** 本应用未经 Apple 公证（仓库无付费开发者证书），属正常现象，任选其一解决：

```bash
# 方法 1 (推荐)：右键应用图标 → 打开 → 再点"打开"
# 方法 2：终端执行以下命令后正常打开
xattr -cr /Applications/CleanYourWechatTool.app
```

**提示"未找到微信数据"？** 打开 系统设置 → 隐私与安全性 → 完全磁盘访问权限，将 CleanYourWechatTool 加入列表后重启应用。微信容器目录 (`~/Library/Containers/com.tencent.xinWeChat`) 受 macOS 隐私保护，未授权时任何工具都无法读取。

图形界面中所有清理条件均为下拉与勾选：选择时间范围 / 文件类型 / 大小阈值 → 预览将处理的完整文件清单 → 确认执行。执行前若检测到微信正在运行会提醒先退出。

### 方式 B：免安装直接运行

```bash
git clone https://github.com/LuckTerence/CleanYourWechatTool.git
cd CleanYourWechatTool

# 启动图形界面
python3 clean_wechat_gui.py

# 或启动终端交互式引导
python3 clean_wechat.py
```

### 方式 C：通过 pip 安装为命令行工具

```bash
pip install .
```

安装后可在系统任意路径调用 `cleanyourwechat` 命令。

---

## 常用场景与命令

### 场景 1：存储分布分析 (`scan`)
分析微信数据目录各分类占用大小及可去重潜力：

```bash
cleanyourwechat scan
```

> **终端输出示例：**
> ```text
> ==================================================================
>   CleanYourWechatTool - 微信存储分布分析
> ==================================================================
>   账号 [wxid_89ab32...] - v4 (微信 4.0+)
>   路径: ~/Library/Containers/com.tencent.xinWeChat/...
> ------------------------------------------------------------------
>   • db_storage   :   166.2 MB     (89 个文件)    2.9%  [数据库保护]
>   • video        :     1.1 GB    (358 个文件)   19.8%  [可优化]
>   • file         :     1.1 GB    (140 个文件)   19.8%  [可优化]
>   • attach       :     2.8 GB (11,039 个文件)   49.8%  [可优化]
>   • cache        :   441.0 MB  (3,898 个文件)    7.7%  [可优化]
> ------------------------------------------------------------------
>   总空间占用   : 5.6 GB
>   优化潜力     : 5.5 GB (97.1% 为媒体附件与缓存)
> ==================================================================
> ```

---

### 场景 2：重复文件硬链接去重 (`dedup`)
识别多群接收的重复附件，合并为硬链接：

```bash
# 演练模式：评估预期释放空间，不修改实际文件
cleanyourwechat dedup --dry-run

# 执行去重：将重复文件转换为硬链接
cleanyourwechat dedup --action hardlink -f
```

---

### 场景 3：白名单规则配置 (`tag`)
配置免清理的联系人或文件特征规则：

```bash
# 添加联系人保护规则（该联系人关联文件不参与清理）
cleanyourwechat tag --add "家人" --wxid "wxid_family123" --protect absolute

# 添加关键词保护规则（仅保护匹配特定名称的文件）
cleanyourwechat tag --add "重要客户" --wxid "client_corp" --keywords "合同,协议,报价,发票"

# 查看当前已生效的规则列表
cleanyourwechat tag --list
```

---

### 场景 4：历史文件归档至外部存储 (`clean`)
将满足时间与大小阈值的文件迁移至外部介质：

```bash
cleanyourwechat clean \
  --archive-to "/Volumes/ExternalSSD/WeChatArchive" \
  --days 180 \
  --min-size 20MB \
  -f
```
> 文件按原相对路径迁移至指定目录，本地原文件移入系统废纸篓。

---

### 场景 5：启动本地 Web 控制台 (`web`)
基于 Python 标准库内置 HTTP 服务启动图形化管理面板：

```bash
cleanyourwechat web
```
启动后自动打开 `http://127.0.0.1:8080`，提供存储分布图表、去重操作与白名单规则管理功能。

---

## 常见问题 (FAQ)

<details>
<summary><strong>Q: 硬链接去重后，微信内能否正常打开文件？</strong></summary>
<p>
可以正常打开。硬链接是 APFS 文件系统层面的引用机制。去重后各聊天会话目录下的文件路径依然存在且有效，微信调用系统 API 读取文件时透明获取物理数据块，不会产生“文件已失效”提示。
</p>
</details>

<details>
<summary><strong>Q: 工具是否会读取聊天文本内容或存在隐私外泄风险？</strong></summary>
<p>
不会。工具仅对本地文件系统中的多媒体和文档进行操作，不解析聊天正文数据库。联系人映射仅通过本地只读方式查询 SQLite 数据库。代码完全开源，无任何网络请求与遥测上报逻辑。
</p>
</details>

<details>
<summary><strong>Q: 清理操作误删文件后如何恢复？</strong></summary>
<p>
工具清理逻辑默认调用 macOS 原生系统废纸篓接口，不执行不可逆的直接删除。如需恢复，在系统废纸篓中找到对应文件并选择“放回原处”即可。
</p>
</details>

<details>
<summary><strong>Q: 是否会导致微信封号或崩溃？</strong></summary>
<p>
不会。封号与异常检测通常由内存注入、客户端逆向修改或自动化协议通信引起。本项目为独立的本地文件管理工具，不依附微信运行进程，不修改客户端签名与程序包内容。
</p>
</details>

<details>
<summary><strong>Q: 是否支持 macOS 微信 4.0+ 版本？</strong></summary>
<p>
支持。工具内置微信数据目录检测机制，兼容微信 3.x（散列目录）与微信 4.0+（<code>xwechat_files</code> 架构），支持多账号自动识别。
</p>
</details>

---

## 测试覆盖与验证

项目包含自动化单元测试集，覆盖硬链接去重、白名单规则匹配、联系人昵称解析及 Web 服务模块：

```bash
python3 -m unittest discover projects/wechat-intelligence-hub/tests
```

> **测试状态**：54 项自动化测试全部通过。

---

## 参与贡献

欢迎提交针对新型微信目录变动的适配、错误反馈或改进建议：
1. 提交 [Issue](https://github.com/LuckTerence/CleanYourWechatTool/issues) 汇报异常日志与环境信息；
2. 提交 Pull Request 贡献代码或优化文档。

---

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。
