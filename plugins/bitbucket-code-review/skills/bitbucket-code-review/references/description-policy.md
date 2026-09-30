# Convenciones locales de descripción

`semantic_release_check.py` no impone reglas por defecto. Extrae tareas y devuelve `applicable=false`, `valid=null`; exit 0. La presencia de semantic-release en dependencias no prueba que la descripción alimente el commit o el release.

Si el usuario o la configuración/instrucciones del repo define un formato, representar únicamente esa regla en `<outdir>/description-policy.json`:

- `header_pattern`: regex Python aplicada a la primera línea normalizada con fullmatch; opcional si solo se exige tarea. Un grupo nombrado `pr` permite comparar el número con el PR real.
- `require_task`: booleano, false por defecto. Reconoce enlaces ClickUp o `Task: #ID`.
- `message`: comentario accionable que explica la regla del repo y la corrección; obligatorio. No afirmar que rompe releases salvo demostrarlo.

```bash
python3 <skill>/scripts/semantic_release_check.py <outdir>/pr.json --policy <outdir>/description-policy.json
```

Exit 0 = válido; 1 = incumplimiento; 2 = error de configuración/lectura (no es un hallazgo del PR). Comprobar que la regex representa fielmente la regla con ejemplos válidos e inválidos antes de publicar `comment_body`. Si la convención no se expresa con este esquema, verificarla directamente contra su fuente; no inventar una política aproximada.

La normalización elimina enlaces Markdown conservando el texto visible, énfasis y escapes. La extracción de tareas ocurre también sobre el original para conservar destinos ClickUp. Este validador es de descripciones; no sustituye validadores de commits, títulos o pipelines.
