# Release Notes - CleanYourWechatTool v1.0.6

**发布日期**: 2026-09-09
**类型**: 紧急修复（产物启动崩溃）+ 发布质量门禁

---

## 修复：v1.0.5 的 .app 双击即崩溃

v1.0.5 产物的图形界面在启动时抛出异常：

```
ModuleNotFoundError: No module named 'PIL'
```

原因：窗口图标显示依赖 Pillow，但打包配置未收集该依赖。
该问题未被 CI 发现——87 项测试与多平台矩阵均针对源码运行，不覆盖打包产物。

修复（双保险）：

1. 图形界面将 Pillow 改为可选依赖，缺失时优雅降级为无窗口图标，**不阻断启动**；
2. 打包配置显式收集 `PIL / PIL.Image / PIL.ImageTk`。

实测：修复后产物启动存活并正常进入主界面（此前必崩）。

## 新增：发布前的产物冒烟门禁

为避免同类问题再次被"测试全绿"掩盖，发布流程在上传 Release 前新增冒烟步骤：

- 真正运行打包产物（macOS 运行 .app，Windows 运行 .exe）；
- 断言进程存活且启动日志无 `Traceback / ModuleNotFoundError / ImportError`；
- Windows 额外校验产物内 `customtkinter`、`PIL` 依赖资源齐备；
- 任一不通过则发布流程失败，坏包不会出现在 Release 页面。

---

## 下载

| 平台 | 文件 |
|---|---|
| macOS (Apple Silicon) | `CleanYourWechatTool-arm64.dmg` |
| macOS (Intel) | `CleanYourWechatTool-x86_64.dmg` |
| Windows 10/11 (64 位) | `CleanYourWechatTool-windows-x64.zip` |

macOS 首次打开若提示"已损坏 / 无法验证开发者"（应用未经公证）：
右键应用图标 → 打开；或执行 `xattr -cr /Applications/CleanYourWechatTool.app`。

Windows：未签名的便携版可能被 SmartScreen 拦截，选择「更多信息」→「仍要运行」即可。
