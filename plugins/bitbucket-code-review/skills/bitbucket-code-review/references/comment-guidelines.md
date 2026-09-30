# Comentarios en Bitbucket

Escribir en español como un senior lead: directo, respetuoso y preciso. El comentario es una instrucción accionable para el autor; la investigación completa queda en los artefactos de revisión.

## Contenido y extensión

- Iniciar con `(AI)`; el publicador lo añade si falta. No anteponer etiquetas de severidad.
- Un defecto por comentario; consolidar la misma causa raíz. Abrir con la condición que dispara el fallo y su consecuencia real, y cerrar con una recomendación principal concreta.
- Tanto inline como global: apuntar a **50–100 palabras de prosa, máximo 150**, en uno o dos párrafos cortos. Un comentario más corto es válido si ya resulta accionable. El código no cuenta en ese límite y tiene su propio presupuesto abajo.
- Incluir solo la evidencia decisiva: un caso de fallo o una referencia precisa suele bastar. Conservar las condiciones y mitigaciones que cambien el impacto; no presentar un fallo detectado por CI como un despliegue roto.
- No volcar cronologías, matrices de comandos/versiones, logs, resultados de verificadores ni alternativas descartadas. Guardarlos en los artefactos de revisión y ampliarlos si el usuario solicita el detalle. No dividir un mismo hallazgo en varios comentarios para eludir el límite.
- Evitar jerga innecesaria, elogios de relleno, nitpicks, preguntas retóricas y recomendaciones adicionales tipo «de paso». No publicar incertidumbre como defecto confirmado.

## Recomendaciones con código

- Incluir un fragmento cuando la corrección sea local, se haya comprobado contra el código revisado y resulte más clara que describirla. Debe mostrar el cambio recomendado, no la salida de un experimento.
- Usar **un solo bloque con lenguaje explícito, idealmente 3–8 líneas y máximo 12**. Mostrar solo el reemplazo relevante o un diff mínimo; identificar el archivo si no queda claro por el inline.
- Comprobar que los símbolos, APIs, versiones y comportamiento propuestos existen y respetan los contratos y convenciones del repo. No inventar helpers ni presentar pseudocódigo como un parche listo para aplicar.
- Si la solución requiere decisiones de diseño, varios archivos o contexto faltante, dar una acción concreta en prosa en lugar de forzar un snippet. Pedir una prueba solo cuando cubra el caso de fallo identificado.

## Revisión editorial antes de publicar

Releer cada `body` de `publish.json`: ¿se entiende qué falla, cuándo ocurre y qué cambiar en una sola lectura? Comprobar los presupuestos de prosa y código; si los excede, reescribir antes de publicar. Eliminar frases que no cambien el diagnóstico o la acción, conservando los matices que afecten la certeza o el impacto.

- Línea concreta → inline sobre el cambio causal. Problema transversal → global. Un fallo de mapeo requiere revisar la ubicación, no inventar una línea.
- Usar saltos de línea reales en JSON, no texto con `\\n` literal.

## Ejemplos de redacción

Estos ejemplos ilustran el formato; las soluciones y contratos deben comprobarse en cada repositorio.

Sin snippet, cuando la acción requiere contexto:

> (AI) Si la lista llega vacía, el acceso al primer elemento lanza una excepción y la operación responde con un error interno. Valida el caso vacío antes de acceder y devuelve la respuesta prevista por el contrato del endpoint. Agrega una prueba con esa entrada.

Con snippet, suponiendo un método que debe devolver `0` para una lista vacía:

> (AI) Cuando `$amounts` está vacío, el cálculo del promedio divide entre cero y falla en lugar de devolver `0`, como exige el contrato del método. Agrega la salida temprana antes de la división y cubre la lista vacía en el test del método:
>
> ```php
> if ($amounts === []) {
>     return 0;
> }
> ```

Para un hallazgo de herramientas con reproducción extensa, suponiendo verificados estos hechos:

> (AI) `npm install <paquete>` puede modificar `package.json` sin pasar por este `preinstall`, dejando el lockfile de pnpm desactualizado. El gate de CI detecta la desincronización, pero el guard no evita el cambio local. Ajusta la documentación para delimitar qué comandos bloquea y documenta el mecanismo de bloqueo local comprobado para las versiones del proyecto.
