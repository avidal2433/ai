#!/usr/bin/env bash
# fetch_pr_data.sh — Recolecta todos los datos de un PR de Bitbucket en paralelo,
# agotando la paginación de cada endpoint.
#
# Uso:      fetch_pr_data.sh <workspace> <repo_slug> <pr_id> [outdir]
# Requiere: BITBUCKET_EMAIL, BITBUCKET_API_TOKEN, curl, jq
#
# Genera en <outdir> (default /tmp/pr-<ws>-<repo>-<id>):
#   pr.json        — info general del PR (título, descripción raw, branches, estado)
#   diff.patch     — diff unificado completo
#   diffstat.json  — array con stats por archivo (para priorizar PRs grandes)
#   commits.json   — array de commits
#   comments.json  — array de comentarios existentes (sin eliminados)
#   statuses.json  — array de CI/checks del commit origen
set -euo pipefail

WS="${1:?Uso: fetch_pr_data.sh <workspace> <repo_slug> <pr_id> [outdir]}"
REPO="${2:?falta repo_slug}"
PR="${3:?falta pr_id}"
OUT="${4:-/tmp/pr-${WS}-${REPO}-${PR}}"
: "${BITBUCKET_EMAIL:?BITBUCKET_EMAIL no configurado}"
: "${BITBUCKET_API_TOKEN:?BITBUCKET_API_TOKEN no configurado}"

mkdir -p "$OUT"
BASE="https://api.bitbucket.org/2.0/repositories/${WS}/${REPO}"

# GET con follow-redirects (los endpoints de diff responden 302) y diagnóstico HTTP.
api_get() { # $1=url  $2=outfile
  local code
  code="$(curl -sL -u "${BITBUCKET_EMAIL}:${BITBUCKET_API_TOKEN}" -o "$2" -w '%{http_code}' "$1")" \
    || { echo "ERROR: curl falló en $1" >&2; return 1; }
  case "$code" in
    2*) return 0 ;;
    401) echo "ERROR 401: credenciales inválidas (BITBUCKET_EMAIL / BITBUCKET_API_TOKEN)" >&2 ;;
    403) echo "ERROR 403: sin acceso a ${WS}/${REPO}" >&2 ;;
    404) echo "ERROR 404: no encontrado — verificar workspace/repo/pr_id ($1)" >&2 ;;
    429) echo "ERROR 429: rate limit de Bitbucket, reintentar en unos segundos" >&2 ;;
    *)   echo "ERROR HTTP $code en $1" >&2 ;;
  esac
  return 1
}

# Consume todas las páginas de un endpoint paginado y agrega .values en un array JSON.
fetch_paginated() { # $1=url_inicial  $2=outfile
  local url="$1" out="$2" tmp
  tmp="$(mktemp)"
  echo '[]' > "$out"
  while [ -n "$url" ]; do
    api_get "$url" "$tmp" || { rm -f "$tmp"; return 1; }
    jq -s '.[0] + (.[1].values // [])' "$out" "$tmp" > "${out}.new" && mv "${out}.new" "$out"
    url="$(jq -r '.next // empty' "$tmp")"
  done
  rm -f "$tmp"
}

# 1) Info del PR primero: statuses depende del source commit
api_get "${BASE}/pullrequests/${PR}" "${OUT}/pr.json"
SRC_COMMIT="$(jq -r '.source.commit.hash' "${OUT}/pr.json")"

# 2) Resto de consultas en paralelo (son independientes entre sí)
api_get         "${BASE}/pullrequests/${PR}/diff"                    "${OUT}/diff.patch"    & P1=$!
fetch_paginated "${BASE}/pullrequests/${PR}/diffstat?pagelen=100"    "${OUT}/diffstat.json" & P2=$!
fetch_paginated "${BASE}/pullrequests/${PR}/commits?pagelen=100"     "${OUT}/commits.json"  & P3=$!
fetch_paginated "${BASE}/pullrequests/${PR}/comments?pagelen=100"    "${OUT}/comments.json" & P4=$!
fetch_paginated "${BASE}/commit/${SRC_COMMIT}/statuses?pagelen=100"  "${OUT}/statuses.json" & P5=$!

FAIL=0
for p in "$P1" "$P2" "$P3" "$P4" "$P5"; do wait "$p" || FAIL=1; done
[ "$FAIL" -eq 0 ] || { echo "ERROR: una o más consultas fallaron (ver mensajes arriba)" >&2; exit 1; }

# 3) Filtrar comentarios eliminados
jq '[.[] | select(.deleted != true)]' "${OUT}/comments.json" > "${OUT}/comments.json.new" \
  && mv "${OUT}/comments.json.new" "${OUT}/comments.json"

# 4) Resumen operativo
echo "=== PR obtenido ==="
jq -r '"PR #\(.id): \(.title) [\(.state)] | \(.source.branch.name) -> \(.destination.branch.name) | autor: \(.author.display_name)"' "${OUT}/pr.json"
echo "Archivos en diffstat: $(jq 'length' "${OUT}/diffstat.json") | Líneas de diff: $(wc -l < "${OUT}/diff.patch")"
echo "Commits: $(jq 'length' "${OUT}/commits.json") | Comentarios existentes: $(jq 'length' "${OUT}/comments.json")"
echo "CI/checks: $(jq -r 'if length == 0 then "sin checks" else group_by(.state) | map("\(.[0].state): \(length)") | join(", ") end' "${OUT}/statuses.json")"
echo "Datos en: ${OUT}"
