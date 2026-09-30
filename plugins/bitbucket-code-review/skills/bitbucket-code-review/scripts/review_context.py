#!/usr/bin/env python3
"""Resume datos locales del PR sin red ni pérdida de los originales.
Uso: review_context.py <outdir>
Genera review_context.json; stdout contiene el mismo JSON compacto.
Los comentarios y descripciones se conservan completos; no incluye metadatos de API.
"""
import json
import sys
from collections import Counter
from pathlib import Path

from semantic_release_check import extract_tasks


def revision(value):
    value = value or {}
    repo = value.get('repository') or {}
    return {'commit': (value.get('commit') or {}).get('hash'),
            'branch': (value.get('branch') or {}).get('name'),
            'repository': repo.get('full_name')}


def summarize(pr, stats, comments, statuses):
    description = pr.get('description') or (pr.get('summary') or {}).get('raw') or ''
    return {
        'id': pr.get('id'), 'title': pr.get('title'), 'state': pr.get('state'),
        'draft': pr.get('draft', False), 'author': pr.get('author', {}).get('display_name'),
        'source': revision(pr.get('source')), 'destination': revision(pr.get('destination')),
        'description': description, 'tasks': extract_tasks(description),
        'files': [{'path': (s.get('new') or s.get('old') or {}).get('path'),
                   'old_path': (s.get('old') or {}).get('path'), 'status': s.get('status'),
                   'added': s.get('lines_added', 0), 'removed': s.get('lines_removed', 0)} for s in stats],
        'comments': [{'id': c.get('id'), 'parent': (c.get('parent') or {}).get('id'),
                      'inline': c.get('inline'), 'body': (c.get('content') or {}).get('raw', '')}
                     for c in comments if not c.get('deleted')],
        'checks': dict(Counter(s.get('state', 'UNKNOWN') for s in statuses)),
        'failed_checks': [{'name': s.get('name'), 'url': s.get('url'),
                           'description': s.get('description')} for s in statuses
                          if s.get('state') in ('FAILED', 'STOPPED')],
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('outdir', type=Path)
    args = ap.parse_args()
    try:
        data = [json.loads((args.outdir / name).read_text()) for name in
                ('pr.json', 'diffstat.json', 'comments.json', 'statuses.json')]
        result = summarize(*data)
        payload = json.dumps(result, ensure_ascii=False)
        (args.outdir / 'review_context.json').write_text(payload, encoding='utf-8')
        print(payload)
    except (OSError, ValueError) as exc:
        ap.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
