#!/usr/bin/env python3
"""Qué cambió en el PR desde la última revisión y qué hilos (AI) hay que volver a mirar.
Uso: review_delta.py <outdir> <repository_path>
Requiere review_context.json (review_context.py) y comments.json en <outdir>, y en el repo
los commits origen, destino y previous_review (hacer fetch de ambas ramas antes).

own_commits: commits sin merge del PR desde previous_review que no vienen del destino, con
sus archivos. merges: merges posteriores y los archivos con resolución manual de conflictos
(los que muestra `git show --cc`). threads: hilos raíz (AI) de la cuenta revisora abiertos
o resueltos por otra persona después de la revisión previa, sin los comentarios de marca de la
solicitud de cambios y de la aprobación (MARKS); path_touched_after indica si un commit propio del PR tocó el
archivo del hilo después de publicarlo (null en hilos globales). Es una pista para decidir
qué verificar, no un veredicto: la corrección puede estar en otro archivo.
Sin previous_review imprime {"previous_review": null} y la re-revisión cubre el PR completo.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from review_context import MARKS, authored_by, reviewer_identity

RECORD, SEP = '\x1e', '\x1f'


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def resolve(repo, rev):
    try:
        return git(repo, 'rev-parse', '--verify', '--quiet', f'{rev}^{{commit}}').strip()
    except subprocess.CalledProcessError:
        raise SystemExit(f'ERROR: el commit {rev} no está en {repo}; hacer fetch de las ramas origen y destino')


def _when(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def own_commits(repo, *revs):
    out = git(repo, 'log', '--no-merges', f'--format={RECORD}%H{SEP}%cI{SEP}%s', '--name-only', *revs)
    commits = []
    for record in out.split(RECORD)[1:]:
        head, *files = record.split('\n')
        sha, date, subject = head.split(SEP)
        commits.append({'commit': sha[:12], 'date': date, 'subject': subject,
                        'files': [f for f in files if f]})
    return commits


def merges(repo, *revs):
    result = []
    for sha in git(repo, 'rev-list', '--merges', *revs).split():
        combined = git(repo, 'show', '--cc', '--format=', sha)
        result.append({'commit': sha[:12],
                       'subject': git(repo, 'log', '-1', '--format=%s', sha).strip(),
                       'conflict_files': [line[len('diff --cc '):] for line in combined.splitlines()
                                          if line.startswith('diff --cc ')]})
    return result


def threads(comments, reviewer, pr_commits, reviewed_at):
    replies = {}
    for c in comments:
        parent = (c.get('parent') or {}).get('id')
        if parent:
            replies.setdefault(parent, []).append(c)
    result = []
    for c in comments:
        raw = (c.get('content') or {}).get('raw', '')
        if c.get('parent') or not authored_by(c, reviewer) \
                or not raw.startswith('(AI)') or raw.startswith(MARKS):
            continue
        resolution = c.get('resolution')
        resolver = (resolution or {}).get('user') or {}
        if resolution is not None and (authored_by({'user': resolver}, reviewer)
                                       or _when(resolution['created_on']) <= _when(reviewed_at)):
            continue
        path = (c.get('inline') or {}).get('path')
        thread_replies = sorted(replies.get(c['id'], []), key=lambda r: _when(r['created_on']))
        touched = None
        if path:
            touched = any(path in commit['files'] and _when(commit['date']) > _when(c['created_on'])
                          for commit in pr_commits)
        result.append({'id': c['id'], 'path': path,
                       'line': (c.get('inline') or {}).get('to') or (c.get('inline') or {}).get('from'),
                       'resolved': resolution is not None,
                       'resolved_by': resolver.get('display_name'),
                       'resolved_on': (resolution or {}).get('created_on'),
                       'replies': len(thread_replies),
                       'last_reply_by': ((thread_replies[-1].get('user') or {}).get('display_name')
                                         if thread_replies else None),
                       'path_touched_after': touched})
    return result


def delta(outdir, repo):
    context = json.loads((outdir / 'review_context.json').read_text())
    previous = context.get('previous_review')
    if not previous:
        return {'previous_review': None}
    source = resolve(repo, context['source']['commit'])
    destination = resolve(repo, context['destination']['commit'])
    since = resolve(repo, previous['commit'])
    comments = [c for c in json.loads((outdir / 'comments.json').read_text()) if not c.get('deleted')]
    return {'previous_review': previous,
            'own_commits': own_commits(repo, source, f'^{since}', f'^{destination}'),
            'merges': merges(repo, source, f'^{since}', f'^{destination}'),
            'threads': threads(comments, reviewer_identity(outdir),
                               own_commits(repo, source, f'^{destination}'), previous['reviewed_at'])}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('outdir', type=Path)
    ap.add_argument('repository_path', type=Path)
    args = ap.parse_args()
    try:
        print(json.dumps(delta(args.outdir, args.repository_path), ensure_ascii=False))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        ap.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
