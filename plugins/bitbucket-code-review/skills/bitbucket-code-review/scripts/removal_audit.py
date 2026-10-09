#!/usr/bin/env python3
"""Audita lo que un PR elimina contra el código de su commit origen, sin red.
Uso: removal_audit.py <outdir> <repo_path> <head_sha>
Lee <outdir>/diff.patch. Escribe <outdir>/removal_audit.json; stdout contiene el mismo JSON compacto:
  dangling: símbolos declarados en código eliminado que <head_sha> todavía referencia.
  orphans: símbolos que solo usaba el código eliminado (constantes, claves de traducción, imports)
           y que <head_sha> ya no referencia. Son candidatos: verificar uso dinámico antes de reportar.
  ambiguous: nombres con demasiadas coincidencias para concluir con grep.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

CODE_EXT = {'.php', '.py', '.js', '.jsx', '.ts', '.tsx', '.vue', '.rb', '.go', '.java', '.kt', '.cs'}
EXCLUDE = [':!vendor', ':!node_modules', ':!*.lock', ':!*lock.json', ':!*lock.yaml', ':!CHANGELOG*']
LANG_DIR = re.compile(r'(^|/)(lang|locales?|i18n|translations)/')
MAX_HITS = 25

DECL = re.compile(r'^\s*(?:export\s+(?:default\s+)?)?(?:(?:abstract|final|readonly|public|protected|private|static)\s+)*'
                  r'(?:class|trait|interface|enum)\s+([A-Za-z_]\w{3,})')
CONST_REF = re.compile(r'\b([A-Z]\w*)::([A-Z][A-Z0-9_]{2,})\b')
TRANS_REF = re.compile(r'(?:\btrans(?:_choice)?|\b__|@lang|\$?\bt|Lang::get)\(\s*[\'"]([\w-]+(?:\.[\w-]+)+)[\'"]')
USE_SINGLE = re.compile(r'^\s*use\s+([\w\\]+?)(?:\s+as\s+\w+)?;')
USE_GROUP = re.compile(r'^\s*use\s+[\w\\]+\\\{([^}]*)\}')
JS_IMPORT = re.compile(r'^\s*import\s+(?:\{([^}]*)\}|(\w+))')


def parse_patch(text):
    files, current, removed_file = {}, None, False
    for line in text.splitlines():
        if line.startswith('diff --git '):
            current, removed_file = None, False
            continue
        if line.startswith('--- '):
            path = line[4:].strip()
            current = path[2:] if path.startswith('a/') else None
            continue
        if line.startswith('+++ '):
            removed_file = line[4:].strip() == '/dev/null'
            if current:
                files.setdefault(current, {'removed_file': removed_file, 'lines': []})
            continue
        if current and line.startswith('-'):
            files[current]['lines'].append(line[1:])
    return files


def git_grep(repo, head, pattern, fixed=True, word=False):
    cmd = ['git', '-C', str(repo), 'grep', '-n', '-I']
    cmd += ['-F'] if fixed else ['-E']
    cmd += ['-w'] if word else []
    cmd += ['-e', pattern, head, '--', '.', *EXCLUDE]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if out.returncode not in (0, 1):
        raise RuntimeError(out.stderr.strip())
    prefix = f'{head}:'
    return [h[len(prefix):] if h.startswith(prefix) else h for h in out.stdout.splitlines()]


def collect(files):
    declared, consts, keys, imports = {}, {}, {}, {}
    for path, info in files.items():
        stem, ext = Path(path).stem, Path(path).suffix
        if info['removed_file'] and ext in CODE_EXT and len(stem) > 3 and stem.lower() != 'index':
            declared.setdefault(stem, path)
        for line in info['lines']:
            if (m := DECL.match(line)):
                declared.setdefault(m.group(1), path)
            for cls, name in CONST_REF.findall(line):
                if cls not in ('self', 'static', 'parent'):
                    consts.setdefault(f'{cls}::{name}', path)
            for key in TRANS_REF.findall(line):
                keys.setdefault(key, path)
            names = []
            if (m := USE_GROUP.match(line)):
                names = [n.strip().split('\\')[-1].split(' as ')[0] for n in m.group(1).split(',')]
            elif (m := USE_SINGLE.match(line)):
                names = [m.group(1).split('\\')[-1]]
            elif (m := JS_IMPORT.match(line)):
                names = [n.strip().split(' as ')[0] for n in (m.group(1) or m.group(2)).split(',')]
            for name in filter(None, names):
                imports.setdefault(name, path)
    return declared, consts, keys, imports


def audit(files, repo, head):
    declared, consts, keys, imports = collect(files)
    result = {'dangling': [], 'orphans': [], 'ambiguous': []}
    gone = set(declared)

    for name, origin in sorted(declared.items()):
        hits = git_grep(repo, head, name, word=True)
        if len(hits) > MAX_HITS:
            result['ambiguous'].append({'symbol': name, 'from': origin, 'hits': len(hits)})
        elif hits:
            result['dangling'].append({'symbol': name, 'from': origin, 'refs': hits[:10]})

    def orphan(symbol, kind, origin, hits):
        result['orphans'].append({'symbol': symbol, 'kind': kind, 'from': origin, 'defined_at': hits[:3]})

    for ref, origin in sorted(consts.items()):
        cls, name = ref.split('::')
        if cls in gone or git_grep(repo, head, ref, word=True):
            continue
        definition = [h for h in git_grep(repo, head, rf'const[[:space:]]+([[:alnum:]_]+[[:space:]]+)?{name}([^[:alnum:]_]|$)', fixed=False)
                      if Path(h.split(':', 1)[0]).stem == cls]
        if definition:
            orphan(ref, 'constant', origin, definition)

    for key, origin in sorted(keys.items()):
        hits = git_grep(repo, head, key)
        if not [h for h in hits if not LANG_DIR.search(h.split(':', 1)[0])]:
            group, leaf = key.split('.', 1)[0], key.rsplit('.', 1)[-1]
            definition = [h for h in git_grep(repo, head, f"'{leaf}'")
                          if LANG_DIR.search(h.split(':', 1)[0]) and Path(h.split(':', 1)[0]).stem == group]
            if definition:
                orphan(key, 'translation', origin, definition)

    for name, origin in sorted(imports.items()):
        if name in gone or len(name) < 4:
            continue
        hits = git_grep(repo, head, name, word=True)
        if len(hits) > MAX_HITS:
            continue
        users = [h for h in hits if Path(h.split(':', 1)[0]).stem != name]
        definition = [h for h in hits if Path(h.split(':', 1)[0]).stem == name]
        if definition and not users:
            orphan(name, 'import', origin, definition)
    return result


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('outdir', type=Path)
    ap.add_argument('repo', type=Path)
    ap.add_argument('head')
    args = ap.parse_args()
    try:
        files = parse_patch((args.outdir / 'diff.patch').read_text(encoding='utf-8', errors='replace'))
        payload = json.dumps(audit(files, args.repo, args.head), ensure_ascii=False)
        (args.outdir / 'removal_audit.json').write_text(payload, encoding='utf-8')
        print(payload)
    except (OSError, RuntimeError) as exc:
        ap.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
