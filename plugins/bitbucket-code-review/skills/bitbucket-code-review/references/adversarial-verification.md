# Verificación independiente

Un agente distinto al hallador intenta refutar cada candidato antes de publicarlo. Iniciarlo sin historial de conversación: recibe ubicación, afirmación y rutas a los artefactos necesarios, incluidos SHA de origen/base, tarea y convenciones aplicables. No recibe severidad, confianza ni razonamiento del hallador.

## Costo y aislamiento

- Por defecto, un verificador por candidato. Debe comprobar defecto, alcanzabilidad y mitigaciones en otras capas en una misma lectura.
- Puede verificar un lote pequeño de candidatos del mismo flujo para reutilizar lecturas, dando un resultado independiente por candidato. No agrupar módulos sin relación.
- Agregar un segundo verificador con una pregunta concreta solo cuando la evidencia sea contradictoria o quede una duda de alto impacto (pérdida de datos, seguridad o disponibilidad). No triplicar automáticamente por el nombre del módulo.
- Ejecutar verificadores independientes en paralelo dentro del límite disponible. Responder compacto, sin narrar búsquedas. Un fallo de ejecución admite un reintento; después queda pendiente, nunca confirmado por defecto.

## Encargo del verificador

> Intenta refutar este candidato: `{path}:{line} ({kind})`, `{claim}`.
> Consulta el código del SHA revisado y las dependencias necesarias, no solo el hunk. Comprueba:
> 1. Qué cambio del PR causa el problema; puede activar una línea preexistente.
> 2. Una entrada/estado alcanzable y el resultado incorrecto concreto.
> 3. Validaciones, tipos, controles y restricciones de otras capas que lo podrían impedir.
> 4. Si el comportamiento es intencional según la tarea o una decisión documentada. Si se alega una convención, cita la regla real y comprueba su alcance y excepciones.
> Devuelve un JSON por candidato:
> `{"verdict":"confirmado|refutado|no_verificable","failure_case":null,"evidence":["ruta:línea o prueba"],"reason":"explicación breve"}`.
> Solo confirma con causalidad, caso concreto y evidencia suficiente; no inventes hechos para completar campos. Para una infracción contractual/documentada, describe la incompatibilidad demostrada. El impacto se evalúa aparte de la certeza.

## Decisión

- **Confirmado:** evidencia directa del defecto, o de la incompatibilidad con un contrato o convención documentada, y una acción concreta. Una consecuencia latente (falla bajo una condición que hoy no ocurre) cuenta como accionable. Publicar; reutilizar `failure_case` en el comentario.
- **Refutado:** mitigado, intencional, ajeno al cambio o sin defecto. Descartar. Que el impacto actual sea bajo no refuta un defecto.
- **No verificable:** falta código, datos o una prueba necesaria. Llevar al chat indicando qué falta, destacando impactos altos.
- Si verificadores discrepan, resolver la contradicción con código/prueba; no promediar opiniones ni decidir por mayoría. Si persiste, no publicar.

Excluir preferencias estilísticas sin regla del repo, avisos ya cubiertos por CI y pedidos genéricos de tests/documentación. Una decisión documentada es contexto, no inmunidad frente a un fallo nuevo demostrado. La ausencia de repo local no impide verificar si se obtiene el código exacto por otra vía; lo decisivo es la evidencia disponible.
