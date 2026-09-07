# CleanYourWechatTool - Contributing Guidelines

Thank you for your interest in contributing to CleanYourWechatTool! This document provides guidelines for contributing to the project.

## 🚀 How to Contribute

### Reporting Bugs

Before creating bug reports, please check existing issues to avoid duplicates. When creating a bug report, please include:

- **Environment**: OS, Python version, WeChat version
- **Steps to reproduce**: Clear steps to reproduce the issue
- **Expected vs actual behavior**: What did you expect vs what happened?
- **Logs/Terminal output**: Relevant error messages
- **Screenshots**: If applicable

### Suggesting Features

Feature requests are welcome! Please provide:

- **Problem statement**: What problem does this solve?
- **Proposed solution**: How should it work?
- **Alternatives considered**: Other solutions you've thought about
- **Use cases**: Examples of how this would be used

### Code Contributions

#### 1. Setup Development Environment

```bash
# Fork the repository and clone it
git clone https://github.com/YOUR_USERNAME/wechat-intelligence-hub
cd wechat-intelligence-hub

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements-dev.txt

# Install pre-commit hooks
pre-commit install
```

#### 2. Make Changes

Follow these coding standards:

- **Type hints**: All functions must have type annotations
- **Docstrings**: Use Google style docstrings
- **Tests**: Add unit tests for new functionality (target ≥ 90% coverage)
- **Code style**: Follow Black formatting (line-length=88)

Example function structure:
```python
from pathlib import Path
from typing import Optional, List

def scan_directory(
    dir_path: Optional[Path], 
    is_protected: bool = False
) -> ScanCategory:
    """Scan directory and return statistics.
    
    Args:
        dir_path: Path to directory to scan
        is_protected: Whether files are protected
        
    Returns:
        ScanCategory with file count and total size
        
    Raises:
        PermissionError: If directory cannot be accessed
    """
    if not dir_path or not dir_path.exists():
        return ScanCategory(...)
    
    # Your implementation here
    ...
```

#### 3. Run Tests

```bash
# Run all tests
pytest projects/wechat-intelligence-hub/tests/ -v

# Run with coverage
pytest projects/wechat-intelligence-hub/tests/ --cov=projects --cov-report=html

# Open coverage report
open htmlcov/index.html
```

#### 4. Code Formatting

```bash
# Format code with Black
black projects/

# Sort imports with isort
isort projects/

# Check types with mypy
mypy projects/clean_wechat.py

# Run linting
flake8 projects/
```

#### 5. Pre-commit Checks

Before committing, run:
```bash
pre-commit run --all-files
```

This will automatically:
- Fix trailing whitespace
- Ensure files end with newline
- Format code with Black
- Sort imports with isort
- Run linters

#### 6. Create Pull Request

1. **Branch naming**: Use `feat/xxx`, `fix/xxx`, `docs/xxx`, `chore/xxx`
2. **Commit messages**: Follow Conventional Commits format
   ```bash
   feat: add white list support for contacts
   fix: resolve crash when database corrupted
   docs: update README with usage examples
   ```
3. **PR description**: Link related issues, describe changes
4. **Update documentation**: Update README/USAGE if needed

### Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting)
- `refactor`: Code refactoring
- `test`: Adding tests
- `chore`: Build/tooling changes

**Examples:**
```bash
feat: add hash deduplication engine for large files
fix: handle permission errors gracefully in scanner
docs: update installation instructions
refactor: extract storage scanning logic into helper function
```

### Testing Requirements

All new features must include:
- ✅ Unit tests for core functions
- ✅ Integration tests for CLI commands
- ✅ Edge case coverage
- ✅ Target: ≥ 90% line coverage

Run tests before submitting PR:
```bash
pytest tests/ -v --cov=clean_wechat --cov-fail-under=90
```

### Review Process

1. Maintainers will review within 48 hours
2. CI/CD pipeline runs automatically on each PR
3. At least one approval required for merge
4. Squash merges preferred for clean history

### First Time Contributors

If you're new to the project, start with these issues:
- [Good First Issue](https://github.com/LuckTerence/wechat-intelligence-hub/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- [Help Wanted](https://github.com/LuckTerence/wechat-intelligence-hub/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)

### Questions?

Join our community:
- 💬 GitHub Discussions
- 📧 Email: xihuan1127@gmail.com

Thank you for making CleanYourWechatTool better! 🎉
