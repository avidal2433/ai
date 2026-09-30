#!/usr/bin/env python3
"""
bitbucket_comments.py — Publica comentarios de code review en un PR de Bitbucket.

Uso:
  python3 bitbucket_comments.py publish <workspace> <repo_slug> <pr_id> <publish.json> --diff <diff.patch> [--dry-run]
  python3 bitbucket_comments.py global  <workspace> <repo_slug> <pr_id> (--body-file <archivo> | --body "<texto>")

Requiere: BITBUCKET_EMAIL, BITBUCKET_API_TOKEN (App Password).

publish.json es una lista; cada entrada:
  {"body": "texto", "path": "app/X.php", "line": 87, "kind": "to"}   -> inline
  {"body": "texto"}                                                   -> global
  kind: "to" = línea nueva/modificada · "from" = línea eliminada (default "to")

Por cada entrada el script:
  1. Antepone "(AI) " al body si no lo tiene.
  2. Valida path/línea contra el diff (diff_line_map): publica en la línea exacta,
     usa la más cercana (±3, mismo tipo) o degrada a comentario global con el
     prefijo "[No inline por mapeo de diff: path:line]".
  3. Publica. HTTP 400 en un inline -> reintenta como global. 429/5xx -> un reintento.
  4. Verifica que la respuesta apunte al path/línea pedidos; si no, borra ese
     comentario y publica el fallback global.

Salida: una línea JSON por entrada
  {"index", "mode": "inline|global|global_fallback", "id", "path", "line", "kind", "ok", "error", "reason"}
y una línea final {"summary": {"inline", "global", "fallback", "failed"}}.
--dry-run: solo pasos 1-2, no publica nada.

Exit: 0 todo publicado · 1 alguna entrada falló · 2 error de uso.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff_line_map  # noqa: E402  (mismo directorio)

API = "https://api.bitbucket.org/2.0"
AI_PREFIX = "(AI)"
FALLBACK_TAG = "[No inline por mapeo de diff: {path}:{line}]"


class BitbucketError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


# ------------------------------------------------------------------ transporte


def auth_header():
    email = os.environ.get("BITBUCKET_EMAIL")
    token = os.environ.get("BITBUCKET_API_TOKEN")
    if not email or not token:
        raise SystemExit("ERROR: BITBUCKET_EMAIL / BITBUCKET_API_TOKEN no configurados")
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()


def request(method, url, payload=None, auth=None):
    """Una llamada HTTP. Devuelve (status, json|None). Lanza BitbucketError si falla."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": auth or "", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raise BitbucketError(e.code, e.read().decode("utf-8", errors="replace")[:300]) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise BitbucketError(0, str(e)) from None


class Publisher:
    def __init__(self, ws, repo, pr_id, transport=None, auth=None):
        self.base = f"{API}/repositories/{ws}/{repo}/pullrequests/{pr_id}/comments"
        self.transport = transport or request
        self.auth = auth

    def _call(self, method, url, payload=None):
        for attempt in (1, 2):
            try:
                return self.transport(method, url, payload, self.auth)
            except BitbucketError as e:
                if attempt == 1 and (e.status == 429 or e.status >= 500):
                    time.sleep(3)
                    continue
                raise

    def post_global(self, body):
        return self._call("POST", self.base, {"content": {"raw": body}})[1]

    def post_inline(self, body, path, line, kind):
        payload = {"content": {"raw": body}, "inline": {"path": path, kind: line}}
        return self._call("POST", self.base, payload)[1]

    def delete(self, comment_id):
        """Best-effort: se usa para retirar un inline que la API ubicó en otra línea."""
        try:
            self._call("DELETE", f"{self.base}/{comment_id}")
        except BitbucketError:
            pass


# ------------------------------------------------------------------ planificación


def with_ai_prefix(body):
    body = (body or "").strip()
    return body if body.startswith(AI_PREFIX) else f"{AI_PREFIX} {body}"


def fallback(body, path, line, reason):
    text = body[len(AI_PREFIX):].strip() if body.startswith(AI_PREFIX) else body
    tag = FALLBACK_TAG.format(path=path, line=line)
    return {"mode": "global_fallback", "body": f"{AI_PREFIX} {tag} {text}",
            "path": path, "line": line, "kind": None, "reason": reason}


def plan_entry(entry, files):
    """Decide cómo publicar una entrada: inline exacta, inline cercana, global o fallback."""
    body = with_ai_prefix(entry.get("body"))
    path, line = entry.get("path"), entry.get("line")
    kind = entry.get("kind") or "to"
    if not path or line is None:
        return {"mode": "global", "body": body, "path": None, "line": None, "kind": None, "reason": None}
    if files is None:
        return fallback(body, path, line, "sin --diff para validar la línea")
    res = diff_line_map.check(files, path, int(line), kind)
    if res["action"] == "publish":
        return {"mode": "inline", "body": body, "path": res["path"], "line": int(line),
                "kind": kind, "reason": None}
    if res["action"] == "use_nearest":
        return {"mode": "inline", "body": body, "path": res["path"], "line": res["nearest"],
                "kind": kind, "reason": f"línea {line} no está en el diff; usada la {res['nearest']}"}
    return fallback(body, path, line, res.get("reason"))


def publish_entry(pub, plan):
    """Publica según el plan y devuelve el plan final con el id del comentario creado."""
    if plan["mode"] == "inline":
        try:
            data = pub.post_inline(plan["body"], plan["path"], plan["line"], plan["kind"]) or {}
        except BitbucketError as e:
            if e.status != 400:
                raise
            plan = fallback(plan["body"], plan["path"], plan["line"], f"Bitbucket rechazó el inline ({e})")
        else:
            got = data.get("inline") or {}
            if got.get("path") == plan["path"] and got.get(plan["kind"]) == plan["line"]:
                return {**plan, "id": data.get("id")}
            pub.delete(data.get("id"))
            plan = fallback(plan["body"], plan["path"], plan["line"],
                            "la API ubicó el comentario en otra línea")
    data = pub.post_global(plan["body"]) or {}
    return {**plan, "id": data.get("id")}


# ------------------------------------------------------------------ comandos

MODE_KEY = {"inline": "inline", "global": "global", "global_fallback": "fallback"}


def cmd_publish(args):
    try:
        with open(args.publish_json, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError) as e:
        print(f"ERROR: no se pudo leer {args.publish_json}: {e}", file=sys.stderr)
        return 2
    if not isinstance(entries, list):
        print("ERROR: publish.json debe ser una lista de entradas", file=sys.stderr)
        return 2

    files = None
    if args.diff:
        with open(args.diff, encoding="utf-8", errors="replace") as f:
            files = diff_line_map.parse(f.read())
    plans = [plan_entry(e, files) for e in entries]
    summary = {"inline": 0, "global": 0, "fallback": 0, "failed": 0}

    if args.dry_run:
        for i, p in enumerate(plans):
            summary[MODE_KEY[p["mode"]]] += 1
            print(json.dumps({"index": i, "dry_run": True, "mode": p["mode"], "path": p["path"],
                              "line": p["line"], "kind": p["kind"], "reason": p["reason"],
                              "body_preview": p["body"][:80]}, ensure_ascii=False))
        print(json.dumps({"summary": summary}))
        return 0

    pub = Publisher(args.workspace, args.repo_slug, args.pr_id, auth=auth_header())
    for i, p in enumerate(plans):
        try:
            r, ok, err = publish_entry(pub, p), True, None
        except BitbucketError as e:
            r, ok, err = p, False, str(e)
        summary[MODE_KEY[r["mode"]] if ok else "failed"] += 1
        print(json.dumps({"index": i, "mode": r["mode"], "id": r.get("id") if ok else None,
                          "path": r["path"], "line": r["line"], "kind": r["kind"],
                          "ok": ok, "error": err, "reason": r.get("reason")}, ensure_ascii=False))
    print(json.dumps({"summary": summary}))
    return 0 if summary["failed"] == 0 else 1


def cmd_global(args):
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            body = f.read()
    else:
        body = args.body
    pub = Publisher(args.workspace, args.repo_slug, args.pr_id, auth=auth_header())
    try:
        data = pub.post_global(with_ai_prefix(body)) or {}
    except BitbucketError as e:
        print(json.dumps({"mode": "global", "ok": False, "error": str(e)}))
        return 1
    print(json.dumps({"mode": "global", "id": data.get("id"), "ok": True}))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("publish", help="publica un lote desde publish.json")
    p.add_argument("workspace"); p.add_argument("repo_slug"); p.add_argument("pr_id", type=int)
    p.add_argument("publish_json")
    p.add_argument("--diff", help="diff.patch para validar líneas inline")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_publish)

    g = sub.add_parser("global", help="publica un único comentario global")
    g.add_argument("workspace"); g.add_argument("repo_slug"); g.add_argument("pr_id", type=int)
    src = g.add_mutually_exclusive_group(required=True)
    src.add_argument("--body-file"); src.add_argument("--body")
    g.set_defaults(func=cmd_global)

    args = ap.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
