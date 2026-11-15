#!/usr/bin/env python3
"""Fail release on missing/stale Russian text, bad formats, or missing Russian plural forms."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / 'tools/release/translation-source.json'
FORMAT = re.compile(r'%(?:\d+\$)?[-#+ 0,(]*\d*(?:\.\d+)?[a-zA-Z%]')


def resources(path):
    result = {}
    for path in sorted(path.glob('*.xml')):
        for element in ET.parse(path).getroot():
            if element.tag not in ('string', 'plurals', 'string-array'):
                continue
            key = element.attrib['name']
            if key in result:
                raise ValueError(f'Duplicate resource: {key} in {path}')
            result[key] = element
    return result


def text(element):
    return ''.join(element.itertext()).strip()


def placeholders(value):
    return collections.Counter(x for x in FORMAT.findall(value) if x not in ('%%', '%n'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-source', action='store_true', help='After reviewing translations, record current English text')
    args = parser.parse_args()
    errors, snapshot, translated = [], {}, 0
    for name, base in [('app', ROOT / 'app/src/main/res'), ('engine', ROOT / '.deps/lumen-ai/ai/src/main/res')]:
        source, russian = resources(base / 'values'), resources(base / 'values-ru')
        if not source or not russian:
            errors.append(f'{name}: resources missing; prepare dependencies first')
        for key, element in source.items():
            if element.attrib.get('translatable') == 'false':
                continue
            snapshot[f'{name}/{key}'] = ET.tostring(element, encoding='unicode').strip()
            target = russian.get(key)
            if target is None:
                errors.append(f'{name}: missing Russian translation: {key}')
                continue
            if target.tag != element.tag:
                errors.append(f'{name}: changed resource type: {key}')
                continue
            translated += 1
            if element.tag == 'string':
                pairs = [(element, target)]
            elif element.tag == 'plurals':
                forms = {x.attrib['quantity']: x for x in target}
                if set(forms) != {'one', 'few', 'many', 'other'}:
                    errors.append(f'{name}: need one/few/many/other: {key}')
                expected = element.find("item[@quantity='other']")
                pairs = [(expected, x) for x in forms.values()]
            else:
                if len(element) != len(target):
                    errors.append(f'{name}: wrong array size: {key}')
                pairs = list(zip(element, target))
            for en, ru in pairs:
                if not text(ru):
                    errors.append(f'{name}: empty translation: {key}')
                if placeholders(text(en)) != placeholders(text(ru)):
                    errors.append(f'{name}: format placeholders differ: {key}')
        for key in russian.keys() - source.keys():
            errors.append(f'{name}: obsolete Russian resource: {key}')
    if not args.record_source:
        saved = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {}
        for key in snapshot.keys() | saved.keys():
            if saved.get(key) != snapshot.get(key):
                errors.append(f'English text changed; review Russian and --record-source: {key}')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    if args.record_source:
        SNAPSHOT.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True) + '\n')
    print(f'Russian translations OK: {translated} resources; placeholders and plural forms checked')
    return 0


if __name__ == '__main__':
    sys.exit(main())
