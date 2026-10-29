"""Compare two completed rendering runs, failing on new per-case regressions."""
import argparse
import json
from pathlib import Path

METRICS = ('retained_source_spans', 'hard_issue_count', 'small_fitted_blocks')


def compare(baseline, current):
    for field in ('manifest_sha256', 'page_count', 'modes', 'profiles', 'font_sha256'):
        if baseline['provenance'].get(field) != current['provenance'].get(field):
            raise ValueError(f'Incompatible run configuration: {field}')
    key = lambda row: (row['page'], row['profile'], row['mode'])
    old = {key(row): row for row in baseline['cases']}
    new = {key(row): row for row in current['cases']}
    provenance = current['provenance']
    expected = provenance['page_count'] * len(provenance['modes']) * len(provenance['profiles'])
    if len(old) != expected or len(new) != expected or old.keys() != new.keys():
        raise ValueError('Only complete runs with identical case coverage can be compared')
    regressions = []
    for identity in sorted(old):
        before, after = old[identity], new[identity]
        if after['status'] == 'error':
            # A broken case remains a failure even if the baseline also errored.
            regressions.append({'case': identity, 'metric': 'error', 'error': after.get('error')})
            continue
        if before['status'] == 'error':
            continue
        if before['source_spans'] != after['source_spans']:
            regressions.append({'case': identity, 'metric': 'source_span_count_changed',
                                'before': before['source_spans'], 'after': after['source_spans']})
        for metric in METRICS:
            if after[metric] > before[metric]:
                regressions.append({'case': identity, 'metric': metric, 'before': before[metric], 'after': after[metric]})
        # Prevent improvements in one error category hiding a new error elsewhere.
        for code in ('missing_text', 'clipping', 'collision', 'graphics_changed',
                     'page_dimensions_changed', 'protected_value_changed'):
            a, b = before['issue_counts'].get(code, 0), after['issue_counts'].get(code, 0)
            if b > a:
                regressions.append({'case': identity, 'metric': code, 'before': a, 'after': b})
    return regressions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('current', type=Path)
    args = parser.parse_args()
    try:
        regressions = compare(json.loads(args.baseline.read_text()), json.loads(args.current.read_text()))
    except ValueError as exc:
        parser.error(str(exc))
    destination = args.current.with_name('regressions.json')
    destination.write_text(json.dumps(regressions, indent=2)+'\n')
    print(f'{len(regressions)} regression findings; {destination}')
    return bool(regressions)


if __name__ == '__main__':
    raise SystemExit(main())
