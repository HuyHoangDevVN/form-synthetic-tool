"""Synthetic Google Forms fixtures. Python 3.8+, standard library only."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import random
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'https://docs.google.com/forms/d/e/1FAIpQLSd1OXNYk755mEzIBDpJmcOgsDD_C7sOOsbvBW4OYjJohIaZgw/'
ROOT = Path(__file__).resolve().parent
SECTOR = 'entry.453271923'


def fetch_form():
    with urlopen(BASE + 'viewform?hl=en', timeout=30) as response:
        html = response.read().decode('utf-8')
    # shortcut: Google's embedded schema is unofficial; stop if its shape changes.
    match = re.search(r'FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);\s*</script>', html, re.S)
    if not match:
        raise ValueError('Public form schema unavailable; sign-in or closed form possible.')
    return html, json.loads(match.group(1))


def load_schema():
    _, data = fetch_form()
    fields = []
    for item in data[1][1]:
        if item[3] == 8:
            continue
        if item[3] != 2 or len(item[4]) != 1:
            raise ValueError('Unsupported question type; review form before submitting.')
        entry = item[4][0]
        options = [option[0] for option in entry[1]]
        if not options or any(not isinstance(value, str) for value in options):
            raise ValueError('Invalid choices.')
        fields.append({'key': 'entry.' + str(entry[0]), 'title': item[1], 'options': options})
    if len(fields) != 31 or SECTOR not in {field['key'] for field in fields}:
        raise ValueError('Form changed: expected 31 questions and the known sector question.')
    return fields


def generate(fields, revised=False):
    rng = random.Random(20261015)
    rows = [{} for _ in range(200)]
    for field in fields:
        choices = field['options']
        values = [choices[index % len(choices)] for index in range(200)]
        if revised and field['key'] == SECTOR:
            counts = {'Sức khỏe': 40, 'Kỹ thuật – Công nghệ': 50,
                      'Kinh tế - Kinh doanh': 44, 'Khoa học xã hội – Nhân văn': 36,
                      'Nghệ thuật và Thiết kế': 30}
            if set(choices) != set(counts):
                raise ValueError('Sector choices changed; review distribution.')
            values = [choice for choice in choices for _ in range(counts[choice])]
        elif revised and field['key'] == 'entry.863899056':
            if set(choices) != {'Nam', 'Nữ', 'Khác'}:
                raise ValueError('Gender choices changed; review distribution.')
            values = ['Nam'] * 92 + ['Nữ'] * 92 + ['Khác'] * 16
        elif revised and len(choices) == 5 and all(
                choice.startswith(str(index) + ' —') for index, choice in enumerate(choices, 1)):
            # Synthetic chart fixture: vary counts per question, not evidence about students.
            counts = [rng.randint(4, 10), rng.randint(8, 14),
                      rng.randint(40, 60), rng.randint(70, 90)]
            counts.append(200 - sum(counts))
            values = [choice for choice, count in zip(choices, counts) for _ in range(count)]
        rng.shuffle(values)
        for row, value in zip(rows, values):
            row[field['key']] = value
    return rows


def topup(fields, baseline):
    rng = random.Random(20261016)
    rows = [{} for _ in range(36)]
    for field in fields:
        choices = field['options']
        counts = Counter(row[field['key']] for row in baseline)
        numerators = [36 * counts[choice] for choice in choices]
        allocated = [value // len(baseline) for value in numerators]
        order = sorted(range(len(choices)),
                       key=lambda i: numerators[i] % len(baseline), reverse=True)
        for index in order[:36 - sum(allocated)]:
            allocated[index] += 1
        values = [choice for choice, count in zip(choices, allocated) for _ in range(count)]
        rng.shuffle(values)
        for row, value in zip(rows, values):
            row[field['key']] = value
    return rows


def validate(fields, rows, balanced=True, expected_count=200):
    if len(rows) != expected_count:
        raise ValueError('Expected exactly {} responses.'.format(expected_count))
    keys = {field['key'] for field in fields}
    for row in rows:
        if set(row) != keys:
            raise ValueError('Missing or extra answer fields.')
        for field in fields:
            if row[field['key']] not in field['options']:
                raise ValueError('Answer outside current form choices.')
    sector = next(field for field in fields if field['key'] == SECTOR)
    counts = Counter(row[SECTOR] for row in rows)
    values = [counts[choice] for choice in sector['options']]
    if balanced and max(values) - min(values) > 1:
        raise ValueError('Sector counts are not balanced.')
    return counts


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def write_rows(path, fields, rows):
    # Never overwrite fixtures that may already have a submission journal.
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=[field['key'] for field in fields])
        writer.writeheader()
        writer.writerows(rows)


def write_distribution(path, fields, rows):
    report = {field['title']: dict(Counter(row[field['key']] for row in rows))
              for field in fields}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


class Confirmation(HTMLParser):
    def __init__(self):
        super().__init__()
        self.confirmed = False

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get('class', '').split()
        if {'vHW8K', 'freebirdFormviewerViewResponseConfirmationMessage'} & set(classes):
            self.confirmed = True


class HiddenInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('type') == 'hidden' and attrs.get('name'):
            self.values[attrs['name']] = attrs.get('value', '')


def build_payload(row, html, data):
    inputs = HiddenInputs()
    inputs.feed(html)
    if not {'fvv', 'fbzx', 'pageHistory'} <= inputs.values.keys():
        raise ValueError('Missing form navigation metadata; stop before submitting.')
    pages = 1 + sum(item[3] == 8 for item in data[1][1])
    payload = dict(inputs.values)
    payload['pageHistory'] = ','.join(str(page) for page in range(pages))
    payload.update(row)
    return payload


def submit(row):
    html, data = fetch_form()
    payload = build_payload(row, html, data)
    request = Request(BASE + 'formResponse?hl=en', data=urlencode(payload).encode('utf-8'),
                      headers={'User-Agent': 'SyntheticFormFixture/1.0',
                               'Content-Type': 'application/x-www-form-urlencoded'})
    with urlopen(request, timeout=30) as response:
        parser = Confirmation()
        parser.feed(response.read().decode('utf-8'))
        if response.status != 200 or not parser.confirmed:
            raise ValueError('No submission confirmation; inspect form manually. Do not retry blindly.')


def remaining_rows(rows, journal, resolution=None):
    states = {}
    if journal.exists():
        for line in journal.read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            if record.get('protocol') != 2:
                raise ValueError('Legacy journal used an incomplete multi-page payload. '
                                 'Its confirmations do not prove saved answers. '
                                 'Reconcile all old responses in Forms/Sheet before a new batch; '
                                 'keep the original journal as evidence.')
            index = record['row']
            if not isinstance(index, int) or not 1 <= index <= len(rows):
                raise ValueError('Invalid journal row; inspect journal manually.')
            if record['status'] not in {'attempting', 'unknown_or_failed',
                                       'confirmation_received', 'reconciled_saved',
                                       'reconciled_not_saved'}:
                raise ValueError('Unknown journal status; inspect journal manually.')
            states[index] = record['status']
    unknown = [index for index, status in states.items()
               if status in {'attempting', 'unknown_or_failed'}]
    if unknown:
        if len(unknown) != 1 or resolution is None:
            raise ValueError('Uncertain rows {}. Check Forms/Sheet, then use '
                             '--resolve-last saved OR --resolve-last not-saved.'.format(unknown))
        index = unknown[0]
        status = 'reconciled_' + resolution.replace('-', '_')
        with journal.open('a', encoding='utf-8') as log:
            log.write(json.dumps({'row': index, 'synthetic': True, 'protocol': 2, 'status': status,
                                  'time': datetime.now(timezone.utc).isoformat()}) + '\n')
        states[index] = status
    elif resolution:
        raise ValueError('No uncertain row to reconcile; omit --resolve-last.')
    return [(index, row) for index, row in enumerate(rows, 1)
            if states.get(index) not in {'confirmation_received', 'reconciled_saved'}]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'revise', 'extend', 'send', 'diagnose'])
    parser.add_argument('--limit', type=int, default=200,
                        help='Maximum pending rows to send this run (1-200)')
    parser.add_argument('--revised', action='store_true',
                        help='Send responses-revised-v2.csv with its separate journal')
    parser.add_argument('--topup', action='store_true', help='Send the separate 36-row top-up CSV')
    parser.add_argument('--resolve-last', choices=['saved', 'not-saved'],
                        help='Operator reconciliation of the uncertain row against Forms/Sheet')
    args = parser.parse_args()
    if args.topup and (args.revised or args.action != 'send'):
        parser.error('--topup is only valid with send and without --revised')
    if args.revised and args.action not in {'prepare', 'send'}:
        parser.error('--revised is only valid with prepare or send')
    if args.resolve_last and args.action != 'send':
        parser.error('--resolve-last is only valid with send')
    if not 1 <= args.limit <= 200:
        parser.error('--limit must be between 1 and 200')
    if args.action == 'diagnose':
        html, data = fetch_form()
        payload = build_payload({}, html, data)
        print('Read-only check. Page history: ' + payload['pageHistory'])
        print('Required navigation metadata present. No responses submitted.')
        return
    fields = load_schema()
    if args.action == 'extend':
        baseline = read_rows(ROOT / 'responses-revised-v2.csv')
        validate(fields, baseline, balanced=False)
        rows = topup(fields, baseline)
        validate(fields, rows, balanced=False, expected_count=36)
        write_rows(ROOT / 'responses-topup-36.csv', fields, rows)
        write_distribution(ROOT / 'distribution-total-236.json', fields, baseline + rows)
        print('Prepared 36 top-up rows and distribution-total-236.json. Nothing submitted.')
        return
    if args.action == 'revise':
        rows = generate(fields, revised=True)
        validate(fields, rows, balanced=False)
        fixture = ROOT / 'responses-revised-v2.csv'
        write_rows(fixture, fields, rows)
        write_distribution(ROOT / 'distribution-revised-v2.json', fields, rows)
        print('Created responses-revised-v2.csv and distribution-revised-v2.json. Nothing submitted.')
        return
    fixture = ROOT / ('responses-revised-v2.csv' if args.revised else 'responses.csv')
    journal = ROOT / ('send-log-revised-v2.jsonl' if args.revised else 'send-log.jsonl')
    if args.topup:
        fixture = ROOT / 'responses-topup-36.csv'
        journal = ROOT / 'send-log-topup-36.jsonl'
    if args.action == 'prepare':
        if fixture.exists():
            rows = read_rows(fixture)
            validate(fields, rows, balanced=not args.revised)
            print('Existing CSV is valid; kept unchanged. Use: python tool.py send')
            return
        rows = generate(fields, revised=args.revised)
        counts = validate(fields, rows, balanced=not args.revised)
        write_rows(fixture, fields, rows)
        (ROOT / 'questions.json').write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(counts), ensure_ascii=True, indent=2))
        print('Prepared 200 synthetic responses. Nothing submitted.')
        return
    rows = read_rows(fixture)
    validate(fields, rows, balanced=not (args.revised or args.topup),
             expected_count=36 if args.topup else 200)
    pending = remaining_rows(rows, journal, args.resolve_last)
    if not pending:
        print('All rows already confirmed or reconciled as saved. Nothing submitted.')
        return
    pending = pending[:args.limit]
    with journal.open('a', encoding='utf-8') as log:
        for index, row in pending:
            record = {'row': index, 'synthetic': True, 'protocol': 2,
                      'time': datetime.now(timezone.utc).isoformat(), 'status': 'attempting'}
            log.write(json.dumps(record) + '\n')
            log.flush()
            try:
                submit(row)
            except (Exception, KeyboardInterrupt):
                record['status'] = 'unknown_or_failed'
                log.write(json.dumps(record) + '\n')
                log.flush()
                raise
            record['status'] = 'confirmation_received'
            log.write(json.dumps(record) + '\n')
            log.flush()
            print('{}/{} confirmation received'.format(index, len(rows)), flush=True)
            if index < len(rows):
                time.sleep(0.5)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, FileExistsError) as error:
        raise SystemExit(str(error))
    except KeyboardInterrupt:
        raise SystemExit('Interrupted. Check the uncertain row in Forms/Sheet before resuming.')
