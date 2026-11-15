#!/usr/bin/env python3
"""Scan all Git refs and publishable working-tree files with Gitleaks, with secrets redacted."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
scanner = os.environ.get('GITLEAKS') or shutil.which('gitleaks') or str(ROOT / '.toolchains/gitleaks/gitleaks')
if not Path(scanner).is_file():
    sys.exit('Gitleaks is required; install the official binary and set GITLEAKS (see RELEASE.md)')
# Check exact private bytes too: a binary keystore renamed to .bin must not bypass the scan.
config = Path(os.environ.get('LUMEN_RU_SIGNING_PROPERTIES', ROOT.parent / '.android-release-private/lumen-ru/signing.properties')).resolve()
if not config.is_file() or config.is_relative_to(ROOT):
    sys.exit('External signing.properties is required and must be outside the repository')
props = dict(x.split('=', 1) for x in config.read_text().splitlines() if '=' in x)
key = Path(props['storeFile']).resolve()
if key.is_relative_to(ROOT):
    sys.exit('Signing key must be outside the repository')
private_values = [key.read_bytes(), props['storePassword'].encode(), props['keyPassword'].encode()]
def private_check(data, label):
    if any(value and value in data for value in private_values):
        sys.exit(f'Private signing material found in {label}; publication stopped')
objects = subprocess.check_output(['git', 'rev-list', '--objects', '--all']).decode().splitlines()
# Read object types first; never print their contents.
ids = [x.split(' ', 1)[0] for x in objects]
proc = subprocess.run(['git', 'cat-file', '--batch'], input=('\n'.join(ids) + '\n').encode(), capture_output=True, check=True)
from io import BytesIO
stream = BytesIO(proc.stdout)
for _ in ids:
    header = stream.readline().decode().strip().split()
    data = stream.read(int(header[2])); stream.read(1)
    if header[1] == 'blob':
        private_check(data, 'Git history blob ' + header[0])
files = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard']).decode().split('\0')
blocked = re.compile(r'(^|/)(local\.properties|signing\.properties|keystore\.properties|\.env(?:\..*)?|\.gradle|\.toolchains|\.deps|\.idea|build|out)(/|$)|\.(jks|keystore|p12|pfx|pem|key)$', re.I)
with tempfile.TemporaryDirectory(prefix='lumen-ru-source-') as directory:
    target = Path(directory)
    count = 0
    for name in sorted(set(files) - {''}):
        source = ROOT / name
        if not source.exists():
            continue
        if blocked.search(name) or source.is_symlink():
            sys.exit(f'Private/local file or symlink would be published: {name}')
        if name.startswith('.github/workflows/'):
            sys.exit('Actions workflow would be published; remove it from the RU branch')
        private_check(source.read_bytes(), name)
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        count += 1
    subprocess.run([scanner, 'dir', str(target), '--redact=100', '--no-banner', '--report-format=json', '--report-path=/tmp/lumen-ru-gitleaks-report.json'], check=True)
subprocess.run([scanner, 'git', str(ROOT), '--log-opts=--all', '--redact=100', '--no-banner', '--report-format=json', '--report-path=/tmp/lumen-ru-gitleaks-report.json'], check=True)
print(f'Secret scan passed: {count} publishable files and all local Git refs; no signing files/local configuration')
