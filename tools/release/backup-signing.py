#!/usr/bin/env python3
"""Create and verify an encrypted local backup; copy backup and recovery file off-server separately."""
from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
import secrets
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
config = Path(os.environ.get('LUMEN_RU_SIGNING_PROPERTIES', ROOT.parent / '.android-release-private/lumen-ru/signing.properties')).resolve()
assert not config.is_relative_to(ROOT), 'Signing properties must live outside repository'
props = dict(line.split('=', 1) for line in config.read_text().splitlines() if '=' in line)
key = Path(props['storeFile']).resolve()
assert key.is_file() and not key.is_relative_to(ROOT)
private = config.parent.parent
backup_dir = private / 'backups/lumen-ru'
backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
recovery = private / 'lumen-ru-backup-recovery.txt'
if not recovery.exists():
    with recovery.open('x') as handle:
        os.chmod(recovery, 0o600)
        handle.write(secrets.token_urlsafe(64) + '\n')
password = recovery.read_bytes().strip()
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode='w') as tar:
    for path, name in [(key, key.name), (config, 'signing.properties'), (ROOT / 'signing-certificate.sha256', 'signing-certificate.sha256')]:
        tar.add(path, arcname=name)
backup = backup_dir / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.tar.gpg')
if backup.exists():
    raise SystemExit('Backup already exists for this second; refusing to overwrite')
# Passphrase is sent via an inherited descriptor; plaintext archive remains only in memory.
def gpg(args, payload):
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, password + b'\n')
        os.close(write_fd)
        return subprocess.check_output(['gpg', '--batch', '--yes', '--pinentry-mode', 'loopback', '--passphrase-fd', str(read_fd), *args], input=payload, pass_fds=(read_fd,))
    finally:
        os.close(read_fd)
# Dedicated GNUPGHOME prevents touching another project's keyring.
gpg_home = private / 'lumen-ru/gpg-backup'
gpg_home.mkdir(mode=0o700, exist_ok=True)
os.environ['GNUPGHOME'] = str(gpg_home)
encrypted = gpg(['--symmetric', '--cipher-algo', 'AES256', '--s2k-mode', '3', '--s2k-count', '65011712', '--output', '-'], archive.getvalue())
with backup.open('xb') as handle:
    os.chmod(backup, 0o600)
    handle.write(encrypted)
assert gpg(['--decrypt', '--output', '-'], encrypted) == archive.getvalue(), 'Backup verification failed'
checksum = backup.with_suffix(backup.suffix + '.sha256')
checksum.write_text(hashlib.sha256(encrypted).hexdigest() + '  ' + backup.name + '\n')
checksum.chmod(0o600)
print(f'Encrypted backup verified: {backup}\nRecovery file: {recovery}\nCopy them to separate secure off-server locations.')
