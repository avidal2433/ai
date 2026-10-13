#!/usr/bin/env python3
"""Resume datos locales del PR sin red ni pérdida de los originales.
Uso: review_context.py <outdir>
Genera review_context.json; stdout contiene el mismo JSON compacto.
Los comentarios y descripciones se conservan completos; no incluye metadatos de API.
previous_review: commit origen del PR cuando la cuenta revisora publicó su último
comentario (AI), según activity.json. Cualquiera puede escribir "(AI)", así que solo
cuentan los comentarios de esa cuenta (BITBUCKET_ACCOUNT_ID o user.json); sin identidad,
sin revisión previa o sin actividad devuelve null y la re-revisión cubre el PR completo.
"""
import os
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from semantic_release_check import extract_tasks


def revision(value):
    value = value or {}
    repo = value.get('repository') or {}
    return {'commit': (value.get('commit') or {}).get('hash'),
            'branch': (value.get('branch') or {}).get('name'),
            'repository': repo.get('full_name')}


def _when(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def authored_by(comment, reviewer):
    user = comment.get('user') or {}
    return bool(reviewer) and reviewer in (user.get('account_id'), user.get('uuid'))


def reviewer_identity(outdir):
    if os.environ.get('BITBUCKET_ACCOUNT_ID'):
        return os.environ['BITBUCKET_ACCOUNT_ID']
    user_file = outdir / 'user.json'
    if user_file.exists():
        user = json.loads(user_file.read_text())
        return user.get('account_id') or user.get('uuid')
    return None


def previous_review(comments, activity, reviewer=None):
    ai_dates = [c['created_on'] for c in comments
                if not c.get('deleted') and c.get('created_on') and authored_by(c, reviewer)
                and (c.get('content') or {}).get('raw', '').startswith('(AI)')]
    if not ai_dates:
        return None
    reviewed_at = max(ai_dates, key=_when)
    pushes = [(u['date'], ((u.get('source') or {}).get('commit') or {}).get('hash'))
              for u in (a.get('update') for a in activity) if u and u.get('date')]
    before = [p for p in pushes if p[1] and _when(p[0]) <= _when(reviewed_at)]
    if not before:
        return None
    return {'commit': max(before, key=lambda p: _when(p[0]))[1], 'reviewed_at': reviewed_at}


def summarize(pr, stats, comments, statuses, activity=None, reviewer=None):
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
                      'inline': c.get('inline'), 'resolved': bool(c.get('resolution')),
                      'body': (c.get('content') or {}).get('raw', '')}
                     for c in comments if not c.get('deleted')],
        'previous_review': previous_review(comments, activity or [], reviewer),
        'checks': dict(Counter(s.get('state', 'UNKNOWN') for s in statuses)),
        'check_runs': [{'name': s.get('name'), 'state': s.get('state'), 'url': s.get('url')}
                       for s in statuses],
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
        activity_file = args.outdir / 'activity.json'
        activity = json.loads(activity_file.read_text()) if activity_file.exists() else []
        result = summarize(*data, activity, reviewer_identity(args.outdir))
        payload = json.dumps(result, ensure_ascii=False)
        (args.outdir / 'review_context.json').write_text(payload, encoding='utf-8')
        print(payload)
    except (OSError, ValueError) as exc:
        ap.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
