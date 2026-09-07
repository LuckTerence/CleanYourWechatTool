# Clean Your WeChat Tool - 微信智能无损瘦身工具

![GitHub stars](https://img.shields.io/github/stars/LuckTerence/CleanYourWechatTool?style=social)
![GitHub release](https://img.shields.io/github/v/release/LuckTerence/CleanYourWechatTool)
![License](https://img.shields.io/badge/license-MIT-blue)
![Test Status](https://github.com/LuckTerence/CleanYourWechatTool/actions/workflows/ci.yml/badge.svg)

> 💡 让 Mac 微信瞬间释放 50GB+ 空间的智能工具，绝对不误删重要文件！

## 🔥 快速开始

```bash
# 安装 (v1.0+)
pip install clean-your-wechat-tool

# 或直接运行 raw script
python3 wechat_slim.py scan
```

## ✨ 核心特性

- 🚀 **极速扫描**: 单线程原生 Python，10k 文件仅需数秒
- 🛡️ **绝对安全**: 数据库隔离保护，废纸篓非暴力删除  
- 📦 **无损归档**: 转存外接硬盘，文字链完整保留
- 🎯 **智能过滤**: 时间/大小/类型多维度组合
- ⚡ **零依赖**: 全部使用 Python 标准库，开箱即用
- ✅ **真实验证**: 已在本机实测清理 5.4GB(97.1%)

## 📊 效果演示

### 存储空间透视
```
==================================================================
       WeChat Slim - 微信智能存储透视器
==================================================================

[账号 1] ID: wxid_xxx | 版本: v4 (微信 4.0+)
路径: /Users/terencesai/Library/Containers/com.tencent.xinWeChat/...
------------------------------------------------------------------
  • db_storage   :   166.1 MB    (89 个文件)   2.9%  [🔒 数据库绝对保护]
  • video        :     1.1 GB   (358 个文件)  19.9%  [可瘦身]
  • file         :     1.1 GB   (140 个文件)  19.8%  [可瘦身]
  • attach       :     2.8 GB (11,000 个文件)  49.7%  [可瘦身]
  • cache        :   437.9 MB  (3,857 个文件)   7.6%  [可瘦身]
------------------------------------------------------------------
  总空间占用   : 5.6 GB
  可瘦身潜力   : 5.4 GB (97.1% 的空间可被安全瘦身/转存)
```

## 🙋 常见问题

**Q: 会误删我的聊天记录吗？**  
A: 绝对不会！我们承诺：**db_storage/**目录下的所有数据 100% 受保护，即使是 --force 也不会触碰。

**Q: 操作失误能恢复吗？**  
A: 可以的!我们默认使用 macOS 废纸篓，您可以在 Finder 里找到并"放回原处"。

**Q: 支持 Windows/Linux 吗？**  
A: v1.0 仅支持 macOS，Windows/Linux版本已在 Roadmap 中规划 v4.0。

**Q: 企业微信支持吗？**  
A: 当前版本不支持企业微信，未来可能会考虑添加。

## 🛠️ 使用方法

### 1. 扫描存储空间
```bash
# 自动发现微信目录并深度扫描
wechat-slim scan

# 指定自定义目录
wechat-slim scan --path ~/MyCustomWechatPath
```

### 2. 执行清理 (Dry-Run 演练)
```bash
# 查看 90 天前大于 20MB 的视频和文件 (不实际删除)
wechat-slim clean --dry-run --days 90 --min-size 20MB --types video,file
```

### 3. 安全清理到废纸篓
```bash
# 确认后可以删除 (移入废纸篓，非永久删除)
wechat-slim clean --days 90 --min-size 20MB --types video,file
```

### 4. 无损外置归档
```bash
# 将文件转移到外接硬盘/NAS
wechat-slim clean \
  --archive-to "/Volumes/MySSD/WeChat_Archive" \
  --days 180 \
  --min-size 10MB \
  -f
```

## 🎯 高级功能

### 交互式向导模式
```bash
# 启动图形化命令行向导
python3 wechat_slim.py
```

### 批量多账号处理
```bash
# 对多个微信账号执行清理
for path in /Users/*/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/*; do
  python3 wechat_slim.py clean --path "$path" --dry-run
done
```

## 🔧 开发指南

### 环境搭建
```bash
git clone https://github.com/LuckTerence/CleanYourWechatTool
cd CleanYourWechatTool

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试
pytest projects/wechat-intelligence-hub/tests/ -v  
```

### 代码质量检查
```

### 代码质量检查
```bash
# 格式化代码
black projects/
isort projects/

# Linter 检查
flake8 projects/
mypy projects/

# Pre-commit hooks
pre-commit run --all-files
```

## 📝 License

MIT License - 详见 [LICENSE](LICENSE) 文件

## 🤝 贡献

欢迎提交 Bug 报告、新功能、文档改进！

请参考 [CONTRIBUTING.md](.github/CONTRIBUTING.md) 了解如何参与项目开发。

## 📈 路线图

- ✅ **v1.0 (当前)**: CLI 基础版 + 真实环境验证
- 🚧 **v1.1**: 白名单系统 + 哈希去重
- 🎯 **v1.2**: 增量扫描 + NPS 反馈收集
- 🎨 **v2.0**: WebUI 可视化面板
- 🌐 **v3.0**: Windows/Linux跨平台支持
- 💼 **v4.0**: Professional Edition 商业化版本

## 📞 联系方式

- **Issue**: [GitHub Issues](https://github.com/LuckTerence/CleanYourWechatTool/issues)
- **Email**: xihuan1127@gmail.com
- **Twitter**: [@LuckTerence](https://twitter.com/LuckTerence)

## ⚠️ 免责声明

本项目为开源工具，仅供个人学习与研究使用。使用者需自行承担使用风险，开发者不对任何数据丢失或损坏承担责任。建议在执行任何操作前备份重要数据。

---

Made with ❤️ by LuckTerence | Last Updated: 2026-09-07
