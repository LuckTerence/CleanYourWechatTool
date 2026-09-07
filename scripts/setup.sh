#!/bin/bash
# Project Setup Script - 一键初始化开发环境

set -e  # Exit on error

echo "🚀 WeChat Slim - Project Initialization"
echo "========================================"

# 1. Create virtual environment if not exists
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
    echo "✅ Virtual environment created!"
else
    echo "⚡ Virtual environment already exists."
fi

# 2. Activate virtual environment
echo "\n⬇️  Activating virtual environment..."
source .venv/bin/activate

# 3. Install dependencies
echo "\n📥 Installing development dependencies..."
pip install --upgrade pip
pip install -r requirements-dev.txt
echo "✅ Dependencies installed!"

# 4. Install pre-commit hooks
echo "\n🔧 Installing pre-commit hooks..."
pre-commit install
echo "✅ Pre-commit hooks installed!"

# 5. Run initial tests
echo "\n🧪 Running initial test suite..."
pytest projects/wechat-intelligence-hub/tests/ -v --tb=short
echo "✅ Tests passed!"

# 6. Initialize git if not a repo
if [ ! -d ".git" ]; then
    echo "\n📝 Initializing git repository..."
    git init
    git add .
    git commit -m "Initial commit: WeChat Slim CLI"
    echo "✅ Git repository initialized!"
fi

echo ""
echo "=================================================================="
echo "✨ Project initialization complete! Ready for development."
echo ""
echo "Next steps:"
echo "  1. Configure git (git config user.email/name)"
echo "  2. Push to GitHub: git remote add origin ..."
echo "  3. Start developing: python3 wechat_slim.py"
echo "  4. Run tests: pytest projects/wechat-intelligence-hub/tests/"
echo ""
echo "Happy coding! 🎉"
echo "=================================================================="
