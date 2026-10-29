#!/usr/bin/env python3
"""
clickup_attach.py — Adjunta el video de las pruebas end-to-end a la tarea de revisión del PR en ClickUp.

Uso:
  python3 clickup_attach.py <video.mp4> --task <task_id>
  python3 clickup_attach.py <video.mp4> --pr-url <url del PR> --list <list_id>

  La tarea de revisión es la que enlaza el PR en su descripción, no la del requerimiento que el PR
  enlaza en la suya. Con --task se usa esa; con --list se busca en la lista la única tarea cuya
  descripción enlaza el PR.
  Requiere CLICKUP_API_KEY con permiso de escritura; CLICKUP_WORKSPACE_ID solo para custom IDs.

Si la tarea ya tiene un adjunto con el mismo nombre de archivo, no lo vuelve a subir: nombrar el
video con el commit (e2e-<repo>-<pr>-<sha7>.mp4) para que cada versión del PR tenga el suyo.

Salida: una línea JSON
  {"ok", "task_id", "task_url", "attachment": {"id", "title", "url"}|null, "skipped": null|"already_attached"}
Exit: 0 ok · 1 error de API, tarea no encontrada o ambigua · 2 uso.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "https://api.clickup.com/api/v2"
MAX_BYTES = 100 * 1024 * 1024
MAX_PAGES = 20
PR_LINK = re.compile(r"https://bitbucket\.org/[\w.-]+/[\w.-]+/pull-requests/\d+")
FILENAME = re.compile(r"[\w.-]+\.mp4")


def request(method, path, key, body=None, content_type=None):
    headers = {"Authorization": key, **({"Content-Type": content_type} if content_type else {})}
    req = urllib.request.Request(f"{API}{path}", data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def task_query(task_id, team):
    return f"?custom_task_ids=true&team_id={urllib.parse.quote(str(team))}" if "-" in task_id else ""


def links_pr(task, pr_url):
    text = "\n".join(task.get(k) or "" for k in ("markdown_description", "description", "text_content"))
    return pr_url in {m.group(0) for m in PR_LINK.finditer(text)}


def find_review_tasks(list_id, pr_url, key, fetch=request):
    matches = []
    for page in range(MAX_PAGES):
        data = fetch("GET", f"/list/{urllib.parse.quote(list_id, safe='')}/task?include_markdown_description=true"
                            f"&include_closed=true&subtasks=true&page={page}", key)
        tasks = data.get("tasks") or []
        matches += [t for t in tasks if links_pr(t, pr_url)]
        if data.get("last_page", True) or not tasks:
            break
    return matches


def multipart(path):
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"attachment\"; "
            f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: video/mp4\r\n\r\n").encode()
    with open(path, "rb") as f:
        return head + f.read() + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def attach(task_id, path, key, team, fetch=request):
    """Sube el video salvo que la tarea ya tenga un adjunto con ese nombre."""
    query = task_query(task_id, team)
    task = fetch("GET", f"/task/{urllib.parse.quote(task_id, safe='')}{query}", key)
    result = {"ok": True, "task_id": task.get("id"), "task_url": task.get("url"), "attachment": None, "skipped": None}
    title = os.path.basename(path)
    existing = next((a for a in task.get("attachments") or [] if a.get("title") == title), None)
    if existing:
        return {**result, "attachment": {k: existing.get(k) for k in ("id", "title", "url")},
                "skipped": "already_attached"}
    body, content_type = multipart(path)
    sent = fetch("POST", f"/task/{urllib.parse.quote(task_id, safe='')}/attachment{query}", key, body, content_type)
    return {**result, "attachment": {k: sent.get(k) for k in ("id", "title", "url")}}


def check_video(path):
    """Motivo por el que el archivo no se puede adjuntar, o None."""
    if not FILENAME.fullmatch(os.path.basename(path)):
        return "el archivo tiene que ser un .mp4 con un nombre sin espacios ni comillas"
    if not os.path.isfile(path):
        return f"no existe {path}"
    if os.path.getsize(path) > MAX_BYTES:
        return f"el video pesa más de {MAX_BYTES // (1024 * 1024)} MB: bajar la resolución o recortar las esperas"
    with open(path, "rb") as f:
        if f.read(12)[4:8] != b"ftyp":
            return "el archivo no es un MP4"
    return None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--task", help="id interno o custom id de la tarea de revisión")
    target.add_argument("--pr-url", help="URL del PR, para buscar su tarea de revisión con --list")
    ap.add_argument("--list", help="lista de ClickUp donde están las tareas de revisión")
    args = ap.parse_args(argv[1:])

    pr = PR_LINK.match(args.pr_url or "")
    if args.pr_url and (not pr or not args.list):
        print("ERROR: --pr-url necesita la URL de un PR de Bitbucket y --list", file=sys.stderr)
        return 2
    problem = check_video(args.video)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 2
    key, team = os.environ.get("CLICKUP_API_KEY"), os.environ.get("CLICKUP_WORKSPACE_ID")
    task_id = (args.task or "").lstrip("#")
    if not key or ("-" in task_id and not team):
        print("ERROR: CLICKUP_API_KEY / CLICKUP_WORKSPACE_ID no configurados", file=sys.stderr)
        return 1

    try:
        if not task_id:
            tasks = find_review_tasks(args.list, pr.group(0), key)
            if len(tasks) != 1:
                print(f"ERROR: {len(tasks)} tareas de la lista {args.list} enlazan {pr.group(0)}; "
                      "se adjunta solo con una", file=sys.stderr)
                return 1
            task_id = tasks[0]["id"]
        result = attach(task_id, args.video, key, team)
    except urllib.error.HTTPError as e:
        print(f"ERROR: ClickUp HTTP {e.code} al adjuntar a {task_id or args.list}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
        print(f"ERROR: ClickUp: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
