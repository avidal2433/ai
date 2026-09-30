#!/usr/bin/env python3
"""
diff_line_map.py — Mapeo determinista de un diff unificado (git) a líneas
comentables de Bitbucket. Elimina los errores 400 por líneas inexistentes.

Uso:
  python3 diff_line_map.py map <diff_file>
      Imprime JSON: {path: {"to": [líneas nuevas/modificadas], "from": [líneas eliminadas]}}

  python3 diff_line_map.py check <diff_file> <path> <line> <to|from>
      Valida un candidato a comentario inline. Imprime JSON con "action":
        publish         -> la línea existe exactamente; publicar inline
        use_nearest     -> no existe, pero hay una línea del mismo tipo a ±3; usar "nearest"
        fallback_global -> no hay línea válida; degradar a comentario global
                           con prefijo "[No inline por mapeo de diff]"

Reglas (equivalentes al algoritmo manual histórico de la skill):
  - inline.to   = posición en el archivo NUEVO de una línea '+'
  - inline.from = posición en el archivo VIEJO de una línea '-'
  - Nunca inventar líneas: solo publicar lo que este script valide.
"""
import json
import re
import sys

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse(text):
    """Devuelve {path: {"to": [int], "from": [int]}} a partir de un diff unificado."""
    files = {}
    cur = None
    old_path = None
    in_hunk = False
    old_ln = new_ln = 0

    for raw in text.splitlines():
        if raw.startswith("diff --git"):
            cur, old_path, in_hunk = None, None, False
        elif not in_hunk and raw.startswith("--- "):
            p = raw[4:].split("\t")[0].strip()
            old_path = None if p == "/dev/null" else (p[2:] if p.startswith("a/") else p)
            in_hunk = False
        elif not in_hunk and raw.startswith("+++ "):
            p = raw[4:].split("\t")[0].strip()
            if p == "/dev/null":          # archivo eliminado: comentar sobre 'from'
                cur = old_path
            else:
                cur = p[2:] if p.startswith("b/") else p
            if cur:
                files.setdefault(cur, {"to": [], "from": []})
            in_hunk = False
        elif raw.startswith("@@"):
            m = HUNK.match(raw)
            if m and cur:
                old_ln, new_ln = int(m.group(1)), int(m.group(3))
                in_hunk = True
        elif in_hunk and cur:
            if raw.startswith("+"):
                files[cur]["to"].append(new_ln)
                new_ln += 1
            elif raw.startswith("-"):
                files[cur]["from"].append(old_ln)
                old_ln += 1
            elif raw.startswith("\\"):    # "\ No newline at end of file"
                pass
            else:                         # línea de contexto (incluye vacías)
                old_ln += 1
                new_ln += 1
    return files


def resolve_path(files, path):
    """Normaliza y resuelve el path contra el diff (tolera prefijos a/, b/, ./)."""
    for pref in ("a/", "b/", "./"):
        if path.startswith(pref):
            path = path[len(pref):]
    if path in files:
        return path
    matches = [p for p in files if p.endswith("/" + path) or path.endswith("/" + p)]
    return matches[0] if len(matches) == 1 else None


def check(files, path, line, kind):
    resolved = resolve_path(files, path)
    if resolved is None or kind not in ("to", "from"):
        return {"valid": False, "path": path, "line": line, "kind": kind,
                "nearest": None, "action": "fallback_global",
                "reason": "path no está en el diff" if resolved is None else "kind inválido"}
    lines = files[resolved][kind]
    if line in lines:
        return {"valid": True, "path": resolved, "line": line, "kind": kind,
                "action": "publish"}
    near = [l for l in lines if abs(l - line) <= 3]
    if near:
        nearest = min(near, key=lambda l: abs(l - line))
        return {"valid": False, "path": resolved, "line": line, "kind": kind,
                "nearest": nearest, "action": "use_nearest"}
    return {"valid": False, "path": resolved, "line": line, "kind": kind,
            "nearest": None, "action": "fallback_global",
            "reason": "sin línea del mismo tipo a ±3"}


def main(argv):
    if len(argv) >= 3 and argv[1] == "map":
        with open(argv[2], encoding="utf-8", errors="replace") as f:
            print(json.dumps(parse(f.read()), ensure_ascii=False, indent=2))
        return 0
    if len(argv) == 6 and argv[1] == "check":
        with open(argv[2], encoding="utf-8", errors="replace") as f:
            files = parse(f.read())
        print(json.dumps(check(files, argv[3], int(argv[4]), argv[5]), ensure_ascii=False))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
