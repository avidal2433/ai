#!/usr/bin/env python3
"""
clickup_task.py — Trae las tareas de ClickUp que enlaza un PR, con su tarea padre, en formato compacto.

Uso:
  python3 clickup_task.py <task_id> [<task_id> ...] [--outdir <outdir>]

  Cada <task_id> es un custom id (TEAM-123, APP-456; con o sin "#") o un id interno (86abc123).
  Pasar todos los IDs de review_context.json.tasks: un enlace puede mostrar un ID y apuntar a
  otro ([#TF-3163](…/AWSDKTF-3163)). Un ID que no existe se avisa y se sigue con los demás; los
  IDs que resuelven a la misma tarea se unen.
  Requiere CLICKUP_API_KEY; CLICKUP_WORKSPACE_ID solo para custom IDs.

Usa GET /task/{id}?custom_task_ids=true&team_id=... — el listado /team/{id}/task
ignora el filtro por custom id y devuelve páginas enteras; no usarlo.

Salida: una línea JSON por tarea distinta
  {"ids": [IDs pedidos que resolvieron a ella], "task": {"id","custom_id","name","status","url","description"},
   "parent": {...}|null}
Con --outdir guarda además el JSON crudo en <outdir>/clickup_task.json: {"tasks": [{"ids","task","parent"}]}.

Exit: 0 al menos una tarea · 1 sin credenciales o ningún ID resolvió (la skill continúa sin este contexto) · 2 uso.
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


def resolve(task_ids, team, key):
    """Tareas distintas en el orden pedido: [{"ids", "task", "parent"}]. Avisa por stderr cada ID que falla."""
    found = {}
    for task_id in task_ids:
        by_custom_id = "-" in task_id
        if by_custom_id and not team:
            print(f"WARN: CLICKUP_WORKSPACE_ID no configurado; se omite el custom id {task_id}", file=sys.stderr)
            continue
        try:
            task = fetch_task(task_id, team, key, by_custom_id=by_custom_id)
            if task["id"] in found:
                found[task["id"]]["ids"].append(task_id)
                continue
            parent = fetch_task(task["parent"], team, key, by_custom_id=False) if task.get("parent") else None
        except urllib.error.HTTPError as e:
            print(f"WARN: ClickUp HTTP {e.code} al consultar {task_id}; se sigue con los demás IDs", file=sys.stderr)
            continue
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            print(f"WARN: ClickUp: {e} al consultar {task_id}; se sigue con los demás IDs", file=sys.stderr)
            continue
        found[task["id"]] = {"ids": [task_id], "task": task, "parent": parent}
    return list(found.values())


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task_ids", nargs="+", metavar="task_id")
    ap.add_argument("--outdir", help="guarda el JSON crudo en <outdir>/clickup_task.json")
    args = ap.parse_args(argv[1:])

    key = os.environ.get("CLICKUP_API_KEY")
    team = os.environ.get("CLICKUP_WORKSPACE_ID")
    if not key:
        print("WARN: CLICKUP_API_KEY no configurado; sin contexto de ClickUp", file=sys.stderr)
        return 1

    task_ids = list(dict.fromkeys(t.lstrip("#") for t in args.task_ids))
    tasks = resolve(task_ids, team, key)
    if not tasks:
        print(f"ERROR: ningún ID resolvió a una tarea de ClickUp: {', '.join(task_ids)}", file=sys.stderr)
        return 1

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
        with open(os.path.join(args.outdir, "clickup_task.json"), "w", encoding="utf-8") as f:
            json.dump({"tasks": tasks}, f, ensure_ascii=False, indent=2)

    for t in tasks:
        print(json.dumps({"ids": t["ids"], "task": compact(t["task"]),
                          "parent": compact(t["parent"]) if t["parent"] else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
