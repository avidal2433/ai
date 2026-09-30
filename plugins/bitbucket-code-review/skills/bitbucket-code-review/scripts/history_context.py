#!/usr/bin/env python3
"""
history_context.py — Contexto histórico del código que un PR modifica.

Responde dos preguntas que el diff por sí solo no puede responder:

  1. ¿Quién escribió el código que este PR está cambiando, cuándo y en qué PR?
     (git blame de las líneas que el PR toca, agrupadas por commit de origen)
  2. ¿Qué se comentó en los PRs anteriores que tocaron estos mismos archivos?
     (comentarios de Bitbucket de esos PRs, filtrados a los paths del diff actual)

Git funciona sin convenciones de mensajes. Se reconocen referencias explícitas
"(PR:NNNN)" o "pull request #NNNN" cuando existen; sin ellas no se infieren PRs.

Uso:
  history_context.py <workspace> <repo_slug> <repo_path> <outdir> [opciones]

  <outdir> debe contener diff.patch y diffstat.json (los genera fetch_pr_data.sh).

Opciones:
  --max-prior-prs N     PRs previos a consultar por comentarios (default 4)
  --max-blame-lines N   líneas por archivo a blamear (default 60)
  --log-depth N         commits a inspeccionar por archivo (default 40)
  --paths PATH [PATH ...] limitar a archivos seleccionados del diff
  --base-ref SHA        base comprobada del diff (sin ella el blame es orientativo)
  --no-api              omitir la consulta de comentarios previos (solo git)

Genera <outdir>/history.json y un resumen legible por stdout.

Es best-effort: si el repo local no está disponible o falla una consulta,
registra un warning y continúa. Nunca debe bloquear el review.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff_line_map  # noqa: E402  (mismo directorio)

PR_REF = re.compile(r"(?:\(PR:|pull request #)(\d+)", re.I)
API = "https://api.bitbucket.org/2.0"
NOISE = re.compile(r"^\s*(lgtm|ok|👍|\+1|listo|dale|gracias|thanks|done|ya está)\W*$", re.I)

warnings = []


def warn(msg):
    warnings.append(msg)
    print(f"WARN: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- git


def git(repo, *args, check=True):
    """Ejecuta git en repo y devuelve stdout, o None si falla."""
    try:
        out = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            warn(f"git {' '.join(args[:2])} falló: {e}")
        return None
    if out.returncode != 0:
        if check:
            warn(f"git {' '.join(args[:2])} salió {out.returncode}: {out.stderr.strip()[:200]}")
        return None
    return out.stdout


def current_pr_id(outdir):
    try:
        with open(os.path.join(outdir, "pr.json"), encoding="utf-8") as f:
            return int(json.load(f).get("id"))
    except (OSError, ValueError, TypeError):
        return None


def resolve_blame_ref(repo, outdir, base_ref=None):
    """Solo una base explícitamente comprobada permite interpretar números de línea."""
    if base_ref and re.fullmatch(r"[0-9a-fA-F]{7,64}", base_ref):
        if git(repo, "cat-file", "-e", f"{base_ref}^{{commit}}", check=False) is not None:
            return base_ref, True
    warn("sin base del diff comprobada; historial contra HEAD solo orientativo")
    return "HEAD", False


def file_history(repo, path, log_depth, ref="HEAD"):
    """Commits recientes del archivo: PRs de origen, autores, churn y última fecha."""
    raw = git(repo, "log", f"-{log_depth}", "--format=%H%x1f%an%x1f%at%x1f%s", ref, "--", path,
              check=False)
    if not raw:
        return None

    prs, authors, dates = [], Counter(), []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).timestamp()
    churn = 0
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        _sha, author, ts, subject = parts
        authors[author] += 1
        try:
            ts = int(ts)
        except ValueError:
            continue
        dates.append(ts)
        if ts >= cutoff:
            churn += 1
        for pr in PR_REF.findall(subject):
            if int(pr) not in prs:
                prs.append(int(pr))

    return {
        # Los más recientes primero (git log ya viene ordenado desc): el review
        # gana poco con PRs de hace tres años y el ruido sesga el ranking global.
        "prior_prs": prs[:10],
        "commits_last_180d": churn,
        "commits_inspected": len(dates),
        "top_authors": [{"name": a, "commits": c} for a, c in authors.most_common(3)],
        "last_touched": _iso(max(dates)) if dates else None,
    }


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def file_exists_at(repo, ref, path):
    """¿El archivo existía antes del PR? Único criterio fiable de 'archivo nuevo':
    que el diff no elimine líneas solo significa que el PR únicamente agregó."""
    return git(repo, "cat-file", "-e", f"{ref}:{path}", check=False) is not None


def insertion_anchors(text):
    """
    Líneas del archivo viejo adyacentes a cada inserción.

    Cuando un PR solo agrega líneas a un archivo existente no hay líneas 'from'
    que blamear, pero el código que rodea la inserción sí tiene historia — y es
    justamente el que define si la adición encaja o duplica algo que ya estaba.
    """
    files = defaultdict(list)
    cur, in_hunk, old_ln = None, False, 0
    for raw in text.splitlines():
        if raw.startswith("diff --git"):
            cur, in_hunk = None, False
        elif not in_hunk and raw.startswith("+++ "):
            p = raw[4:].split("\t")[0].strip()
            cur = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
            in_hunk = False
        elif raw.startswith("@@"):
            m = diff_line_map.HUNK.match(raw)
            if m and cur:
                old_ln, in_hunk = int(m.group(1)), True
        elif in_hunk and cur:
            if raw.startswith("+"):
                files[cur] += [old_ln - 1, old_ln]
            elif raw.startswith("\\"):
                pass
            else:                       # contexto y eliminadas avanzan el archivo viejo
                old_ln += 1
    return {p: sorted({ln for ln in v if ln > 0}) for p, v in files.items()}


def to_ranges(lines, budget):
    """Agrupa líneas ordenadas en rangos contiguos, respetando un presupuesto total."""
    ranges, spent = [], 0
    for ln in sorted(set(lines)):
        if spent >= budget:
            break
        if ranges and ln == ranges[-1][1] + 1:
            ranges[-1][1] = ln
        else:
            ranges.append([ln, ln])
        spent += 1
    return ranges


def blame(repo, ref, path, lines, budget):
    """
    Blame de las líneas indicadas, agrupado por commit de origen.
    Devuelve [{pr, sha, author, date, summary, lines: [...]}] ordenado por
    cantidad de líneas afectadas.
    """
    ranges = to_ranges(lines, budget)
    if not ranges:
        return []

    args = ["blame", "--line-porcelain"]
    for a, b in ranges:
        args += ["-L", f"{a},{b}"]
    args += [ref, "--", path]
    raw = git(repo, *args, check=False)
    if not raw:
        return []

    meta, groups = {}, defaultdict(lambda: {"lines": []})
    sha = None
    for line in raw.splitlines():
        header = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", line)
        if header:
            sha, final_line = header.group(1), int(header.group(2))
            meta.setdefault(sha, {})
            groups[sha]["lines"].append(final_line)
        elif sha and line.startswith("author "):
            meta[sha].setdefault("author", line[7:].strip())
        elif sha and line.startswith("author-time "):
            meta[sha].setdefault("time", int(line[12:].strip()))
        elif sha and line.startswith("summary "):
            meta[sha].setdefault("summary", line[8:].strip())

    out = []
    for sha, g in groups.items():
        m = meta.get(sha, {})
        summary = m.get("summary", "")
        pr = PR_REF.search(summary)
        out.append({
            "sha": sha[:12],
            "pr": int(pr.group(1)) if pr else None,
            "author": m.get("author"),
            "date": _iso(m["time"]) if m.get("time") else None,
            "summary": summary,
            "lines": sorted(g["lines"]),
        })
    return sorted(out, key=lambda g: len(g["lines"]), reverse=True)


# --------------------------------------------------------------------------- api


def api_get(url, auth):
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def prior_comments(ws, repo_slug, pr_ids, paths, auth):
    """Comentarios de PRs previos, quedándose con los que tocan los paths actuales."""
    collected = []
    for pr_id in pr_ids:
        url = f"{API}/repositories/{ws}/{repo_slug}/pullrequests/{pr_id}/comments?pagelen=100"
        try:
            while url:
                page = api_get(url, auth)
                for c in page.get("values", []):
                    if c.get("deleted"):
                        continue
                    body = ((c.get("content") or {}).get("raw") or "").strip()
                    if not body or NOISE.match(body):
                        continue
                    inline = c.get("inline") or {}
                    path = inline.get("path")
                    if path and path not in paths:
                        continue          # comentario sobre un archivo ajeno al diff actual
                    collected.append({
                        "pr": pr_id,
                        "path": path,
                        "line": inline.get("to") or inline.get("from"),
                        "scope": "inline" if path else "global",
                        "author": (c.get("user") or {}).get("display_name"),
                        "date": (c.get("created_on") or "")[:10],
                        "is_ai": body.startswith("(AI)"),
                        "body": body,
                        "id": c.get("id"),
                        "parent": (c.get("parent") or {}).get("id"),
                    })
                url = page.get("next")
        except urllib.error.HTTPError as e:
            warn(f"PR previo #{pr_id}: HTTP {e.code} al leer comentarios")
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            warn(f"PR previo #{pr_id}: {e}")
    return collected


# --------------------------------------------------------------------------- main


def rank_prior_prs(files, limit, exclude=None):
    """
    Prioriza PRs previos por cuántos archivos del diff actual tocaron, y a
    igualdad, por recencia (id más alto = más nuevo). Excluye el PR bajo review:
    si ya está mergeado en la rama base, sus propios commits aparecen en el log.
    """
    hits = Counter()
    for info in files.values():
        for pr in info.get("prior_prs", []):
            if pr != exclude:
                hits[pr] += 1
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], -kv[0]))
    return [pr for pr, _ in ranked[:limit]]


def main(argv):
    warnings.clear()
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("workspace", "repo_slug", "repo_path", "outdir"):
        ap.add_argument(name)
    ap.add_argument("--max-prior-prs", type=int, default=4)
    ap.add_argument("--max-blame-lines", type=int, default=60)
    ap.add_argument("--log-depth", type=int, default=40)
    ap.add_argument("--no-api", action="store_true")
    ap.add_argument("--paths", nargs="+")
    ap.add_argument("--base-ref")
    args = ap.parse_args(argv[1:])
    if min(args.max_prior_prs, args.max_blame_lines, args.log_depth) < 0:
        ap.error("los límites no pueden ser negativos")
    ws, repo_slug, repo_path, outdir = args.workspace, args.repo_slug, args.repo_path, args.outdir
    max_prior_prs, max_blame_lines, log_depth = args.max_prior_prs, args.max_blame_lines, args.log_depth
    use_api = not args.no_api

    diff_path = os.path.join(outdir, "diff.patch")
    if not os.path.exists(diff_path):
        print(f"ERROR: no existe {diff_path} (correr fetch_pr_data.sh primero)", file=sys.stderr)
        return 1
    with open(diff_path, encoding="utf-8", errors="replace") as f:
        diff_text = f.read()
    diff_files = diff_line_map.parse(diff_text)
    if args.paths:
        unknown = set(args.paths) - set(diff_files)
        if unknown:
            ap.error(f"paths fuera del diff textual: {sorted(unknown)}")
        diff_files = {p: v for p, v in diff_files.items() if p in args.paths}
    anchors = insertion_anchors(diff_text)

    if git(repo_path, "rev-parse", "--is-inside-work-tree", check=False) is None:
        warn(f"{repo_path} no es un repositorio git; sin contexto histórico local")
        result = {"files": {}, "prior_pr_comments": [], "warnings": warnings,
                  "blame_ref": None, "blame_ref_is_pr_base": False}
        _write(outdir, result)
        return 0

    ref, exact = resolve_blame_ref(repo_path, outdir, args.base_ref)

    files = {}
    for path, lines in diff_files.items():
        info = file_history(repo_path, path, log_depth, ref) or {
            "prior_prs": [], "commits_last_180d": 0, "commits_inspected": 0,
            "top_authors": [], "last_touched": None,
        }
        info["is_new_file"] = not file_exists_at(repo_path, ref, path)
        # Preferimos blamear lo que el PR reemplaza; si solo agrega, blameamos
        # el código vecino, que es contra el que hay que juzgar la adición.
        if info["is_new_file"]:
            target, basis = [], "none"
        elif lines["from"]:
            target, basis = lines["from"], "removed_lines"
        else:
            target, basis = anchors.get(path, []), "adjacent_context"
        info["blame_basis"] = basis
        info["blame"] = blame(repo_path, ref, path, target, max_blame_lines) if target else []
        info["lines_added"] = len(lines["to"])
        info["lines_removed"] = len(lines["from"])
        files[path] = info

    comments = []
    ranked = rank_prior_prs(files, max_prior_prs, exclude=current_pr_id(outdir))
    queried = []
    if use_api and ranked:
        email = os.environ.get("BITBUCKET_EMAIL")
        token = os.environ.get("BITBUCKET_API_TOKEN")
        if email and token:
            auth = base64.b64encode(f"{email}:{token}".encode()).decode()
            queried = ranked
            comments = prior_comments(ws, repo_slug, ranked, set(files), auth)
        else:
            warn("sin BITBUCKET_EMAIL/BITBUCKET_API_TOKEN; omitido el lente de PRs previos")

    result = {
        "blame_ref": ref,
        "blame_ref_is_pr_base": exact,
        "prior_prs_queried": queried,
        "files": files,
        "prior_pr_comments": comments,
        "warnings": warnings,
    }
    _write(outdir, result)
    _summary(files, queried, comments)
    return 0


def _write(outdir, result):
    dest = os.path.join(outdir, "history.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Contexto histórico en: {dest}")


def _summary(files, ranked, comments):
    print("=== Contexto histórico ===")
    hot = sorted(files.items(), key=lambda kv: -kv[1]["commits_last_180d"])[:5]
    for path, info in hot:
        tag = " [archivo nuevo]" if info["is_new_file"] else ""
        if info["blame_basis"] == "adjacent_context":
            tag += " [blame sobre código vecino]"
        origin = info["blame"][0] if info["blame"] else None
        src = f" | origen principal: PR #{origin['pr']}" if origin and origin.get("pr") else ""
        print(f"  {path}{tag} — {info['commits_last_180d']} commits/180d"
              f" | último: {info['last_touched']}{src}")
    if ranked:
        print(f"  PRs previos consultados: {', '.join('#' + str(p) for p in ranked)}")
    ai = sum(1 for c in comments if c["is_ai"])
    print(f"  Comentarios previos relevantes: {len(comments)} ({ai} de reviews AI anteriores)")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
