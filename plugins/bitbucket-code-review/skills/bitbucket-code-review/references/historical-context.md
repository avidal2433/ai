# Historia enfocada en una duda

Consultar cuando ayude a comprobar una regresión o explicar una decisión del código. No ejecutar sobre todo el repositorio por rutina.

```bash
python3 <skill>/scripts/history_context.py <workspace> <repo_slug> <repository_path> <outdir> --paths <archivo> [<otro>] --base-ref <sha-base-comprobada>
```

Necesita `pr.json` y `diff.patch`. Genera `history.json`. Límites: `--max-prior-prs` (4), `--max-blame-lines` (60 por archivo), `--log-depth` (40); ampliar solo cuando lo exija la duda. `--no-api` consulta únicamente git.

La base debe corresponder al lado viejo del diff: no asumir que destination/HEAD coincide. Comprobarla contra el diff obtenido; sin base comprobada, omitir `--base-ref` y tratar el resultado como orientación. Para renombres, comprobar el path antiguo en `diffstat.json` antes de inferir que el archivo es nuevo.

Interpretación:

- `blame_basis=removed_lines`: origen de líneas eliminadas/reemplazadas; `adjacent_context`: código vecino a una inserción; `none`: sin líneas históricas seleccionadas.
- `blame_ref_is_pr_base=false`: no citar números de línea históricos como prueba.
- `commits_last_180d`: cuenta limitada por `log_depth`, no total exhaustivo ni prueba de inestabilidad.
- `prior_prs_queried`: IDs cuya consulta se intentó; revisar `warnings` para fallos. Los mensajes sin referencias explícitas de PR no permiten recuperar sus discusiones; git sigue siendo útil.
- `prior_pr_comments`: comentarios completos con IDs y padres. Una objeción aislada no prueba un acuerdo; recuperar el hilo si falta una respuesta o resolución antes de invocarla como evidencia.

Leer primero el resultado de los archivos seleccionados. Si hay señales útiles, consultar el commit/PR de origen para comprobar intención y comportamiento; el título del commit por sí solo no demuestra una regresión. Citar PR o commit, sin atribuir culpa a autores. Registrar historia omitida o incompleta; no bloquear el review por este contexto opcional.
