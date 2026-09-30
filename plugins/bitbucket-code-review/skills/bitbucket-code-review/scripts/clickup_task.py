#!/usr/bin/env python3
"""
clickup_task.py — Trae una tarea de ClickUp y su tarea padre (si tiene) en formato compacto.

Uso:
  python3 clickup_task.py <task_id> [--outdir <outdir>]

  <task_id> es el custom id (TEAM-123, APP-456; con o sin "#") o el id interno (86abc123).
  Requiere CLICKUP_API_KEY; CLICKUP_WORKSPACE_ID solo para custom IDs.

Usa GET /task/{id}?custom_task_ids=true&team_id=... — el listado /team/{id}/task
ignora el filtro por custom id y devuelve páginas enteras; no usarlo.

Salida: una línea JSON
  {"task": {"id","custom_id","name","status","url","description"}, "parent": {...}|null}
Con --outdir guarda además el JSON crudo de ambas tareas en <outdir>/clickup_task.json.

Exit: 0 ok · 1 sin credenciales o error de API (la skill continúa sin este contexto) · 2 uso.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.clickup.com/api/v2"


def fetch_task(task_id, team_id, key, by_custom_id):
    query = f"?custom_task_ids=true&team_id={urllib.parse.quote(str(team_id))}" if by_custom_id else ""
    req = urllib.request.Request(f"{API}/task/{urllib.parse.quote(task_id, safe='')}{query}",
                                 headers={"Authorization": key})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def compact(task):
    return {
        "id": task.get("id"),
        "custom_id": task.get("custom_id"),
        "name": task.get("name"),
        "status": (task.get("status") or {}).get("status"),
        "url": task.get("url"),
        "description": (task.get("text_content") or task.get("description") or "").strip(),
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task_id")
    ap.add_argument("--outdir", help="guarda el JSON crudo en <outdir>/clickup_task.json")
    args = ap.parse_args(argv[1:])

    key = os.environ.get("CLICKUP_API_KEY")
    team = os.environ.get("CLICKUP_WORKSPACE_ID")
    task_id = args.task_id.lstrip("#")
    if not key or ("-" in task_id and not team):
        print("WARN: CLICKUP_API_KEY / CLICKUP_WORKSPACE_ID no configurados; sin contexto de ClickUp",
              file=sys.stderr)
        return 1

    task_id = args.task_id.lstrip("#")
    try:
        task = fetch_task(task_id, team, key, by_custom_id="-" in task_id)
        parent = fetch_task(task["parent"], team, key, by_custom_id=False) if task.get("parent") else None
    except urllib.error.HTTPError as e:
        print(f"ERROR: ClickUp HTTP {e.code} al consultar {task_id}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
        print(f"ERROR: ClickUp: {e}", file=sys.stderr)
        return 1

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
        with open(os.path.join(args.outdir, "clickup_task.json"), "w", encoding="utf-8") as f:
            json.dump({"task": task, "parent": parent}, f, ensure_ascii=False, indent=2)

    print(json.dumps({"task": compact(task), "parent": compact(parent) if parent else None},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
