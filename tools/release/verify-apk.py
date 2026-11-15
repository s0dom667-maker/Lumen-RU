#!/usr/bin/env python3
"""Verify actual APK identity, signing certificate, providers, native ABIs and Russian resources."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
APP_ID = 'io.github.s0dom667maker.lumenru'


def properties(path):
    return dict(line.split('=', 1) for line in Path(path).read_text().splitlines() if line.strip() and not line.lstrip().startswith('#'))


def output(*command):
    return subprocess.check_output([str(x) for x in command], text=True)


def verify(apk, sdk):
    tools = max((sdk / 'build-tools').iterdir(), key=lambda p: tuple(int(x) for x in p.name.split('.') if x.isdigit()))
    signature = output(tools / 'apksigner', 'verify', '--verbose', '--print-certs', apk)
    assert 'Number of signers: 1' in signature, 'Expected exactly one APK signer'
    certificate = re.search(r'certificate SHA-256 digest: ([0-9a-f]+)', signature).group(1)
    assert certificate == (ROOT / 'signing-certificate.sha256').read_text().strip(), 'Wrong signing certificate'
    assert 'Verified using v2 scheme (APK Signature Scheme v2): true' in signature, 'APK v2 signature missing'
    badging = output(tools / 'aapt', 'dump', 'badging', apk)
    version = properties(ROOT / 'version.properties')
    for key, expected in [('name', APP_ID), ('versionCode', version['versionCode']), ('versionName', version['versionName'])]:
        assert re.search(rf"{key}='([^']+)'", badging).group(1) == expected, f'Wrong APK {key}'
    assert 'application-debuggable' not in badging, 'APK must not be debuggable'
    assert "application-label:'Lumen RU'" in badging
    assert "application-label-ru:'Lumen RU'" in badging
    manifest = output(tools / 'aapt', 'dump', 'xmltree', apk, 'AndroidManifest.xml')
    authorities = re.findall(r'android:authorities[^=]*="([^"]+)"', manifest)
    assert f'{APP_ID}.files' in authorities, 'Missing FileProvider'
    assert authorities and all(x.startswith(APP_ID + '.') for x in authorities), f'Provider authority collision: {authorities}'
    permissions = re.findall(r'android:name[^=]*="([^"]+DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION)"', manifest)
    assert permissions and all(x.startswith(APP_ID + '.') for x in permissions), 'Custom permission collision'
    resources = output(tools / 'aapt', 'dump', '--values', 'resources', apk)
    assert 'config ru' in resources and 'Настройки' in resources, 'Russian strings missing in compiled APK'
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        assert 'assets/lumen-ru/LICENSE' in names and 'assets/lumen-ru/NOTICE' in names
        for abi in ('arm64-v8a', 'armeabi-v7a', 'x86_64'):
            assert any(n.startswith(f'lib/{abi}/') for n in names), f'Missing ABI: {abi}'
    tests = [ET.parse(p).getroot().attrib for p in (ROOT / 'app/build/test-results/testDebugUnitTest').glob('TEST-*.xml')]
    assert tests and all(int(t.get('failures', 0)) == int(t.get('errors', 0)) == 0 for t in tests), 'No passing unit test results'
    lint = ET.parse(ROOT / 'app/build/reports/lint-results-release.xml').getroot()
    assert not any(e.get('severity') in ('Error', 'Fatal') for e in lint), 'Unresolved Android Lint errors'
    return {
        'applicationId': APP_ID, 'versionName': version['versionName'], 'versionCode': int(version['versionCode']),
        'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'signingCertificateSha256': certificate,
        'providerAuthorities': sorted(set(authorities)), 'russianResources': True,
        'unitTests': sum(int(t['tests']) for t in tests),
        'lint': {'baselineEntries': len(ET.parse(ROOT / 'app/lint-baseline-upstream.xml').getroot()), 'newErrors': 0, 'warnings': sum(e.get('severity') == 'Warning' for e in lint)},
        'upstream': properties(ROOT / 'upstream.properties'),
        'lumenAiCommit': (ROOT / '.github/lumen-ai.commit').read_text().strip(),
        'notificationPatchSha256': hashlib.sha256((ROOT / 'tools/release/lumen-ai-ru.patch').read_bytes()).hexdigest(),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = verify(args.apk.resolve(), args.sdk.resolve())
    data = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.report:
        args.report.write_text(data)
    print(data)
