#!/usr/bin/env python3
"""Local, signed Lumen RU release pipeline. No Actions, force push, or upstream writes."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/release'))
# Hyphenated filename is kept directly callable from the shell.
import importlib.util
spec = importlib.util.spec_from_file_location('verify_apk', ROOT / 'tools/release/verify-apk.py')
verify_apk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_apk)
REPO = 's0dom667-maker/Lumen-RU'
ORIGIN = f'https://github.com/{REPO}.git'


def run(*args, capture=False, **kwargs):
    print('+', ' '.join(str(x) for x in args), flush=True)
    if capture:
        return subprocess.check_output([str(x) for x in args], text=True, **kwargs).strip()
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def fail(message):
    raise RuntimeError(message)


def clean():
    if run('git', 'status', '--porcelain', capture=True):
        fail('Commit the source changes first. A published release requires a clean checkout.')
    if run('git', 'branch', '--show-current', capture=True) != 'main':
        fail('Publish only from main; merge the reviewed update branch first.')
    for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD'):
        if (ROOT / '.git' / name).exists():
            fail('Finish the current Git operation before releasing.')


def environment():
    # An explicitly provided JDK wins; otherwise prefer the isolated local JDK 21 installation.
    candidates = sorted((ROOT / '.toolchains').glob('jdk-21*'))
    if 'JAVA_HOME' not in os.environ and candidates:
        os.environ['JAVA_HOME'] = str(candidates[-1])
    if os.environ.get('JAVA_HOME'):
        os.environ['PATH'] = os.environ['JAVA_HOME'] + '/bin:' + os.environ['PATH']
    sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if not sdk and (ROOT / 'local.properties').is_file():
        sdk = verify_apk.properties(ROOT / 'local.properties').get('sdk.dir')
    sdk = Path(sdk or Path.home() / 'Android/Sdk').resolve()
    if not (sdk / 'build-tools').is_dir():
        fail('Android SDK missing; set ANDROID_HOME. See RELEASE.md.')
    os.environ['ANDROID_HOME'] = str(sdk)
    os.environ['ANDROID_SDK_ROOT'] = str(sdk)
    return sdk


def check_identity():
    if run('git', 'remote', 'get-url', 'origin', capture=True).removesuffix('.git') != ORIGIN.removesuffix('.git'):
        fail('origin must be the HTTPS URL of Lumen-RU')
    if run('git', 'remote', 'get-url', '--push', 'origin', capture=True).removesuffix('.git') != ORIGIN.removesuffix('.git'):
        fail('origin push URL must be the HTTPS URL of Lumen-RU')
    if run('gh', 'api', 'user', '--jq', '.login', capture=True) != 's0dom667-maker':
        fail('GitHub CLI must be authenticated as s0dom667-maker')
    run('git', 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main', 'refs/tags/*:refs/tags/*')
    run('git', 'merge-base', '--is-ancestor', 'refs/remotes/origin/main', 'HEAD')


def build(sdk):
    provenance = verify_apk.properties(ROOT / 'upstream.properties')
    run('git', 'merge-base', '--is-ancestor', provenance['commit'], 'HEAD')
    if run('git', 'show', provenance['commit'] + ':LICENSE', capture=True) != (ROOT / 'LICENSE').read_text().strip():
        fail('Upstream license was changed; review required')
    original_notice = run('git', 'show', provenance['commit'] + ':NOTICE', capture=True)
    if not (ROOT / 'NOTICE').read_text().startswith(original_notice):
        fail('Original copyright notices must be retained')
    run('tools/release/prepare-deps.sh')
    run('python3', 'tools/release/check-translations.py')
    run('python3', 'tools/release/check-secrets.py')
    run('python3', 'tools/release/test-release.py')
    # Avoid saving external signing passwords in Gradle's local configuration cache.
    run('./gradlew', ':app:assembleRelease', ':app:testDebugUnitTest', ':app:lintRelease', '--no-daemon', '--no-configuration-cache', '--max-workers=2')
    apk = ROOT / 'app/build/outputs/apk/release/app-universal-release.apk'
    report = verify_apk.verify(apk, sdk)
    version = report['versionName']
    dest = ROOT / 'out' / version
    dest.mkdir(parents=True, exist_ok=True)
    named_apk = dest / f'Lumen-RU-{version}-universal.apk'
    shutil.copyfile(apk, named_apk)
    (dest / 'SHA256SUMS.txt').write_text(f'{report["sha256"]}  {named_apk.name}\n')
    report['sourceCommit'] = run('git', 'rev-parse', 'HEAD', capture=True)
    report['workingTreeDirty'] = bool(run('git', 'status', '--porcelain', capture=True))
    report['builtAtUtc'] = datetime.now(timezone.utc).isoformat()
    (dest / 'BUILD.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(f'Verified APK: {named_apk}\nSHA-256: {report["sha256"]}', flush=True)
    return dest, report


def ensure_monotonic(version, code):
    for tag in run('git', 'tag', '--list', capture=True).splitlines():
        result = subprocess.run(['git', 'show', f'{tag}:version.properties'], capture_output=True, text=True)
        if result.returncode:
            continue  # Original upstream tag, before RU had an independent version file.
        old = dict(x.split('=', 1) for x in result.stdout.splitlines() if '=' in x)
        if old['versionName'] == version:
            if int(old['versionCode']) != code:
                fail('Existing tag uses this versionName with another versionCode')
            continue  # Retrying --publish-current.
        if int(old['versionCode']) >= code:
            fail(f'versionCode must exceed every previous RU release ({tag})')


def publish(dest, report):
    clean()
    version = report['versionName']
    tag = 'v' + version
    head = run('git', 'rev-parse', 'HEAD', capture=True)
    tag_exists = subprocess.run(['git', 'rev-parse', '--verify', f'refs/tags/{tag}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if tag_exists:
        if run('git', 'rev-list', '-1', tag, capture=True) != head:
            fail('Existing tag points to another commit; it will not be moved')
    else:
        run('git', 'tag', '-a', tag, '-m', f'Lumen RU {version}; based on Lumen {report["upstream"]["version"]}')
    run('git', 'push', '--no-follow-tags', '--atomic', 'origin', 'HEAD:refs/heads/main', f'refs/tags/{tag}:refs/tags/{tag}')
    upstream = report['upstream']
    notes = dest / 'RELEASE-NOTES.md'
    notes.write_text(
        f'Lumen RU {version} — русская сборка Lumen.\n\n'
        f'Основано на [Lumen {upstream["version"]}](https://github.com/subhashhhhhh/Lumen/tree/{upstream["commit"]}), '
        f'исходный коммит `{upstream["commit"]}`.\n\n'
        f'- Русский интерфейс, включая уведомления и формы множественного числа.\n'
        f'- Отдельный пакет `{verify_apk.APP_ID}`, название Lumen RU; устанавливается рядом с официальным Lumen.\n'
        f'- Модели распознавания и исходная функциональность сохранены.\n'
        f'- Постоянная release-подпись; versionCode: {report["versionCode"]}.\n'
        f'- Универсальный APK: arm64-v8a, armeabi-v7a, x86_64; Android 11 и новее.\n'
        f'- Проверены APK, русские ресурсы, FileProvider, Android Lint с baseline исходного проекта и модульные тесты ({report["unitTests"]}).\n\n'
        f'Обновления Obtainium: https://github.com/{REPO}\n'
        f'Фильтр APK (при необходимости): `^Lumen-RU-.*-universal\\.apk$`\n\n'
        f'SHA-256 APK: `{report["sha256"]}`\n\n'
        f'SHA-256 сертификата подписи: `{report["signingCertificateSha256"]}`\n\n'
        f'Lumen © 2026 Subhash Gottumukkala. Локализация и сборка RU: s0dom667-maker. Apache-2.0.\n'
    )
    existing = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPO, '--json', 'isDraft,isPrerelease,targetCommitish'], capture_output=True, text=True)
    if existing.returncode == 0:
        release = json.loads(existing.stdout)
        if not release['isDraft']:
            fail(f'Release {tag} already published. Nothing will be replaced or deleted.')
    else:
        run('gh', 'release', 'create', tag, '--repo', REPO, '--verify-tag', '--draft', '--title', f'Lumen RU {version}', '--notes-file', notes)
    # Resume a partial upload by comparing asset checksums; never overwrite existing assets.
    # GitHub's /releases/tags endpoint excludes drafts; gh resolves them through the release list.
    release_api = json.loads(run('gh', 'release', 'view', tag, '--repo', REPO, '--json', 'apiUrl', capture=True))['apiUrl']
    if not release_api.startswith(f'https://api.github.com/repos/{REPO}/releases/'):
        fail('Unexpected release API URL')
    assets = json.loads(run('gh', 'api', release_api, capture=True))['assets']
    assets = {a['name']: a for a in assets}
    for path in [dest / f'Lumen-RU-{version}-universal.apk', dest / 'SHA256SUMS.txt', dest / 'BUILD.json']:
        if path.name in assets:
            expected = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
            if assets[path.name].get('digest') != expected:
                fail(f'Draft asset {path.name} differs. No asset will be overwritten; inspect the draft.')
        else:
            run('gh', 'release', 'upload', tag, path, '--repo', REPO)
    # Stable releases become visible only after all three verified assets have uploaded.
    run('gh', 'release', 'edit', tag, '--repo', REPO, '--draft=false', '--prerelease=false', '--latest', '--notes-file', notes)
    print(f'Published: https://github.com/{REPO}/releases/tag/{tag}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['--build-only', 'patch', 'minor', '--publish-current'], nargs='?')
    # Treat --build-only/--publish-current as positional modes without argparse interpreting them.
    if sys.argv[1:] in (['--help'], ['-h']):
        parser.print_help()
        return 0
    if len(sys.argv) != 2 or sys.argv[1] not in ('--build-only', 'patch', 'minor', '--publish-current'):
        parser.print_help()
        return 2
    mode = sys.argv[1]
    os.chdir(ROOT)
    with (ROOT / '.release.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sdk = environment()
        if mode == '--build-only':
            build(sdk)
            return 0
        clean()
        check_identity()
        current = verify_apk.properties(ROOT / 'version.properties')['versionName']
        if mode in ('patch', 'minor'):
            tagged = subprocess.run(['git', 'rev-parse', '--verify', 'refs/tags/v' + current], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            version_commit = run('git', 'log', '-1', '--format=%s', capture=True) == 'Release Lumen RU ' + current
            if tagged or version_commit:
                result = subprocess.run(['gh', 'release', 'view', 'v' + current, '--repo', REPO, '--json', 'isDraft'], capture_output=True, text=True)
                if result.returncode or json.loads(result.stdout)['isDraft']:
                    fail('Current version has an unpublished tag; retry ./release.sh --publish-current')
        previous = (ROOT / 'version.properties').read_bytes()
        changed = False
        if mode in ('patch', 'minor'):
            version = verify_apk.properties(ROOT / 'version.properties')
            major, minor, patch = map(int, version['versionName'].split('.'))
            if mode == 'minor':
                minor, patch = minor + 1, 0
            else:
                patch += 1
            number = f'{major}.{minor}.{patch}'
            code = int(version['versionCode']) + 1
            (ROOT / 'version.properties').write_text(f'versionName={number}\nversionCode={code}\n')
            changed = True
        try:
            version = verify_apk.properties(ROOT / 'version.properties')
            if not re.fullmatch(r'\d+\.\d+\.\d+', version['versionName']):
                fail('versionName must be a stable major.minor.patch version')
            ensure_monotonic(version['versionName'], int(version['versionCode']))
            saved = ROOT / 'out' / version['versionName'] / 'BUILD.json'
            tag_exists = subprocess.run(['git', 'rev-parse', '--verify', 'refs/tags/v' + version['versionName']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if mode != '--publish-current' and subprocess.run(['git', 'rev-parse', '--verify', 'refs/tags/v' + verify_apk.properties(ROOT / 'version.properties')['versionName']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                fail('Version tag already exists; inspect it before publishing')
            if mode == '--publish-current' and tag_exists and saved.is_file():
                # Keep exactly the previous upload bytes when retrying a partially uploaded draft.
                dest = saved.parent
                report = json.loads(saved.read_text())
                if report['sourceCommit'] != run('git', 'rev-parse', 'HEAD', capture=True) or report['workingTreeDirty']:
                    fail('Saved build does not belong to this clean commit')
                checked = verify_apk.verify(dest / f'Lumen-RU-{version["versionName"]}-universal.apk', sdk)
                if checked['sha256'] != report['sha256']:
                    fail('Saved APK checksum changed')
                run('tools/release/prepare-deps.sh')
                run('python3', 'tools/release/check-translations.py')
                run('python3', 'tools/release/check-secrets.py')
            else:
                dest, report = build(sdk)
        except BaseException:
            if changed:
                (ROOT / 'version.properties').write_bytes(previous)
            raise
        if changed:
            run('git', 'add', 'version.properties')
            run('git', 'commit', '-m', f'Release Lumen RU {version["versionName"]}')
        report['sourceCommit'] = run('git', 'rev-parse', 'HEAD', capture=True)
        report['workingTreeDirty'] = False
        (dest / 'BUILD.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        publish(dest, report)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.CalledProcessError, BlockingIOError) as error:
        print(f'Release stopped: {error}\nIf the version commit/tag was created, retry with ./release.sh --publish-current.', file=sys.stderr)
        sys.exit(1)
