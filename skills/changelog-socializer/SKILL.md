---
name: changelog-socializer
description: "Convierte el contenido técnico de un CHANGELOG (entradas estilo conventional-commits: Feature, Bugfix, Refactor, Dependencies, chore, etc.) en un resumen descriptivo en español, claro y NO técnico, listo para socializar/informar un despliegue a operaciones, soporte, negocio y stakeholders. Activa esta skill SIEMPRE que el usuario pegue contenido de changelog, notas de versión, o una lista de PRs/commits y pida convertirlo, traducirlo, redactarlo, 'pasarlo a algo entendible', resumirlo o prepararlo para informar/socializar/anunciar un despliegue o release — incluso si no usa la palabra 'changelog' (frases como 'voy a desplegar estos cambios y debo informarlos', 'pásame esto a lenguaje no técnico para el equipo', 'arma las notas de la versión X', 'qué le digo a soporte de este release', 'necesito comunicar estos cambios' también deben activarla). No uses esta skill para generar documentación funcional de un módulo (esa es functional-documentation), ni para escribir el CHANGELOG técnico en sí, ni para redactar mensajes de commit."
---

# Changelog Socializer

Convierte entradas técnicas de un CHANGELOG en un resumen **descriptivo, en español y no técnico**, pensado para informar un despliegue a personas que no leen código: operaciones, soporte, negocio y stakeholders. La salida se devuelve **en la conversación** (lista para copiar a Slack, correo o un ticket), salvo que el usuario pida guardarla en un archivo.

El insumo es el texto del changelog. La salida es lo que el negocio realmente entiende: **qué cambió y por qué le importa**, no cómo se implementó.

## Principios fundamentales

1. **Audiencia**: operaciones, soporte, negocio y stakeholders. NUNCA desarrolladores. Si una frase solo la entiende quien programó el cambio, está mal redactada.
2. **No omitir, pero jerarquizar**: menciona **TODOS** los cambios, sin excepción. Pero no todos pesan igual. Lo visible para el usuario o relevante para el negocio (features de usuario, bugfixes con síntoma visible, nuevos medios de pago, nuevos ambientes/tenants) va **primero** y en lenguaje claro. Lo interno (CI/CD, dependencias, pruebas automatizadas, documentación interna, refactors, ajustes de naming) se **agrupa y se menciona al final**, breve. Nunca elimines algo en silencio: si no aplica al negocio, igual se nombra como mantenimiento interno.
3. **Describe por impacto, no por causa técnica**: especialmente los bugfixes. Describe el **síntoma que el usuario dejará de ver**, no la excepción, clase o método que se tocó. "Se corrige un error que rompía la página de estadísticas" es útil; "se evita ErrorException en StatsType" no lo es.
4. **Confiabilidad sobre invención**: no inventes impacto. Si una entrada es críptica y no tienes contexto (diff, PR o tarea), descríbela de forma conservadora o pide aclaración. Nunca afirmes un comportamiento que no puedas respaldar.
5. **Idioma**: español neutro, claro y directo. Aunque las entradas estén en inglés, la salida es en español. Conserva en su forma original los nombres propios que el negocio reconoce (productos, medios de pago, proveedores, ambientes): "Bre-B de Credibanco", "Getnet UAT", "Sentry".
6. **Brevedad útil**: una línea clara por cambio relevante. Un bullet entendible vale más que un párrafo exhaustivo.

## Enriquecer con contexto (cuando el repositorio esté accesible)

Cada entrada del changelog suele traer pistas: `(PR:4002)`, un hash de commit `(e085a27)` y `closes #TF-2548`. **No traduzcas literalmente el mensaje de commit** — muchas veces es críptico y oscurece el impacto real. Cuando tengas acceso al repositorio o a las herramientas conectadas, úsalas para entender qué cambió de verdad:

- `git show <hash> --stat` para ver qué archivos se tocaron.
- `git show <hash> -- <archivo-clave>` para leer el diff real y deducir el impacto.
- Si hay MCP/herramientas de ClickUp, Jira, Bitbucket o GitHub disponibles, consúltalas con el ID de la tarea o el número de PR para entender el "para qué".

**Ejemplo de por qué importa**: la entrada `remove empty space for redirect_old const value` suena a nada. Al ver el diff (`' REDIRECT_OLD'` → `'REDIRECT_OLD'`, se quitó un espacio inicial) entiendes que era el valor de un método de conexión que no se reconocía correctamente. *Eso* es lo que describes, no "se quitó un espacio en una constante".

Si el repositorio **no** está accesible, trabaja solo con el texto, infiere de forma conservadora y, si algo queda ambiguo, dilo en vez de inventar.

## Cómo mapear cada tipo de cambio a lenguaje de negocio

| Tipo de entrada técnica | Cómo presentarlo | Plantilla de frase |
|---|---|---|
| Feature visible al usuario | Nueva capacidad o mejora de experiencia | "Se habilita / Se agrega / Se mejora ..." |
| Bugfix con síntoma visible | Corrección descrita por el síntoma resuelto | "Se corrige un error que ..." |
| Feature/cambio de plataforma (CI, deploy, tenant, ambiente) | Habilitación operativa o de infraestructura | "Se habilita el despliegue al ambiente X / Se prepara el ambiente X" |
| Refactor | Mejora interna sin cambios visibles | "Se mejora internamente ... (sin cambios visibles para el usuario)" |
| Dependencies (deps) | Actualización interna; **consolidable** si son varias | "Se actualiza la librería interna X a su versión más reciente" |
| docs / documentación | Apoyo a soporte/operaciones | "Se agrega documentación funcional de X, para apoyo de soporte y operaciones" |
| tests | Mantenimiento de pruebas automatizadas | "Se ajustan pruebas automatizadas internas" |
| chore / naming / cosmético | Mantenimiento menor | "Ajuste interno de ... (sin impacto funcional)" |

> Varios cambios internos del mismo tipo (p. ej. dos actualizaciones de la misma dependencia, o varios ajustes de tests) pueden **fusionarse en una sola línea** para no inflar el resumen.

## Formato de salida

Usa esta estructura. Una versión por bloque, **de la más nueva a la más antigua**:

```
**X.Y.Z**
- <cambio visible/relevante 1>
- <cambio visible/relevante 2>
- <cambios internos agrupados: docs / deps / tests / refactor / CI>

**X.Y.(Z-1)**
- ...

### Resumen ejecutivo para socializar
> <1 párrafo que destaque el cambio más importante del lote (el "estrella") y mencione el resto en bloque.>
```

Reglas del formato:

- Encabeza cada bloque con el número de versión en negrita. La fecha es opcional; inclúyela solo si el usuario la quiere.
- Dentro de cada versión: **primero lo visible** (features de usuario, bugfixes con síntoma), **luego lo interno** agrupado.
- El **resumen ejecutivo** es parte de la entrega por defecto: resalta lo más vendible/importante y resume el resto en una frase.
- Ofrece variantes si son útiles: una versión más corta (1 párrafo) para un canal de Slack, o un tono más formal/comercial para un correo a stakeholders.

## Lo que NO debe aparecer en la salida final

Estos son **insumos**, no resultado. Si aparecen en la salida, está mal:

- Hashes de commit (`e085a27`), números de PR (`PR:4002`), IDs de tarea (`TF-2548`, `PT-20126`, `ITOPS-4353`, `#86e1f83`).
- Nombres de clase, archivo, método, namespace o excepción (`StatsTypes`, `Handler.php`, `PublicPropertyNotFoundException`, `GeolocationException`).
- Jerga de implementación: `const`, `enum`, `trait`, `stub`, `handler`, `singleton`, `seeder`.
- La traducción literal del mensaje de commit cuando oscurece el impacto.

**Única excepción**: nombres propios que el negocio sí reconoce y necesita ver — productos, medios de pago, proveedores y ambientes (ej: "Bre-B de Credibanco", "Getnet UAT", "Sentry", "Credibanco").

## Lo que SÍ debe aparecer

- El impacto en lenguaje claro: qué puede hacer ahora el usuario, qué problema dejó de ocurrir, qué se habilitó.
- Nombres de producto/proveedor/medio de pago/ambiente reconocibles por el negocio.
- La versión y la agrupación visible-primero / interno-al-final.
- El resumen ejecutivo.

## Flujo de trabajo

1. **Recibe el texto** del changelog. Si el usuario no lo pegó, pídelo.
2. **Parsea** por versión y por categoría (Feature, Bugfix, Refactor, Dependencies, tests, docs, chore).
3. **Enriquece** las entradas ambiguas con el diff/PR/tarea **si el repositorio está accesible** (ver sección de enriquecimiento). No traduzcas literal.
4. **Redacta** los bullets por impacto. Jerarquiza: visible primero, interno agrupado al final, **sin omitir nada**.
5. **Agrega** el resumen ejecutivo.
6. **Entrega en la conversación**. Ofrece variantes (versión corta para Slack, tono formal) y, si lo piden, guarda en archivo.

## Ejemplos

**Ejemplo 1 — versión compacta (solo lo visible relevante):**

Entrada:
```
11.69.0 (2026-05-11)
Feature
transactions: add condition to avoid applying filter limits - (PR:3964), closes #PT-19921
Bugfix
export: Fix generate custom export with only one sessionExtra field - (PR:3969), closes #PT-20001
Refactor
maxmind: refactor maxmind service - (PR:3965), closes #PT-19929
delete execution commands from deployment
```

Salida:
```
**11.69.0**
- Se permite quitar la restricción de filtros detallados para determinados tenant.
- Se corrige un problema que ocurría al exportar un informe personalizado con un solo campo adicional.
- Mantenimiento interno: mejoras internas del servicio de geolocalización (MaxMind) y limpieza del proceso de despliegue, sin cambios visibles para el usuario.
```

**Ejemplo 2 — versión rica, con cambios internos agrupados:**

Entrada:
```
11.72.0 (2026-05-22)
Feature
add holidays documentation - (PR:3991), closes #TF-2500
GeneralStat: format count values in liquidations for better readability - (PR:3995)
Bugfix
geolocation: handle GeolocationException when IP contains multiple addresses - (PR:3985), closes #PT-20095
recurring: show inactive status badge when response exists and recurring is disabled - (PR:3993), closes #PT-20126
tests: correct branding name in BrandingTest
Dependencies
deps: update placetopay/base - (PR:3984)
deps: update placetopay/base - (PR:3988)
update name PlacetoPay for Placetopay
```

Salida:
```
**11.72.0**
- Se mejora la visualización del conteo de transacciones en las estadísticas de liquidaciones, mostrando los valores con formato numérico para una lectura más clara.
- Se corrige un error de geolocalización que se presentaba cuando la dirección IP de una transacción contenía varias direcciones separadas por coma, asegurando que la ubicación se identifique correctamente.
- Se ajusta el estado visual de los pagos recurrentes: ahora se muestra correctamente la etiqueta de "Inactivo" en rojo cuando el recurrente está deshabilitado pero ya tiene una respuesta registrada (antes se mostraba en blanco).
- Se agrega documentación funcional del módulo de Festivos, para apoyo de soporte y operaciones.
- Mantenimiento interno: actualización de la librería interna placetopay/base, ajustes de pruebas automatizadas y unificación de la marca a "Placetopay".

### Resumen ejecutivo para socializar
> Este despliegue mejora la legibilidad de las estadísticas de liquidaciones y entrega correcciones de experiencia (geolocalización con múltiples IPs y visualización del estado en pagos recurrentes inactivos). Incluye además documentación funcional de Festivos y actualizaciones internas de dependencias y pruebas.
```
