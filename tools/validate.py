"""Run the automated checks and record file fingerprints for publication.

Writes .runtime/validation.json in the form tools/publish.py expects: test
results, the browser reports that must already exist, and a SHA-256 for every
file in the publication allowlist. No network access and no credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from publish import publication_files  # noqa: E402

BROWSER_REPORTS = ['browser.json', 'real-browser.json', 'causes-browser.json']


def run(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    return result.returncode, (result.stdout or '') + (result.stderr or '')


def node_tests():
    code, output = run(['npm.cmd' if sys.platform == 'win32' else 'npm', 'test'])
    passed = re.search(r'^# pass (\d+)', output, re.M)
    failed = re.search(r'^# fail (\d+)', output, re.M)
    return {'command': 'npm test', 'passed': int(passed.group(1)) if passed else 0,
            'failed': int(failed.group(1)) if failed else (0 if code == 0 else 1), 'exit_code': code}


def python_tests():
    code, output = run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'])
    ran = re.search(r'Ran (\d+) tests', output)
    failed = len(re.findall(r'^(?:FAIL|ERROR):', output, re.M))
    total = int(ran.group(1)) if ran else 0
    return {'command': 'python -B -m unittest discover -s tests -p test_*.py', 'passed': total - failed,
            'failed': failed, 'exit_code': code}


def main():
    node, python = node_tests(), python_tests()
    reports = {}
    for name in BROWSER_REPORTS:
        path = ROOT / '.runtime' / name
        report = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        reports[name] = report.get('status', 'missing')
    files = publication_files()
    fingerprints = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
    status = 'passed' if (node['failed'] == 0 and node['exit_code'] == 0 and python['failed'] == 0 and python['exit_code'] == 0
                          and all(value == 'passed' for value in reports.values())) else 'failed'
    record = {
        'status': status, 'verified_at': datetime.now(timezone.utc).isoformat(),
        'node_tests': node, 'python_tests': python, 'local_browser': reports,
        'files': fingerprints,
    }
    (ROOT / '.runtime').mkdir(exist_ok=True)
    (ROOT / '.runtime' / 'validation.json').write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'status': status, 'node': node['passed'], 'python': python['passed'], 'browser': reports, 'files': len(files)}))
    return 0 if status == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
