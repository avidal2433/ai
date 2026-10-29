#!/usr/bin/env python3
"""
clickup_attach.py — Deja la evidencia de las pruebas end-to-end en las tareas de ClickUp que enlaza el PR:
el video como adjunto y un comentario que explica los casos probados.

Uso:
  python3 clickup_attach.py <video.mp4> --task <task_id> [--task <task_id> ...] --comment-file <archivo>

  <task_id> es cada tarea que enlaza la descripción del PR (review_context.json.tasks): las del
  requerimiento, que se comparten con el cliente. Custom id (TEAM-123, con o sin "#") o id interno.
  El comentario es el texto de --comment-file; el script le agrega una línea con el nombre del video.
  Requiere CLICKUP_API_KEY con permiso de escritura; CLICKUP_WORKSPACE_ID solo para custom IDs.

No repite nada: no sube el video si la cuenta ya adjuntó uno con ese nombre a la tarea, ni comenta si
un comentario reciente de la cuenta trae la línea exacta que nombra el video. Lo que suba o comente otra
persona no cuenta, para que nadie pueda suprimir la evidencia. Nombrar el video con el commit
(e2e-<repo>-<pr>-<sha7>.mp4) para que cada versión del PR tenga el suyo. Como máximo 3 tareas.

Salida: una línea JSON {"ok", "tasks": [{"task_id", "task_url", "attachment", "comment_id",
  "skipped": [...], "error"}]}; ok es false si alguna tarea falló (las demás se procesan igual).
Exit: 0 ok · 1 error de API en alguna tarea o credenciales faltantes · 2 uso.
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
MAX_COMMENT = 4000
MAX_TASKS = 3
FILENAME = re.compile(r"[\w.-]+\.mp4")


def request(method, path, key, body=None, content_type=None):
    headers = {"Authorization": key, **({"Content-Type": content_type} if content_type else {})}
    req = urllib.request.Request(f"{API}{path}", data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def task_path(task_id, team, suffix=""):
    query = f"?custom_task_ids=true&team_id={urllib.parse.quote(str(team))}" if "-" in task_id else ""
    return f"/task/{urllib.parse.quote(task_id, safe='')}{suffix}{query}"


def multipart(path):
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"attachment\"; "
            f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: video/mp4\r\n\r\n").encode()
    with open(path, "rb") as f:
        return head + f.read() + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def video_line(video):
    return f"Video de las pruebas: {os.path.basename(video)}, adjunto en esta tarea."


def comment_text(explanation, video):
    return f"{explanation.strip()}\n\n{video_line(video)}"


def by(item, me):
    return str((item.get("user") or {}).get("id")) == str(me)


def whoami(key, fetch=request):
    return fetch("GET", "/user", key)["user"]["id"]


def publish(task_id, video, explanation, key, team, me, fetch=request):
    """Sube el video y comenta los casos en una tarea, salvo lo que la cuenta `me` ya dejó."""
    title = os.path.basename(video)
    task = fetch("GET", task_path(task_id, team), key)
    result = {"task_id": task.get("id"), "task_url": task.get("url"), "attachment": None, "comment_id": None,
              "skipped": []}
    existing = next((a for a in task.get("attachments") or []
                     if a.get("title") == title and by(a, me) and not a.get("deleted")), None)
    if existing:
        result["skipped"].append("attachment")
    else:
        body, content_type = multipart(video)
        existing = fetch("POST", task_path(task_id, team, "/attachment"), key, body, content_type)
    result["attachment"] = {k: existing.get(k) for k in ("id", "title", "url")}

    comments = fetch("GET", task_path(task_id, team, "/comment"), key).get("comments") or []
    said = next((c for c in comments
                 if by(c, me) and video_line(video) in (c.get("comment_text") or "").splitlines()), None)
    if said:
        result["skipped"].append("comment")
    else:
        payload = json.dumps({"comment_text": comment_text(explanation, video), "notify_all": False}).encode()
        said = fetch("POST", task_path(task_id, team, "/comment"), key, payload, "application/json")
    result["comment_id"] = said.get("id")
    return result


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
    ap.add_argument("--task", action="append", required=True, help="tarea que enlaza la descripción del PR")
    ap.add_argument("--comment-file", required=True, help="explicación de los casos probados")
    args = ap.parse_args(argv[1:])

    problem = check_video(args.video)
    explanation = open(args.comment_file, encoding="utf-8").read() if os.path.isfile(args.comment_file) else ""
    if not problem and not explanation.strip():
        problem = f"{args.comment_file} no existe o está vacío"
    if not problem and len(explanation) > MAX_COMMENT:
        problem = f"la explicación pasa de {MAX_COMMENT} caracteres: resumir los casos"
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 2
    key, team = os.environ.get("CLICKUP_API_KEY"), os.environ.get("CLICKUP_WORKSPACE_ID")
    task_ids = list(dict.fromkeys(t.lstrip("#") for t in args.task))
    if len(task_ids) > MAX_TASKS:
        print(f"ERROR: más de {MAX_TASKS} tareas: dejar la evidencia solo en las del requerimiento del PR",
              file=sys.stderr)
        return 2
    if not key or (any("-" in t for t in task_ids) and not team):
        print("ERROR: CLICKUP_API_KEY / CLICKUP_WORKSPACE_ID no configurados", file=sys.stderr)
        return 1

    try:
        me = whoami(key)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
        print(f"ERROR: ClickUp no identificó la cuenta: {e}", file=sys.stderr)
        return 1
    results = []
    for task_id in task_ids:
        try:
            results.append(publish(task_id, args.video, explanation, key, team, me))
        except urllib.error.HTTPError as e:
            results.append({"task_id": task_id, "error": f"ClickUp HTTP {e.code}"})
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            results.append({"task_id": task_id, "error": f"ClickUp: {e}"})
    ok = not any("error" in r for r in results)
    print(json.dumps({"ok": ok, "tasks": results}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
