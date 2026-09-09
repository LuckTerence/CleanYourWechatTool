# Release Notes - CleanYourWechatTool v1.0.5

**发布日期**: 2026-09-09  
**类型**: 全平台支持升级（Windows & macOS 双端全覆盖）+ 零 Emoji 纯净专业交互 + 界面平滑滚动修复

---

## 核心新特性：Windows 跨平台全面支持

全面拓展设备生态，在保持原汁原味安全守则（白名单绝对保护、数据库物理隔离、系统回收站/废纸篓安全退路）的前提下，完整覆盖 Windows 微信生态：

1. **Windows 注册表与多盘符智能发现**：
   - 自动读取注册表 HKCU\Software\Tencent\WeChat\FileSavePath 定位用户真实数据盘；
   - 自动探测多盘符及用户文档路径（~/Documents/WeChat Files、D:\WeChat Files、E:\...）；
   - 支持 Windows 微信 4.0 跨平台现代化存储结构与微信 3.x 传统 FileStorage + Msg 结构。

2. **Windows 数据库绝对物理锁定**：
   - 底层清理引擎将 Msg 目录及所有 *.db、*.sqlite 文件纳入不可触碰的物理死线，坚决防止损坏聊天数据库。

3. **Windows 本地进程与交互原生适配**：
   - 微信进程检测（Windows tasklist.exe / macOS pgrep）；
   - 系统级文件打开（Windows os.startfile / explorer /select 与 macOS open）；
   - 系统专属中文字体智能选用（Windows Microsoft YaHei 与 macOS PingFang SC）；
   - 回收站安全移动（支持 Windows Recycle Bin 与 macOS Trash）。

4. **全自动 CI 构建与 Release 发布**：
   - GitHub Actions 新增 Windows 构建流水线，自动输出 CleanYourWechatTool-windows-x64.zip 便携免安装绿色版。

---

## 界面与交互优化

1. **零 Emoji 纯净企业级视觉**：
   - 全面移除界面所有 Emoji 表情符号，采用严谨、沉稳的现代化专业生产力工具设计；
   - 抽屉列表与状态提示统一采用清晰文本标签（[视频]、[压缩包]、[文档]、[其他]）。

2. **自适应视口滚动体验**：
   - 核心视图全面集成 CTkScrollableFrame，适配不同屏幕分辨率与小屏笔记本；
   - 展开细粒度筛选面板后依然能够平滑双指/滚轮上下滚动，头部与底部操作栏始终常驻。

---

## 质量与测试

- 全量 84 项测试全部通过（含 Windows 目录发现、结构解析、全版本白名单与去重验证）；
- 代码质量通过 Flake8 严格校验（零语法与命名告警）。
