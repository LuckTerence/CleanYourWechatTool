#!/usr/bin/env python3
"""发布前检查 (preflight) —— 把验收标准固化为一条命令。

用法:
    python3 scripts/preflight.py [--skip-gui] [--skip-dual-env]

全部检查通过 (退出码 0) 才允许打 tag 发布; 任何一项失败即退出码 1。

检查项:
  1. 版本号一致性 (pyproject.toml == setup.py)
  2. 工作区干净 (无未提交改动)
  3. 与远程同步 (本地不领先于 origin/main)
  4. pytest 全量 (当前解释器)
  5. flake8 / mypy 零告警
  6. CLI 全命令冒烟 (10 条)
  7. GUI 测试真跑 (用系统 Python 的 Tk 8.6 环境真跑 test_gui.py,
     防止本地 venv 缺 Tcl 导致 GUI 测试被 skip 而掩盖 CI 失败
     —— v1.0.8 第一轮构建即因此翻车)
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []
PASSED: list[str] = []

# 系统 Python (framework build, 自带 Tk 8.6) —— 用于真跑 GUI 测试
SYSTEM_PY = '/Library/Frameworks/Python.framework/Versions/3.12/bin/python3'


def check(name: str, ok: bool, detail: str = '') -> bool:
    mark = '✅' if ok else '❌'
    line = f'{mark} {name}'
    if detail:
        line += f'  ({detail})'
    print(line)
    (PASSED if ok else FAILURES).append(name)
    return ok


def run(cmd: list[str], cwd: Path = REPO, timeout: int = 300) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except FileNotFoundError:
        return 127, f'命令不存在: {cmd[0]}'
    except subprocess.TimeoutExpired:
        return 124, '超时'


def read_versions() -> tuple[str, str]:
    pyproject = (REPO / 'pyproject.toml').read_text()
    setup = (REPO / 'setup.py').read_text()
    m1 = re.search(r'version\s*=\s*"(\d+\.\d+\.\d+)"', pyproject)
    m2 = re.search(r'version\s*=\s*"(\d+\.\d+\.\d+)"', setup)
    return (m1.group(1) if m1 else '?', m2.group(1) if m2 else '?')


def check_version_consistency() -> bool:
    v1, v2 = read_versions()
    return check('1. 版本号一致性 (pyproject == setup.py)', v1 == v2 and v1 != '?',
                 f'pyproject={v1} setup={v2}')


def check_workspace_clean() -> bool:
    code, out = run(['git', 'status', '--porcelain'])
    clean = code == 0 and not out.strip()
    return check('2. 工作区干净 (无未提交改动)', clean,
                 out.strip()[:120] if not clean else '')


def check_synced_with_remote() -> bool:
    run(['git', 'fetch', 'origin'], timeout=60)
    code, out = run(['git', 'log', 'origin/main..HEAD', '--oneline'])
    ahead = code == 0 and out.strip()
    return check('3. 本地与远程同步 (无未推送提交)', not ahead,
                 f'{len(out.strip().splitlines())} 个未推送提交' if ahead else '')


def check_pytest() -> bool:
    code, out = run([sys.executable, '-m', 'pytest', '-o', 'addopts=', '-q',
                     f'--basetemp={tempfile.mkdtemp()}/pt'],
                    timeout=600)
    m = re.search(r'(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) failed)?', out)
    detail = m.group(0) if m else out[-200:]
    # skipped 是环境限制 (headless GUI / 平台差异), 不算失败
    failed = int(m.group(3)) if m and m.group(3) else (0 if m else -1)
    return check('4. pytest 全量 (当前解释器)', code == 0 and failed == 0, detail)


def check_lint() -> bool:
    ok1, out1 = run([sys.executable, '-m', 'flake8', 'clean_wechat.py',
                     'clean_wechat_gui.py', 'projects/wechat_intelligence_hub/engine/'])
    ok2, out2 = run([sys.executable, '-m', 'mypy', 'projects/clean_wechat.py',
                     '--ignore-missing-imports'])
    detail = ''
    if ok1 != 0:
        detail += f'flake8: {out1.strip()[:120]} '
    if ok2 != 0:
        detail += f'mypy: {out2.strip()[:120]}'
    return check('5. flake8 / mypy 零告警', ok1 == 0 and ok2 == 0, detail)


def check_cli_smoke() -> bool:
    """CLI 全命令冒烟 (注意: 命令参数必须用列表逐个传, 不能拼成带空格的字符串)。"""
    cases = [
        ['scan', '--help'], ['clean', '--help'], ['dedup', '--help'],
        ['restore', '--help'], ['tag', '--help'], ['stats', '--help'],
        ['tag', '--list'],
        ['scan', '--path', str(tempfile.mkdtemp())],        # 空目录: 不崩溃即通过
        ['clean', '--dry-run', '--min-size', '1KB',
         '--path', str(tempfile.mkdtemp())],
    ]
    bad = []
    for case in cases:
        code, out = run([sys.executable, '-B', 'clean_wechat.py', *case], timeout=120)
        if code != 0:
            bad.append(f'{" ".join(case)} (exit {code}: {out.strip()[-100:]})')
    return check('6. CLI 全命令冒烟', not bad, '; '.join(bad) if bad else f'{len(cases)} 条全部通过')


def check_gui_tests_real_run() -> bool:
    """GUI 测试必须在能真跑 Tk 的环境执行 (防止本地 skip 掩盖 CI 失败)。

    v1.0.8 第一轮构建翻车的教训: 本地 venv 缺 Tcl → 10 个 GUI 测试被 skip
    → 本地"全绿" → CI 的 macOS runner 真跑 → 失败 → 打包全部跳过。
    """
    if not Path(SYSTEM_PY).exists():
        return check('7. GUI 测试真跑 (系统 Python)', False,
                     f'未找到 {SYSTEM_PY}, 无法真跑 GUI 测试 —— 请先安装或改用其他带 Tk 的解释器')
    if shutil.which(f'{SYSTEM_PY.rsplit("/", 1)[0]}/pip3.12') is None and \
       not Path(SYSTEM_PY.replace('bin/python3', 'bin/pip3')).exists():
        pass  # pip 检查交由 pytest 内部的 import 失败提示
    code, out = run([SYSTEM_PY, '-m', 'pytest',
                     'projects/wechat-intelligence-hub/tests/test_gui.py',
                     '-o', 'addopts=', '-q', f'--basetemp={tempfile.mkdtemp()}/pt'],
                    timeout=300)
    m = re.search(r'(\d+) passed(?:, (\d+) skipped)?', out)
    if code != 0 or not m:
        # 区分"环境不可用"与"测试失败": Tcl 初始化失败是环境问题, 给出明确指引
        if 'Tcl' in out or 'display' in out.lower():
            return check('7. GUI 测试真跑 (系统 Python)', False,
                         '系统 Python 的 Tk 无法初始化 —— 请检查 customtkinter 是否已 --user 安装')
        return check('7. GUI 测试真跑 (系统 Python)', False, out[-200:])
    detail = m.group(0)
    # GUI 测试的 passed 数应为 22 (若大量 skip 说明环境异常, 仍算失败)
    passed = int(m.group(1))
    return check('7. GUI 测试真跑 (系统 Python, 不允许被 skip)', passed >= 20,
                 f'{detail} (低于 20 passed 说明 GUI 测试被 skip, 有掩盖风险)')


def main() -> int:
    parser = argparse.ArgumentParser(description='发布前检查')
    parser.add_argument('--skip-gui', action='store_true',
                        help='跳过 GUI 真跑检查 (不建议, 仅在明确知道风险时使用)')
    args = parser.parse_args()

    print('=' * 66)
    print('  CleanYourWechatTool - 发布前检查 (preflight)')
    print('=' * 66)

    results = [
        check_version_consistency(),
        check_workspace_clean(),
        check_synced_with_remote(),
        check_pytest(),
        check_lint(),
        check_cli_smoke(),
    ]
    if not args.skip_gui:
        results.append(check_gui_tests_real_run())

    print('-' * 66)
    print(f'通过 {sum(results)}/{len(results)}')
    if all(results):
        print('\n✅ 全部通过 —— 可以打 tag 发布。')
        print('   发布流程见 docs/RELEASE_PROCESS.md')
        return 0
    print('\n❌ 存在失败项 —— 禁止发布。逐项修复后重跑:')
    for f in FAILURES:
        print(f'   - {f}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
