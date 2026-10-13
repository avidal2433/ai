#!/usr/bin/env bash
# pr_worktree.sh — Checkout aislado de un commit para ejecutar código del PR sin tocar
# el working tree del usuario.
#
# Uso:
#   pr_worktree.sh create <repository_path> <sha> <worktree_dir> [opciones]
#   pr_worktree.sh remove <repository_path> <worktree_dir>
#
# Opciones de create (repetibles):
#   --check <cmd>   prerrequisito (base de datos, servicio); se ejecuta en <repository_path>
#                   antes de crear nada. Si falla, sale con 3 sin dejar rastro.
#   --copy <ruta>   copia una ruta no versionada (vendor, node_modules) del checkout
#                   principal con copy-on-write. No se symlinkea: los autoloaders resuelven
#                   la ruta real y ejecutarían el código del checkout principal.
#   --link <ruta>   symlink a un archivo de configuración del checkout principal (.env).
#   --mkdir <ruta>  crea directorios que el runtime espera y git no versiona.
#
# Exit: 0 ok · 1 error · 2 uso · 3 prerrequisito no disponible.
set -euo pipefail

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

cow_copy() { # $1=origen $2=destino
  mkdir -p "$(dirname "$2")"
  if [ "$(uname)" = "Darwin" ]; then
    cp -cR "$1" "$2" 2>/dev/null || cp -R "$1" "$2"
  else
    cp -R --reflink=auto "$1" "$2"
  fi
}

cmd_create() {
  local repo="${1:?}" sha="${2:?}" dir="${3:?}"
  shift 3
  local checks=() copies=() links=() dirs=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) checks+=("${2:?--check requiere un comando}"); shift 2 ;;
      --copy)  copies+=("${2:?--copy requiere una ruta}"); shift 2 ;;
      --link)  links+=("${2:?--link requiere una ruta}"); shift 2 ;;
      --mkdir) dirs+=("${2:?--mkdir requiere una ruta}"); shift 2 ;;
      *) usage ;;
    esac
  done

  local p
  for p in ${copies[@]+"${copies[@]}"} ${links[@]+"${links[@]}"} ${dirs[@]+"${dirs[@]}"}; do
    case "/$p/" in
      //*|*/../*) echo "ERROR: ruta fuera del repositorio: $p (usa rutas relativas sin ..)" >&2; exit 2 ;;
    esac
  done

  local c
  for c in ${checks[@]+"${checks[@]}"}; do
    if ! (cd "$repo" && bash -c "$c") >/dev/null 2>&1; then
      echo "PRERREQUISITO NO DISPONIBLE: $c" >&2
      exit 3
    fi
  done

  git -C "$repo" cat-file -e "${sha}^{commit}" 2>/dev/null \
    || { echo "ERROR: el commit $sha no existe en $repo; haz fetch de la rama del PR" >&2; exit 1; }
  [ -e "$dir" ] && { echo "ERROR: $dir ya existe" >&2; exit 1; }
  git -C "$repo" worktree add --quiet --detach "$dir" "$sha"

  for p in ${copies[@]+"${copies[@]}"}; do
    [ -e "$repo/$p" ] || { echo "ERROR: --copy $p no existe en $repo" >&2; exit 1; }
    cow_copy "$repo/$p" "$dir/$p"
  done
  for p in ${links[@]+"${links[@]}"}; do
    [ -e "$repo/$p" ] || { echo "ERROR: --link $p no existe en $repo" >&2; exit 1; }
    mkdir -p "$(dirname "$dir/$p")"
    ln -s "$(cd "$repo" && pwd)/$p" "$dir/$p"
  done
  for p in ${dirs[@]+"${dirs[@]}"}; do
    mkdir -p "$dir/$p"
  done
  echo "worktree: $dir @ $(git -C "$dir" rev-parse --short HEAD)"
}

cmd_remove() {
  local repo="${1:?}" dir="${2:?}"
  git -C "$repo" worktree remove --force "$dir"
  git -C "$repo" worktree prune
  echo "worktree eliminado: $dir"
}

case "${1:-}" in
  create) shift; [ $# -ge 3 ] || usage; cmd_create "$@" ;;
  remove) shift; [ $# -eq 2 ] || usage; cmd_remove "$@" ;;
  *) usage ;;
esac
