#!/usr/bin/env python3
"""Extrae tareas y, solo con --policy, valida una convención local de descripción.
Uso: semantic_release_check.py <pr.json> [--policy <policy.json>]
     semantic_release_check.py --description TEXT [--pr-id N] [--policy FILE]
Policy JSON: header_pattern (regex fullmatch de la primera línea normalizada),
require_task (bool, default false), message (instrucción accionable del repositorio).
Un grupo nombrado 'pr' opcional valida el número del PR. Sin policy no impone formato.
Exit: 0 válido/no aplicable, 1 incumplimiento, 2 error de lectura/configuración.
"""
import argparse
import json
import re
import sys


def normalize(raw):
    text = (raw or '').replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'(\*\*|__)(.+?)\1', r'\2', text)
    text = re.sub(r'(?<![\w*_])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*_])', r'\2', text)
    text = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|])', r'\1', text).replace('`', '')
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(line.strip() for line in text.split('\n'))).strip()


def extract_tasks(raw):
    """IDs explícitos Task: #ID y URLs ClickUp estándar/custom, sin inferir tickets de otros trackers."""
    tasks = []
    # URLs antes de normalizar Markdown, que eliminaría el destino del enlace.
    for match in re.finditer(r'https://app\.clickup\.com/t/(?:\d+/)?([A-Za-z0-9_-]+)', raw or ''):
        if match.group(1) not in tasks:
            tasks.append(match.group(1))
    for match in re.finditer(r'^Task:\s+#?([A-Za-z0-9_-]+)\s*$', normalize(raw), re.I | re.M):
        if match.group(1) not in tasks:
            tasks.append(match.group(1))
    return tasks


def validate(raw, pr_id=None, policy=None):
    text = normalize(raw)
    tasks = extract_tasks(raw)
    result = {'applicable': policy is not None, 'valid': None, 'tasks': tasks,
              'task_id': tasks[0] if tasks else None, 'errors': [], 'comment_body': None}
    if policy is None:
        return result
    if not isinstance(policy, dict) or not isinstance(policy.get('message'), str) or not policy['message'].strip():
        raise ValueError('policy requiere message con la convención y acción esperada')
    if not isinstance(policy.get('require_task', False), bool):
        raise ValueError('require_task debe ser booleano')
    pattern = policy.get('header_pattern')
    if pattern is None and not policy.get('require_task'):
        raise ValueError('policy requiere header_pattern o require_task=true')
    if pattern is not None:
        if not isinstance(pattern, str):
            raise ValueError('header_pattern debe ser texto')
        match = re.fullmatch(pattern, text.split('\n')[0])
        if not match:
            result['errors'].append('header_format')
        elif match.groupdict().get('pr') is not None and pr_id is not None:
            if int(match.group('pr')) != int(pr_id):
                result['errors'].append('pr_id_mismatch')
    if policy.get('require_task') and not tasks:
        result['errors'].append('task_missing')
    result['valid'] = not result['errors']
    if not result['valid']:
        result['comment_body'] = policy['message']
        if 'pr_id_mismatch' in result['errors']:
            result['comment_body'] += f'\n\nEl número del PR debe ser {pr_id}.'
    return result


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pr_json', nargs='?')
    ap.add_argument('--description')
    ap.add_argument('--pr-id', type=int)
    ap.add_argument('--policy')
    args = ap.parse_args(argv[1:])
    try:
        if args.pr_json:
            with open(args.pr_json, encoding='utf-8') as f:
                pr = json.load(f)
            raw = pr.get('description') or (pr.get('summary') or {}).get('raw') or ''
            pr_id = pr.get('id')
        elif args.description is not None:
            raw, pr_id = args.description, args.pr_id
        else:
            ap.print_usage(sys.stderr)
            return 2
        policy = None
        if args.policy:
            with open(args.policy, encoding='utf-8') as f:
                policy = json.load(f)
        result = validate(raw, pr_id, policy)
    except (OSError, ValueError, TypeError, re.error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['valid'] is False else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
