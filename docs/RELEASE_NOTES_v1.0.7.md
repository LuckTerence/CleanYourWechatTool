# Release Notes - CleanYourWechatTool v1.0.7

**发布日期**: 2026-09-09  
**类型**: 紧急修复（macOS 打包依赖补全与 Logo 渲染）+ CI 产物冒烟门禁加固

---

## 核心修复：补齐 macOS 打包环境 Pillow 依赖

在 v1.0.6 中虽然增加了优雅降级，但 macOS 构建环境（CI `build-gui` 步骤）中因遗漏 `pillow` 安装，导致生成的 `.app` 产物未打包 Pillow，运行时无法显示高清 Logo 和窗口图标：

1. **构建依赖补全**：在 `.github/workflows/ci.yml` 的 `build-gui` 阶段中正式补齐 `pillow` 安装；
2. **Logo 逻辑严密防护**：在 `clean_wechat_gui.py` 的 `_build_ui()` 中增加 `Image is not None` 判空，彻底消除潜在的 `AttributeError`；
3. **冒烟测试深度加固**：在产物冒烟门禁中新增「Logo 与关键装饰依赖完整性」断言，若出现图标降级告警将立即阻断发布。

---

## 下载

| 平台 | 文件 | 说明 |
|---|---|---|
| macOS (Apple Silicon) | `CleanYourWechatTool-arm64.dmg` | 适用于 M1/M2/M3/M4 芯片 Mac |
| macOS (Intel) | `CleanYourWechatTool-x86_64.dmg` | 适用于 Intel 芯片 Mac |
| Windows 10/11 (64 位) | `CleanYourWechatTool-windows-x64.zip` | 绿色便携免安装版，解压直接运行 |

macOS 首次打开若提示"已损坏 / 无法验证开发者"（应用未经公证）：
右键应用图标 → 打开；或执行 `xattr -cr /Applications/CleanYourWechatTool.app`。

Windows：未签名的便携版可能被 SmartScreen 拦截，选择「更多信息」→「仍要运行」即可。
