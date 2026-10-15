"""Configurable synthetic fixtures for authorized Google Forms testing. Python 3.8+."""
import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import random
import re
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
PROTOCOL = 3
SAVED = {'confirmation_received', 'reconciled_saved'}
UNCERTAIN = {'attempting', 'unknown_or_failed'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def form_base(url):
    if not isinstance(url, str):
        raise ValueError('form_url must be a Google Forms URL.')
    parsed = urlsplit(url)
    match = re.fullmatch(r'/forms/d/e/([A-Za-z0-9_-]+)/(?:viewform|formResponse)?', parsed.path)
    if parsed.scheme != 'https' or parsed.netloc != 'docs.google.com' or not match:
        raise ValueError('Use the full https://docs.google.com/forms/d/e/ID/viewform URL.')
    return 'https://docs.google.com/forms/d/e/{}/'.format(match.group(1))


def weights(values):
    if not isinstance(values, list) or not values:
        raise ValueError('Weights must be a nonempty list.')
    if any(type(value) not in (int, float) for value in values):
        raise ValueError('Weights must be numbers, not booleans or strings.')
    numbers = [Decimal(str(value)) for value in values]
    if any(not value.is_finite() or value < 0 for value in numbers) or sum(numbers) <= 0:
        raise ValueError('Weights must be finite, nonnegative, with a positive total.')
    return numbers


def load_config(path):
    return validate_config(json.loads(path.read_text(encoding='utf-8-sig')))


def validate_config(config):
    allowed = {'form_url', 'batch_id', 'count', 'seed', 'distributions', 'likert_weights'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('Unknown config keys; see config.example.json.')
    for key in ('form_url', 'batch_id', 'count'):
        if key not in config:
            raise ValueError('Missing config key: ' + key)
    config['form_url'] = form_base(config['form_url'])
    if not isinstance(config['batch_id'], str) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', config['batch_id']):
        raise ValueError('batch_id must be 1-64 letters, digits, underscores or hyphens.')
    if type(config['count']) is not int or config['count'] <= 0:
        raise ValueError('count must be a positive integer.')
    config.setdefault('seed', 20261015)
    if type(config['seed']) is not int:
        raise ValueError('seed must be an integer.')
    config.setdefault('distributions', {})
    if not isinstance(config['distributions'], dict):
        raise ValueError('distributions must map entry IDs to choice weights.')
    for key, rule in config['distributions'].items():
        if not re.fullmatch(r'entry\.\d+', key) or not isinstance(rule, dict) or not rule:
            raise ValueError('Each distribution needs an entry.ID and choice-to-weight object.')
        weights(list(rule.values()))
    if 'likert_weights' in config:
        weights(config['likert_weights'])
        if len(config['likert_weights']) != 5:
            raise ValueError('likert_weights must contain weights for levels 1-5.')
    return config


def fetch_form(base):
    with urlopen(base + 'viewform?hl=en', timeout=30) as response:
        html = response.read().decode('utf-8')
    # shortcut: Google's embedded schema is unofficial; stop if its shape changes.
    match = re.search(r'FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);\s*</script>', html, re.S)
    if not match:
        raise ValueError('Public form schema unavailable; sign-in or closed form possible.')
    return html, json.loads(match.group(1))


def parse_schema(data):
    fields = []
    for item in data[1][1]:
        if item[3] == 8 and len(item) > 5 and item[5] not in (None, -2):
            raise ValueError('Section branching is not supported.')
        if item[3] in (6, 8):
            continue
        if item[3] not in (2, 3) or len(item[4]) != 1:
            raise ValueError('Only single-choice and dropdown questions are supported.')
        entry = item[4][0]
        if any((len(option) > 2 and option[2] is not None) or
               (len(option) > 4 and option[4]) for option in entry[1]):
            raise ValueError('Branching and free-text Other choices are not supported.')
        options = [option[0] for option in entry[1]]
        if not options or any(not isinstance(value, str) for value in options) or len(set(options)) != len(options):
            raise ValueError('Invalid or duplicate choices.')
        fields.append({'key': 'entry.' + str(entry[0]), 'title': item[1], 'options': options})
    if not fields or len({field['key'] for field in fields}) != len(fields):
        raise ValueError('Missing questions or duplicate entry IDs.')
    return fields


def allocate(count, values):
    numbers = weights(values)
    quotas = [count * value / sum(numbers) for value in numbers]
    counts = [int(value) for value in quotas]
    order = sorted(range(len(counts)), key=lambda i: quotas[i] - counts[i], reverse=True)
    for index in order[:count - sum(counts)]:
        counts[index] += 1
    return counts


def field_weights(field, config):
    choices = field['options']
    rule = config['distributions'].get(field['key'])
    if rule is not None:
        if set(rule) != set(choices):
            raise ValueError('Distribution choices differ from form: ' + field['key'])
        return [rule[choice] for choice in choices]
    if 'likert_weights' in config and is_likert(field):
        return config['likert_weights']
    return [1] * len(choices)


def is_likert(field):
    return len(field['options']) == 5 and all(
        re.match(r'^{}(?:\s*[—–-]|$)'.format(i), choice)
        for i, choice in enumerate(field['options'], 1))


def generate(fields, config):
    unknown = set(config['distributions']) - {field['key'] for field in fields}
    if unknown:
        raise ValueError('Unknown distribution entry IDs: ' + ', '.join(sorted(unknown)))
    rng = random.Random(config['seed'])
    rows = [{} for _ in range(config['count'])]
    for field in fields:
        counts = allocate(config['count'], field_weights(field, config))
        values = [choice for choice, count in zip(field['options'], counts) for _ in range(count)]
        rng.shuffle(values)
        for row, value in zip(rows, values):
            row[field['key']] = value
    return rows


def validate(fields, rows, count):
    if len(rows) != count:
        raise ValueError('Expected exactly {} responses.'.format(count))
    keys = {field['key'] for field in fields}
    for row in rows:
        if set(row) != keys:
            raise ValueError('Missing or extra answer fields.')
        for field in fields:
            if row[field['key']] not in field['options']:
                raise ValueError('Answer outside current choices: ' + field['key'])


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def write_rows(path, fields, rows):
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=[field['key'] for field in fields])
        writer.writeheader()
        writer.writerows(rows)


def batch_path(config):
    form_id = config['form_url'].rstrip('/').split('/')[-1]
    return ROOT / '.batches' / form_id / config['batch_id']


def load_batch(folder, config, fields):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('config') != config or manifest.get('fields') != fields:
        raise ValueError('Batch config or live schema changed. Restore the original before resuming.')
    rows = read_rows(folder / 'responses.csv')
    validate(fields, rows, config['count'])
    identity = digest({'config': config, 'fields': fields, 'rows': rows})
    if manifest.get('batch_hash') != identity:
        raise ValueError('CSV or manifest changed; stop to avoid mismatched submission history.')
    return rows, identity


def prepare(folder, config, fields):
    if folder.exists():
        load_batch(folder, config, fields)
        print('Existing batch is valid; CSV and journal kept unchanged.')
        return
    rows = generate(fields, config)
    validate(fields, rows, config['count'])
    folder.mkdir(parents=True, exist_ok=False)
    write_rows(folder / 'responses.csv', fields, rows)
    manifest = {'config': config, 'fields': fields,
                'batch_hash': digest({'config': config, 'fields': fields, 'rows': rows})}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    report = {field['key']: {'title': field['title'],
                            'counts': dict(Counter(row[field['key']] for row in rows))}
              for field in fields}
    (folder / 'distribution.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Prepared {} synthetic rows. Nothing submitted.'.format(len(rows)))


class FormHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = {}
        self.confirmed = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('type') == 'hidden' and attrs.get('name'):
            self.hidden[attrs['name']] = attrs.get('value', '')
        classes = set(attrs.get('class', '').split())
        if classes & {'vHW8K', 'freebirdFormviewerViewResponseConfirmationMessage'}:
            self.confirmed = True


def build_payload(row, html, data):
    parser = FormHTML()
    parser.feed(html)
    if not {'fvv', 'fbzx', 'pageHistory'} <= parser.hidden.keys():
        raise ValueError('Missing form navigation metadata; stop before submitting.')
    pages = 1 + sum(item[3] == 8 for item in data[1][1])
    payload = dict(parser.hidden)
    payload['pageHistory'] = ','.join(str(page) for page in range(pages))
    payload.update(row)
    return payload


def submit(base, row, fields):
    html, data = fetch_form(base)
    if parse_schema(data) != fields:
        raise ValueError('Form schema changed during sending; stop before POST.')
    payload = build_payload(row, html, data)
    request = Request(base + 'formResponse?hl=en', data=urlencode(payload).encode('utf-8'),
                      headers={'User-Agent': 'SyntheticFormFixture/1.0',
                               'Content-Type': 'application/x-www-form-urlencoded'})
    with urlopen(request, timeout=30) as response:
        parser = FormHTML()
        parser.feed(response.read().decode('utf-8'))
        if response.status != 200 or not parser.confirmed:
            raise ValueError('No submission confirmation. Check Forms/Sheet; do not retry blindly.')


@contextmanager
def batch_lock(folder):
    path = folder / 'send.lock'
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError('Batch is locked. Check for another process; do not remove an active lock.')
    try:
        os.close(descriptor)
        yield
    finally:
        path.unlink()


def append_record(log, identity, row, status):
    record = {'protocol': PROTOCOL, 'batch_hash': identity, 'row': row,
              'synthetic': True, 'status': status,
              'time': datetime.now(timezone.utc).isoformat()}
    log.write(json.dumps(record) + '\n')
    log.flush()
    os.fsync(log.fileno())


def remaining_rows(rows, journal, identity):
    states = {}
    if journal.exists():
        for line in journal.read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            if record.get('protocol') != PROTOCOL or record.get('batch_hash') != identity:
                raise ValueError('Legacy or mismatched journal; keep it and reconcile in Forms/Sheet.')
            index, status = record['row'], record['status']
            if type(index) is not int or not 1 <= index <= len(rows) or status not in (
                    SAVED | UNCERTAIN | {'reconciled_not_saved'}):
                raise ValueError('Invalid journal record; inspect it before resuming.')
            states[index] = status
    unknown = [index for index, status in states.items() if status in UNCERTAIN]
    pending = [(index, row) for index, row in enumerate(rows, 1) if states.get(index) not in SAVED]
    return pending, unknown


def send_batch(folder, config, fields, limit=None, resolution=None, stop=None, progress=None):
    with batch_lock(folder):
        rows, identity = load_batch(folder, config, fields)
        journal = folder / 'send-log.jsonl'
        pending, unknown = remaining_rows(rows, journal, identity)
        if unknown and (len(unknown) != 1 or resolution is None):
            raise ValueError('Uncertain rows {}. Check Forms/Sheet, then use '
                             '--resolve-last saved OR --resolve-last not-saved.'.format(unknown))
        if not unknown and resolution:
            raise ValueError('No uncertain row to reconcile; omit --resolve-last.')
        with journal.open('a', encoding='utf-8') as log:
            if unknown:
                append_record(log, identity, unknown[0], 'reconciled_' + resolution.replace('-', '_'))
                if resolution == 'saved':
                    pending = [(index, row) for index, row in pending if index != unknown[0]]
            pending = pending[:limit]
            if not pending:
                print('All rows already confirmed or reconciled as saved. Nothing submitted.')
                return
            for position, (index, row) in enumerate(pending):
                if stop is not None and stop.is_set():
                    print('Stopped before the next row. Confirmed rows kept in journal.')
                    return
                append_record(log, identity, index, 'attempting')
                try:
                    submit(config['form_url'], row, fields)
                except (Exception, KeyboardInterrupt):
                    append_record(log, identity, index, 'unknown_or_failed')
                    raise
                append_record(log, identity, index, 'confirmation_received')
                if progress is not None:
                    progress(index, len(rows))
                print('{}/{} confirmation received'.format(index, len(rows)), flush=True)
                if position < len(pending) - 1:
                    time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'send', 'diagnose'])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--limit', type=int, help='Maximum pending rows to send this run')
    parser.add_argument('--resolve-last', choices=['saved', 'not-saved'])
    args = parser.parse_args()
    if args.limit is not None and (args.limit <= 0 or args.action != 'send'):
        parser.error('--limit must be positive and is only valid with send')
    if args.resolve_last and args.action != 'send':
        parser.error('--resolve-last is only valid with send')
    config = load_config(args.config)
    html, data = fetch_form(config['form_url'])
    fields = parse_schema(data)
    if args.action == 'diagnose':
        for field in fields:
            field_weights(field, config)
        unknown = set(config['distributions']) - {field['key'] for field in fields}
        if unknown:
            raise ValueError('Unknown distribution entry IDs: ' + ', '.join(sorted(unknown)))
        print(json.dumps(fields, ensure_ascii=True, indent=2))
        print('Page history: ' + build_payload({}, html, data)['pageHistory'])
        print('Read-only check. Nothing submitted.')
        return
    folder = batch_path(config)
    if args.action == 'prepare':
        prepare(folder, config, fields)
    else:
        if not folder.is_dir():
            raise ValueError('Batch not prepared. Run prepare with the same config first.')
        send_batch(folder, config, fields, args.limit, args.resolve_last)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
        raise SystemExit(str(error))
    except KeyboardInterrupt:
        raise SystemExit('Interrupted. Check the uncertain row in Forms/Sheet before resuming.')
