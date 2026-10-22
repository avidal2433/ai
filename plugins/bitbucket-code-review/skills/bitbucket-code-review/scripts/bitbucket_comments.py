#!/usr/bin/env python3
"""
bitbucket_comments.py — Publica comentarios de code review en un PR de Bitbucket.

Uso:
  python3 bitbucket_comments.py publish <workspace> <repo_slug> <pr_id> <publish.json> --diff <diff.patch> [--dry-run]
  python3 bitbucket_comments.py global  <workspace> <repo_slug> <pr_id> (--body-file <archivo> | --body "<texto>")
  python3 bitbucket_comments.py reply   <workspace> <repo_slug> <pr_id> <comment_id> (--body-file <archivo> | --body "<texto>")
  python3 bitbucket_comments.py resolve <workspace> <repo_slug> <pr_id> <comment_id> [<comment_id> ...]
  python3 bitbucket_comments.py request-changes <workspace> <repo_slug> <pr_id> [--withdraw]
  python3 bitbucket_comments.py approve <workspace> <repo_slug> <pr_id> (--commit <sha> | --withdraw)

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

reply responde dentro del hilo de un comentario. resolve solo resuelve hilos cuyo
comentario raíz empieza con "(AI)" y fue publicado por la cuenta revisora
(BITBUCKET_ACCOUNT_ID o, si el token lo permite, GET /user); sin esa identidad no
resuelve nada. Un hilo ya resuelto cuenta como ok. Salida: una línea JSON por comment_id {"id", "ok", "status", "error"}.

request-changes deja a la cuenta del token en "changes requested" sobre el PR y publica el
comentario de marca (CHANGES_MARK) que la identifica como de una revisión (AI); sin esa marca
no se retirará sola. Si la cuenta ya tiene una solicitud vigente no hace nada; si tiene una
aprobación, la retira antes cuando lleva su marca y devuelve "skipped" cuando la puso una persona.
Con --withdraw solo retira una solicitud vigente que tenga su marca
(review_context.changes_requested_by_ai); una puesta a mano devuelve "skipped". Necesita la
misma identidad que resolve; sin ella solicita cambios sin mirar el estado previo.

approve aprueba el PR solo si su commit origen sigue siendo --commit, está abierto y no es
borrador, no es de la cuenta revisora y ningún check del commit está FAILED, STOPPED o
INPROGRESS. Si la cuenta tiene una solicitud de cambios con su marca, la retira antes; una
puesta a mano devuelve "skipped". Después publica el comentario de marca (APPROVAL_MARK). Con
--withdraw solo retira una aprobación vigente que tenga su marca (review_context.approved_by_ai).
Siempre necesita la identidad de la cuenta revisora.

Salida de request-changes y approve: una línea JSON {"ok", "status", "marker", "error"} con
status "changes_requested", "already_requested", "approved", "already_approved", "withdrawn",
"skipped" o "failed".

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
from review_context import (APPROVAL_MARK, CHANGES_MARK, approved_by_ai, authored_by,  # noqa: E402
                            changes_requested_by_ai, reviewer_state)

API = "https://api.bitbucket.org/2.0"
AI_PREFIX = "(AI)"
FALLBACK_TAG = "[No inline por mapeo de diff: {path}:{line}]"
CHANGES_BODY = (f"{CHANGES_MARK} por los hallazgos (AI) pendientes de esta revisión. La solicitud se "
                "retira cuando una revisión posterior confirme que están corregidos.")
APPROVAL_BODY = (APPROVAL_MARK + ": la revisión no encontró defectos pendientes en `{commit}`. La aprobación "
                 "se retira si una revisión posterior encuentra alguno.")
BLOCKING_CHECKS = ("FAILED", "STOPPED", "INPROGRESS")


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
    headers = {"Authorization": auth or ""}
    # Bitbucket answers 400 to request-changes when a JSON content type arrives without a body.
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
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
        self.repo = f"{API}/repositories/{ws}/{repo}"
        self.pr = f"{self.repo}/pullrequests/{pr_id}"
        self.base = f"{self.pr}/comments"
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

    def post_reply(self, body, parent_id):
        return self._call("POST", self.base, {"content": {"raw": body}, "parent": {"id": parent_id}})[1]

    def get(self, comment_id):
        return self._call("GET", f"{self.base}/{comment_id}")[1]

    def resolve(self, comment_id):
        return self._call("POST", f"{self.base}/{comment_id}/resolve")[1]

    def list_all(self, url):
        items = []
        while url:
            page = self._call("GET", url)[1] or {}
            items.extend(page.get("values", []))
            url = page.get("next")
        return items

    def get_pr(self):
        return self._call("GET", self.pr)[1] or {}

    def request_changes(self):
        return self._call("POST", f"{self.pr}/request-changes")[1]

    def withdraw_changes(self):
        return self._call("DELETE", f"{self.pr}/request-changes")[1]

    def approve(self):
        return self._call("POST", f"{self.pr}/approve")[1]

    def withdraw_approval(self):
        return self._call("DELETE", f"{self.pr}/approve")[1]

    def statuses(self, commit):
        return self.list_all(f"{self.repo}/commit/{commit}/statuses?pagelen=100")

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


def read_body(args):
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            return f.read()
    return args.body


def cmd_global(args):
    body = read_body(args)
    pub = Publisher(args.workspace, args.repo_slug, args.pr_id, auth=auth_header())
    try:
        data = pub.post_global(with_ai_prefix(body)) or {}
    except BitbucketError as e:
        print(json.dumps({"mode": "global", "ok": False, "error": str(e)}))
        return 1
    print(json.dumps({"mode": "global", "id": data.get("id"), "ok": True}))
    return 0


def cmd_reply(args):
    pub = Publisher(args.workspace, args.repo_slug, args.pr_id, auth=auth_header())
    try:
        data = pub.post_reply(with_ai_prefix(read_body(args)), args.comment_id) or {}
    except BitbucketError as e:
        print(json.dumps({"mode": "reply", "parent": args.comment_id, "ok": False, "error": str(e)}))
        return 1
    print(json.dumps({"mode": "reply", "parent": args.comment_id, "id": data.get("id"), "ok": True}))
    return 0


def reviewer_identity(auth):
    if os.environ.get("BITBUCKET_ACCOUNT_ID"):
        return os.environ["BITBUCKET_ACCOUNT_ID"]
    try:
        user = request("GET", f"{API}/user", auth=auth)[1] or {}
    except BitbucketError:
        return None
    return user.get("account_id") or user.get("uuid")


def resolve_comment(pub, comment_id, reviewer):
    """Resuelve un hilo (AI) de la cuenta revisora. Devuelve (ok, status, error)."""
    try:
        comment = pub.get(comment_id) or {}
        if not (comment.get("content") or {}).get("raw", "").startswith(AI_PREFIX):
            return False, "skipped", "el comentario no es (AI)"
        user = comment.get("user") or {}
        if reviewer not in (user.get("account_id"), user.get("uuid")):
            return False, "skipped", "el comentario no es de la cuenta revisora"
        if comment.get("parent"):
            return False, "skipped", "solo se resuelve el comentario raíz del hilo"
        if comment.get("resolution"):
            return True, "already_resolved", None
        pub.resolve(comment_id)
        return True, "resolved", None
    except BitbucketError as e:
        if e.status == 409:
            return True, "already_resolved", None
        return False, "failed", str(e)


def cmd_resolve(args):
    auth = auth_header()
    reviewer = reviewer_identity(auth)
    if not reviewer:
        print("ERROR: no se pudo identificar la cuenta revisora; define BITBUCKET_ACCOUNT_ID "
              "(account_id de Bitbucket del dueño del token)", file=sys.stderr)
        return 2
    pub = Publisher(args.workspace, args.repo_slug, args.pr_id, auth=auth)
    failed = 0
    for comment_id in args.comment_ids:
        ok, status, err = resolve_comment(pub, comment_id, reviewer)
        failed += not ok
        print(json.dumps({"id": comment_id, "ok": ok, "status": status, "error": err}, ensure_ascii=False))
    return 0 if failed == 0 else 1


def state_result(ok, status, error=None, marker=None):
    return {"ok": ok, "status": status, "marker": marker, "error": error}


def history(pub):
    return pub.list_all(f"{pub.pr}/activity?pagelen=50"), pub.list_all(f"{pub.base}?pagelen=100")


def same_commit(a, b):
    n = min(len(a or ""), len(b or ""))
    return n >= 7 and a[:n] == b[:n]


def post_marker(pub, status, body, what):
    """Publica el comentario de marca de un estado ya fijado en el PR."""
    try:
        return state_result(True, status, marker=(pub.post_global(body) or {}).get("id"))
    except BitbucketError as e:
        return state_result(True, status, f"sin comentario de marca; {what} no se retirará sola: {e}")


def request_changes(pub, reviewer=None):
    """Solicita cambios y publica la marca que permite retirarlos después."""
    try:
        if reviewer:
            state = reviewer_state(pub.get_pr().get("participants"), reviewer)
            if state == "changes_requested":
                return state_result(True, "already_requested")
            if state == "approved":
                if not approved_by_ai(*history(pub), reviewer):
                    return state_result(False, "skipped", "la aprobación vigente no la puso una revisión (AI)")
                pub.withdraw_approval()
        pub.request_changes()
    except BitbucketError as e:
        return state_result(False, "failed", str(e))
    return post_marker(pub, "changes_requested", CHANGES_BODY, "la solicitud")


def withdraw_changes(pub, reviewer):
    """Retira la solicitud de cambios vigente solo si la puso una revisión (AI)."""
    try:
        if reviewer_state(pub.get_pr().get("participants"), reviewer) != "changes_requested":
            return state_result(False, "skipped", "la cuenta revisora no tiene una solicitud de cambios vigente")
        if not changes_requested_by_ai(*history(pub), reviewer):
            return state_result(False, "skipped", "la solicitud de cambios no la puso una revisión (AI)")
        pub.withdraw_changes()
        return state_result(True, "withdrawn")
    except BitbucketError as e:
        return state_result(False, "failed", str(e))


def approve(pub, reviewer, commit):
    """Aprueba la versión revisada si sigue vigente y su CI no falla ni está en curso."""
    try:
        pr = pub.get_pr()
        head = ((pr.get("source") or {}).get("commit") or {}).get("hash")
        if pr.get("state") != "OPEN" or pr.get("draft"):
            return state_result(False, "skipped", "el PR no está abierto o es un borrador")
        if not same_commit(head, commit):
            return state_result(False, "skipped", f"el PR cambió desde la revisión: ahora está en {head}")
        if authored_by({"user": pr.get("author")}, reviewer):
            return state_result(False, "skipped", "el PR es de la cuenta revisora")
        blocking = sorted({s.get("name") or s.get("key") or "?" for s in pub.statuses(head)
                           if s.get("state") in BLOCKING_CHECKS})
        if blocking:
            return state_result(False, "skipped", "CI con checks fallidos o en curso: " + ", ".join(blocking))
        state = reviewer_state(pr.get("participants"), reviewer)
        if state == "approved":
            return state_result(True, "already_approved")
        if state == "changes_requested":
            if not changes_requested_by_ai(*history(pub), reviewer):
                return state_result(False, "skipped", "la solicitud de cambios vigente no la puso una revisión (AI)")
            pub.withdraw_changes()
        pub.approve()
    except BitbucketError as e:
        return state_result(False, "failed", str(e))
    return post_marker(pub, "approved", APPROVAL_BODY.format(commit=head[:12]), "la aprobación")


def withdraw_approval(pub, reviewer):
    """Retira la aprobación vigente solo si la puso una revisión (AI)."""
    try:
        if reviewer_state(pub.get_pr().get("participants"), reviewer) != "approved":
            return state_result(False, "skipped", "la cuenta revisora no tiene una aprobación vigente")
        if not approved_by_ai(*history(pub), reviewer):
            return state_result(False, "skipped", "la aprobación no la puso una revisión (AI)")
        pub.withdraw_approval()
        return state_result(True, "withdrawn")
    except BitbucketError as e:
        return state_result(False, "failed", str(e))


def missing_identity():
    print("ERROR: no se pudo identificar la cuenta revisora; define BITBUCKET_ACCOUNT_ID "
          "(account_id de Bitbucket del dueño del token)", file=sys.stderr)
    return 2


def print_state(result):
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_request_changes(args):
    auth = auth_header()
    pub = Publisher(args.workspace, args.repo_slug, args.pr_id, auth=auth)
    reviewer = reviewer_identity(auth)
    if args.withdraw:
        if not reviewer:
            return missing_identity()
        return print_state(withdraw_changes(pub, reviewer))
    return print_state(request_changes(pub, reviewer))


def cmd_approve(args):
    if not args.withdraw and not args.commit:
        print("ERROR: approve necesita --commit <sha revisado> o --withdraw", file=sys.stderr)
        return 2
    auth = auth_header()
    reviewer = reviewer_identity(auth)
    if not reviewer:
        return missing_identity()
    pub = Publisher(args.workspace, args.repo_slug, args.pr_id, auth=auth)
    if args.withdraw:
        return print_state(withdraw_approval(pub, reviewer))
    return print_state(approve(pub, reviewer, args.commit))


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

    r = sub.add_parser("reply", help="responde dentro del hilo de un comentario")
    r.add_argument("workspace"); r.add_argument("repo_slug"); r.add_argument("pr_id", type=int)
    r.add_argument("comment_id", type=int)
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--body-file"); src.add_argument("--body")
    r.set_defaults(func=cmd_reply)

    v = sub.add_parser("resolve", help="resuelve hilos (AI) ya atendidos")
    v.add_argument("workspace"); v.add_argument("repo_slug"); v.add_argument("pr_id", type=int)
    v.add_argument("comment_ids", type=int, nargs="+")
    v.set_defaults(func=cmd_resolve)

    c = sub.add_parser("request-changes", help="solicita cambios en el PR o, con --withdraw, retira la solicitud")
    c.add_argument("workspace"); c.add_argument("repo_slug"); c.add_argument("pr_id", type=int)
    c.add_argument("--withdraw", action="store_true")
    c.set_defaults(func=cmd_request_changes)

    a = sub.add_parser("approve", help="aprueba el PR revisado o, con --withdraw, retira la aprobación")
    a.add_argument("workspace"); a.add_argument("repo_slug"); a.add_argument("pr_id", type=int)
    mode = a.add_mutually_exclusive_group()
    mode.add_argument("--commit", help="commit origen revisado (source.commit.hash)")
    mode.add_argument("--withdraw", action="store_true")
    a.set_defaults(func=cmd_approve)

    args = ap.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
