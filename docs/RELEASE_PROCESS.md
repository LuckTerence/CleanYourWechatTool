# 发布流程与回退方案

> 本文档回答三个问题：为什么 bug 会反复出现、怎样验证一个版本可以发布、
> 发布后出问题怎么办。所有步骤都对应仓库内的实际工具。

---

## 一、bug 反复出现的根因（本项目 4 天开发史的真实案例）

每次失败都不是孤立的意外，归为五类。**每次出新 bug 时，先对照这张表定位类别**：

| # | 根因 | 真实案例 | 对应防御 |
|---|---|---|---|
| 1 | **多个写入者无协调** | 并行 AI 会话同时改仓库，互相覆盖；误报照修后 CLI 全挂 | 单会话写入；修复报告问题前先复现 |
| 2 | **改代码未跑完整验证链** | 改默认值没同步测试断言 → CI 红、发布被跳过；本地 venv 缺 Tcl → 10 个 GUI 测试被 skip → 本地"全绿"掩盖 CI 失败 | `scripts/preflight.py`（含 GUI 真跑） |
| 3 | **同一逻辑多处实现** | 死线判定在 cleaner 与 GUI 各写一份；预估与执行口径分离 → 承诺 304MB 实际 238MB | 提取 `is_hard_protected()` 单一事实来源 |
| 4 | **环境组合未验证** | Tk 9.0 + CTk 6.0.0（CTk 未适配 Tk 9）→ 触控板滚动失效；打包产物缺 PIL → 双击即崩 | 产物冒烟门禁（CI 内置）+ 双环境测试 |
| 5 | **测试断言与产品行为脱节** | 测试数据用旧结构（无 path 键）→ 一改实现就 KeyError | 行为级测试优先于静态断言 |

---

## 二、发布前验收标准与检查步骤

### 验收标准（全部满足才允许发布）

| # | 标准 | 检查方式 |
|---|---|---|
| 1 | 版本号三处一致（pyproject / setup.py / 待打 tag） | preflight 第 1 项 |
| 2 | 工作区干净、与远程同步 | preflight 第 2/3 项 |
| 3 | pytest 全量通过（允许 skipped，不允许 failed） | preflight 第 4 项 |
| 4 | flake8 / mypy 零告警 | preflight 第 5 项 |
| 5 | CLI 全命令冒烟通过 | preflight 第 6 项 |
| 6 | **GUI 测试真跑**（≥20 passed，不允许被 skip） | preflight 第 7 项 |
| 7 | CI 全绿（test 矩阵 + security-scan） | `gh run list` |
| 8 | 产物冒烟通过（CI 内置：.app/.exe 启动 + 无 traceback） | CI 的 build-gui job |
| 9 | **人工下载 Release 实测**（挂载 → 运行 → 走一遍关键路径） | 手动，不可省略 |

### 检查步骤

```bash
# 一条命令跑完 1-6 项 (任何失败即退出码 1, 禁止发布)
python3 scripts/preflight.py

# 第 7 项: 推送后确认 CI 全绿
git push origin main
gh run watch   # 或 gh run list --limit 1
```

---

## 三、发布步骤

```bash
# 1. preflight 全绿后, 升版本号 (三处同步)
#    pyproject.toml / setup.py: version = "X.Y.Z"

# 2. 写发布说明
#    docs/RELEASE_NOTES_vX.Y.Z.md

# 3. 提交并打 tag (tag 触发 CI: test 矩阵 → release → 双平台打包 → 产物冒烟)
git add -A && git commit -m "chore(release): vX.Y.Z"
git push origin main
git tag -a vX.Y.Z -m "CleanYourWechatTool vX.Y.Z · <一句话主题>"
git push origin vX.Y.Z

# 4. 创建 Release 页面 (CI 会自动把资产传上来)
gh release create vX.Y.Z --title "CleanYourWechatTool vX.Y.Z · <主题>" \
    --notes-file docs/RELEASE_NOTES_vX.Y.Z.md

# 5. 等 CI 全绿后验证
gh run watch
gh release view vX.Y.Z --json assets --jq '[.assets[].name] | join("\n")'
#    必须看到: arm64.dmg / windows-x64.zip / .whl / .tar.gz 四件

# 6. 【不可省略】下载实测
curl -sL -o /tmp/test.dmg <dmg 链接>
hdiutil attach /tmp/test.dmg -nobrowse -quiet
/Applications/CleanYourWechatTool.app/Contents/MacOS/CleanYourWechatTool &
#    确认启动存活 + 无 traceback, 然后走一遍: 扫描 → 勾选 → 清理 → 废纸篓
```

**如果 CI 的打包 job 失败（Release 资产为空）**：说明产物有问题被冒烟门禁拦下——
这是门禁在正常工作。修复后 **force 重推 tag 重新构建**：

```bash
git tag -f -a vX.Y.Z -m "..." && git push -f origin vX.Y.Z
# gh release upload 会用 --clobber 覆盖资产
```

---

## 四、回退方案（按影响范围分四层）

### 层 1：把"官方下载"切回旧版本（用户侧回退，最常用）

Release 是累积的，旧版本永久可下载。回退只需切换 Latest 标记：

```bash
gh release edit v1.0.7 --latest --repo LuckTerence/CleanYourWechatTool
```

用户下次打开 Release 页看到的就是旧版本。**不需要删任何东西。**

### 层 2：撤下有问题的版本（防止新用户下载到）

```bash
gh release delete vX.Y.Z --yes          # 删 Release 页面
git push origin --delete refs/tags/vX.Y.Z   # 删 tag
```

适用场景：新版本启动即崩、或包含数据风险（参考 v1.0.5 的处理）。

### 层 3：代码回退（修复后重发）

**用 `git revert` 而不是 `reset --hard`**（保留历史，多人协作安全）：

```bash
git revert <坏提交的hash>        # 生成一个反向提交
git push origin main
# 然后按"发布步骤"发一个补丁版本 (版本号 +0.0.1)
```

### 层 4：用户数据的天然回退（产品自带，无需操作）

- 所有清理默认**移入系统废纸篓**（macOS Trash / Windows Recycle Bin），
  用户在废纸篓里「放回原处」即恢复；
- 归档模式生成 `archive_manifest.json`，`cleanyourwechat restore --manifest <路径>`
  可原样恢复；
- 数据库与程序组件在**物理死线**内（`is_hard_protected`），任何规则都不可触碰。

---

## 五、历史教训检查表（每次出 bug 后对照）

- [ ] 这个 bug 属于五类根因中的哪一类？防御措施是否已加入 preflight 或 CI？
- [ ] 修复是否引入了新的不一致（是否两处实现、是否需要同步测试）？
- [ ] 报告的问题是否先**复现**了？（端到端 agent 的 L2 误报教训）
- [ ] 改动是否在**能真跑相关测试的环境**验证过？（本地 skip 掩盖的教训）
- [ ] 打包产物是否实测过？（v1.0.5 缺 PIL 的教训——CI 测源码不测产物）
