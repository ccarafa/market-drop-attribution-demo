"""Publish the verified static study to the existing authorized origin."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_FILES = [
    'index.html', 'demo.html', 'assets/style.css', 'assets/app.js', 'assets/engine.js',
    'assets/study.css', 'assets/study.js', 'assets/study-core.js',
    'data/replay.json', 'data/manifest.json', 'data/study.json', 'README.md',
    'docs/METHOD.md', 'docs/REAL_DATA_METHOD.md', 'AGENTS.md', '.gitignore',
    '.gitattributes', '.nojekyll', 'package.json', 'tests/engine.test.js',
    'tests/study.test.js', 'tests/test_real_study.py', 'tools/prepare_data.py',
    'tools/build_real_study.py', 'tools/check_browser.py', 'tools/check_real_browser.py',
    'tools/publish.py', '.github/workflows/checks.yml',
    'tools/datayes_client.py', 'tools/check_datayes_connection.py',
    'tests/test_datayes_client.py', 'tests/test_datayes_connection.py',
    'docs/DATAYES_CONNECTION.md', '.github/workflows/datayes-connection.yml',
]
ENV = os.environ.copy()
ENV['PYTHONWARNINGS'] = 'ignore'
ENV.pop('GH_TOKEN', None)
ENV.pop('GITHUB_TOKEN', None)


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=ROOT, env=ENV, capture_output=True,
                            text=True, encoding='utf-8', errors='replace')
    if check and result.returncode:
        raise RuntimeError('Command failed: ' + args[0] + ' ' + args[1])
    return result


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(['gh', *args], check=check)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(['git', '-c', 'safe.directory=' + str(ROOT), *args], check=check)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api(path: str) -> dict:
    return json.loads(gh('api', path).stdout)


def main() -> None:
    # The existing remote pins the destination; the active CLI account cannot redirect it.
    remote = git('remote', 'get-url', 'origin').stdout.strip()
    match = re.fullmatch(r'https://github\.com/([^/]+)/([^/]+?)(?:\.git)?', remote)
    if not match:
        raise RuntimeError('Expected an existing HTTPS GitHub origin')
    owner, name = match.groups()
    if name != 'market-drop-attribution-demo':
        raise RuntimeError('Unexpected target repository')
    repository = owner + '/' + name
    credential = gh('auth', 'token', '--hostname', 'github.com', '--user', owner)
    token = credential.stdout.strip()
    if not token:
        raise RuntimeError('Authorized destination credential unavailable')
    ENV['GH_TOKEN'] = token
    identity = api('user')
    if identity.get('login', '').lower() != owner.lower():
        raise RuntimeError('Destination credential mismatch')
    info = api('repos/' + repository)
    if info.get('visibility') != 'public' or not info.get('permissions', {}).get('push'):
        raise RuntimeError('Expected existing public repository with push permission')
    pages = api('repos/' + repository + '/pages')
    if not pages.get('https_enforced'):
        raise RuntimeError('HTTPS must be enforced before publication')

    files = BASE_FILES + sorted(path.relative_to(ROOT).as_posix()
                               for path in (ROOT / 'data/series').glob('[0-9].json'))
    if not (ROOT / 'data/series').is_dir() or len(files) == len(BASE_FILES):
        raise RuntimeError('History shards missing')
    for filename in files:
        path = ROOT / filename
        if not path.is_file():
            raise RuntimeError('Publication file missing: ' + filename)
        text = path.read_text(encoding='utf-8')
        if re.search(r'-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}', text):
            raise RuntimeError('Credential marker in publication: ' + filename)
    unexpected = set(git('ls-files').stdout.splitlines()) - set(files)
    if unexpected:
        raise RuntimeError('Unexpected tracked files')

    for filename in ['browser.json', 'real-browser.json']:
        report = json.loads((ROOT / '.runtime' / filename).read_text(encoding='utf-8'))
        if report.get('status') != 'passed' or report.get('deployment') != 'local_subpath':
            raise RuntimeError('Local browser verification missing: ' + filename)
        fingerprints = report.get('asset_sha256')
        if not fingerprints:
            raise RuntimeError('Browser verification lacks file fingerprints: ' + filename)
        for relative, expected in fingerprints.items():
            if sha(ROOT / relative) != expected:
                raise RuntimeError('File changed after browser verification: ' + relative)
    validation = json.loads((ROOT / '.runtime' / 'validation.json').read_text(encoding='utf-8'))
    if validation.get('status') != 'passed' or not validation.get('node_tests') or not validation.get('python_tests'):
        raise RuntimeError('Calculation and interaction test verification missing')
    if set(validation.get('files', {})) != set(files):
        raise RuntimeError('Validation file allowlist differs from publication')
    for relative, expected in validation['files'].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError('File changed after validation: ' + relative)

    git('-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential', 'fetch', 'origin', 'main')
    behind = git('rev-list', '--count', 'HEAD..origin/main').stdout.strip()
    if behind != '0':
        raise RuntimeError('Remote has new commits; reconcile before publication')
    git('config', 'user.name', 'Z')
    git('config', 'user.email', 'project@localhost')
    git('add', '--', *files)
    if git('diff', '--cached', '--quiet', check=False).returncode:
        git('commit', '-m', 'Publish verified attribution site and data connection tools')
    git('-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential', 'push', 'origin', 'HEAD:main')
    commit = git('rev-parse', 'HEAD').stdout.strip()
    gh('repo', 'edit', repository, '--description',
       '市场事件归因系统：历史日度事件、同期样本比较、量价证据与数据质量核查')
    result = {'status': 'pushed_waiting_pages', 'repository': repository,
              'url': pages['html_url'], 'commit': commit,
              'study_sha256': sha(ROOT / 'data/study.json'),
              'replay_sha256': sha(ROOT / 'data/replay.json'),
              'data_scope': '2026-07 local historical daily evidence',
              'all_new_provider_data_downloaded': False}
    (ROOT / '.runtime' / 'deployment.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ['status', 'url', 'commit']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
