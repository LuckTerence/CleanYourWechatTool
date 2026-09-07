# CleanYourWechatTool - Project Scaffold Documentation
# ================================================
# 
# This document describes the project structure and development setup.
#
## 📁 Directory Structure

```
wechat-intelligence-hub/
├── .github/                    # GitHub configuration
│   ├── workflows/              # CI/CD pipelines
│   │   └── ci.yml              # Automated testing on push/PR
│   ├── ISSUE_TEMPLATE/         # Issue templates
│   │   ├── bug_report.md
│   │   └── feature_request.md
│   ├── CONTRIBUTING.md         # Contribution guidelines
│   └── PULL_REQUEST_TEMPLATE.md
│
├── docs/                       # Documentation
│   ├── wechat-storage-slimming-prd-v2.0.md  # Product Requirements
│   └── USAGE.md                # User guide
│
├── projects/                   # Python source code
│   ├── clean_wechat.py          # Main CLI application
│   └── wechat-intelligence-hub/
│       └── tests/              # Test suite
│           └── test_clean_wechat.py
│
├── scripts/                    # Utility scripts
│   └── setup.sh                # Project initialization script
│
├── .pre-commit-config.yaml     # Pre-commit hooks configuration
├── .gitignore                  # Git ignore rules
├── LICENSE                     # MIT License
├── README.md                   # Project readme
├── requirements-dev.txt        # Development dependencies
├── pyproject.toml             # Project metadata & tool config
├── setup.py                   # Setuptools configuration
└── .env.example               # Environment variables template
```

## 🔧 Development Setup

### Quick Start (Recommended)

```bash
# Run the automated setup script
./scripts/setup.sh
```

This will:
1. ✅ Create virtual environment
2. ✅ Install all dependencies
3. ✅ Set up pre-commit hooks
4. ✅ Run initial test suite
5. ✅ Initialize git repository

### Manual Setup

#### 1. Clone & Enter Project

```bash
git clone https://github.com/LuckTerence/wechat-intelligence-hub.git
cd wechat-intelligence-hub
```

#### 2. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# or
.venv\Scripts\activate      # Windows
```

#### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements-dev.txt
```

#### 4. Verify Installation

```bash
# Check installation
python3 -c "import sys; print(f'Python {sys.version}')"

# Verify pytest works
pytest --version

# Verify linting tools
flake8 --version
black --version
mypy --version
```

## 📦 Key Configuration Files

### `requirements-dev.txt`
Development dependencies including:
- **Testing**: pytest, pytest-cov, pytest-xdist
- **Code Quality**: black, isort, flake8, mypy, pylint
- **Packaging**: build, twine, wheel
- **Pre-commit**: pre-commit hooks framework

### `pyproject.toml`
Unified configuration for:
- **Build system**: setuptools >= 61.0
- **Black**: Code formatting (line-length=88)
- **isort**: Import sorting (profile="black")
- **mypy**: Type checking (Python 3.8+)
- **pytest**: Test configuration (90% coverage target)

### `.pre-commit-config.yaml`
Automated code quality checks before commit:
- ✅ Trailing whitespace removal
- ✅ EOF newline enforcement
- ✅ YAML/JSON syntax validation
- ✅ Large file detection (max 5MB)
- ✅ Line ending normalization (LF)
- ✅ Black code formatting
- ✅ Import sorting
- ✅ Flake8 linting
- ✅ MyPy type checking
- ✅ Pylint static analysis

### `.github/workflows/ci.yml`
Continuous Integration pipeline:
- **Test matrix**: macOS + Ubuntu × Python 3.8-3.12
- **Coverage upload**: Codecov integration
- **Security scanning**: Bandit + Safety
- **Auto-publish**: PyPI release on tags

## 🎯 Code Quality Standards

### Before You Commit

Always run these commands to ensure code quality:

```bash
# Format code
black projects/
isort projects/

# Check types
mypy projects/clean_wechat.py

# Lint code
flake8 projects/

# Run all tests with coverage
pytest projects/wechat-intelligence-hub/tests/ --cov=projects --cov-report=html
```

### Pre-commit Hooks (Auto-run on commit)

```bash
# Install hooks once
pre-commit install

# They'll run automatically before each commit!
git commit -m "your message"
```

If any hook fails, it will prevent the commit and show you how to fix issues.

## 🧪 Testing Strategy

### Unit Tests
Located in `tests/test_clean_wechat.py`:
- **Test function logic** independently
- **Mock external dependencies** (file system, database)
- **Target**: ≥ 90% line coverage

Run tests:
```bash
# All tests
pytest projects/wechat-intelligence-hub/tests/ -v

# Specific test
pytest projects/wechat-intelligence-hub/tests/test_clean_wechat.py::test_format_bytes

# With coverage report
pytest projects/wechat-intelligence-hub/tests/ --cov=projects --cov-report=html --cov-fail-under=90
```

### Integration Tests
End-to-end testing:
- Test actual file operations
- Validate CLI commands
- Verify disk I/O behavior

```bash
# Full integration test suite
pytest projects/wechat-intelligence-hub/tests/integration/ -v
```

## 📚 Code Style Guide

### Type Hints (Required)
```python
from pathlib import Path
from typing import Optional, List, Dict, Tuple

def scan_directory(
    dir_path: Optional[Path], 
    is_protected: bool = False
) -> ScanCategory:
    """Scan a directory and return statistics.
    
    Args:
        dir_path: Path to the directory to scan.
        is_protected: Whether files are protected from deletion.
        
    Returns:
        ScanCategory object with aggregated file information.
        
    Raises:
        PermissionError: If access denied to directory.
    """
```

### Docstrings (Google Style)
```python
"""Module description.

Features:
    - Feature 1
    - Feature 2

Example:
    >>> example_function(10)
    100
"""
```

### Naming Conventions
- **Classes**: PascalCase (`AccountProfile`, `ScanCategory`)
- **Functions**: snake_case (`discover_accounts`, `format_bytes`)
- **Variables**: snake_case (`account_id`, `total_size`)
- **Constants**: UPPER_SNAKE_CASE (`MAX_FILE_SIZE`, `DEFAULT_DAYS`)
- **Private methods**: Single underscore (`_internal_helper()`)
- **Public API**: No underscore (`scan_account()`)

## 🚀 Building & Packaging

### Build Distribution
```bash
python -m build
```

Creates:
- `dist/clean_wechat-1.0.0.tar.gz` (source distribution)
- `dist/clean_wechat-1.0.0-py3-none-any.whl` (wheel)

### Test Package Locally
```bash
twine check dist/*
```

### Publish to PyPI (Release Only)
```bash
# Login to PyPI (first time)
twine upload dist/*

# Or use GitHub Actions workflow (recommended)
git tag v1.0.0
git push origin v1.0.0
```

## 🔍 Debugging Tips

### Enable Verbose Logging
```bash
# Add debugging flags
python3 clean_wechat.py --verbose scan
```

### Interactive Debugger (pdb)
```bash
import pdb
pdb.set_trace()  # Breakpoint
# Or run: python3 -m pdb clean_wechat.py
```

### Inspect File Operations
```bash
# Dry-run mode (no actual deletions)
python3 clean_wechat.py clean --dry-run

# Trace file system calls
strace python3 clean_wechat.py clean  # Linux only
```

## 📊 Metrics & KPIs

Track these metrics during development:

| Metric | Target | Tool |
|--------|--------|------|
| **Test Coverage** | ≥ 90% | pytest-cov |
| **Type Annotations** | 100% functions | mypy |
| **Code Formatting** | Black-compliant | black |
| **Import Sorting** | isort-standard | isort |
| **Linter Errors** | 0 | flake8/pylint |

## 🆘 Troubleshooting

### Virtual Environment Issues
```bash
# Recreate venv if corrupted
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Pre-commit Hook Fails
```bash
# Force pre-commit to run
pre-commit run --all-files --show-diff-on-failure

# Disable temporarily (not recommended)
pre-commit run --hook-stage manual
```

### CI Pipeline Fails Locally
```bash
# Replicate CI environment
docker pull python:3.11-slim
docker run -it python:3.11-slim bash
# Inside container: follow manual setup steps
```

## 🤝 Getting Help

- 📖 Read [CONTRIBUTING.md](.github/CONTRIBUTING.md) for detailed contribution guide
- 💬 Open an issue on GitHub for bugs/questions
- 📧 Email: xihuan1127@gmail.com

## 🎓 Learning Resources

- [Python Best Practices](https://docs.python-guide.org/writing/)
- [PEP 8 Style Guide](https://peps.python.org/pep-0008/)
- [Typing in Python](https://typing.readthedocs.io/)
- [Git Flow Workflow](https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow)

---

Ready to develop? Let's make CleanYourWechatTool better! 🚀
