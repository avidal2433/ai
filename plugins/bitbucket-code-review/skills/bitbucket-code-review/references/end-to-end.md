# Pruebas end-to-end

Antes de aprobar, el PR se ejecuta de punta a punta en el commit revisado: por la misma entrada que usa quien lo opera (navegador, petición HTTP, comando, job, otra aplicación que lo consume) y en un entorno propio de la revisión. Los tests automatizados no reemplazan esta corrida, y esta corrida tampoco reemplaza los tests.

## Escenarios

Definirlos antes de preparar nada y escribirlos en `<outdir>/e2e/escenarios.md`: escenario, entrada y resultado esperado.

1. **Cambio.** Cada comportamiento que el PR agrega o modifica y cada criterio explícito de la tarea, con su camino feliz y los estados límite que el PR toca: validaciones, permisos y errores.
2. **Regresión.** Los flujos vecinos que pasan por el código cambiado: quién llama a lo modificado y qué pantallas, comandos o integraciones lo usan. Si el repo es un servicio o una librería que otras aplicaciones consumen (autenticación, API, SDK, paquete), incluir el flujo principal de cada consumidor afectado, levantado desde su rama principal. Los consumidores se identifican en la configuración y las dependencias de los clones locales.
3. **Suite.** La suite automatizada del repo en el commit revisado. Vale la del CI si sus pasos de tests corrieron de verdad en ese commit (`check_runs` y los reportes de tests del pipeline). Si no corrieron, ejecutarla en el checkout aislado. En repos grandes basta con los directorios de los dominios tocados y de sus consumidores.

La entrada depende de lo que cambia:

| Cambio | Entrada |
| --- | --- |
| Pantalla, formulario, flujo web | Navegador automatizado (por ejemplo, Playwright) contra la app servida desde el checkout aislado. |
| API o webhook | Peticiones HTTP reales con su autenticación. |
| Comando, job, cola, tarea programada | Ejecutarlo y comprobar sus efectos en la base y en lo que emite. |
| Migración | Aplicarla sobre una copia de la base con datos y revertirla si el repo lo soporta. |
| Librería o SDK | Instalar la versión del PR en un consumidor (repositorio por ruta local) y ejecutar su flujo. |
| Dependencias | Arrancar la app y ejecutar los flujos que usan el paquete actualizado. |
| Despliegue o CI | Ejecutar lo que corre localmente: build, empaquetado, render de la plantilla. Lo que solo se comprueba desplegando queda sin ejecutar. |

Validar el resultado en lo que ve el usuario y en el efecto real (filas en la base, archivos generados, correos en el log, respuesta del consumidor), no solo en un código HTTP.

## Entorno

- **Código.** El del PR va en el checkout aislado (sección 2 de la skill). Los consumidores van en checkouts aislados de su rama principal. Nunca se usa el working tree del usuario.
- **Base de datos.** Cada revisión tiene la suya, con un nombre que incluya repo y PR para que las revisiones en paralelo no choquen. Se crea con las migraciones y seeders del repo, o como copia nueva de la base local de desarrollo. Nunca se usa la base del usuario ni la de otra corrida de tests.
- **Configuración.** Usar una copia del `.env` dentro del checkout aislado, no un symlink: escribir en un symlink cambiaría el del usuario. En la copia se sobrescriben la base, la URL, el envío de correo (a log) y lo que haga falta. Si el archivo no termina en salto de línea, agregarlo antes de añadir variables.
- **Servidor.** Servir la app en un puerto libre o en un host nuevo, sin cambiar los sitios ni la configuración global del usuario. Si el flujo necesita un host (OAuth, cookies por dominio), crearlo para la revisión y eliminarlo al terminar.
- **Terceros.** Usar los dobles que el repo usa en sus tests o el modo de pruebas del proveedor. Prohibido usar producción, pagos o cobros reales, correos reales y saltar captchas u otras protecciones de un tercero contra bots. Si un flujo exige algo de esto y el repo no trae su doble de pruebas, ese escenario queda sin ejecutar.
- **Credenciales.** Solo usuarios y claves de prueba creados en la base de la revisión. Nunca usar ni imprimir secretos reales.

## Ejecución y evidencia

Ejecutar cada escenario y registrar en `<outdir>/e2e/` su resultado y su evidencia: URL final, respuesta, captura, consulta a la base o salida del comando. Si un escenario falla, repetirlo sobre la rama destino con el mismo entorno:

- **Falla solo en el PR.** Es un candidato (sección 4 de la skill) y la corrida es la evidencia del caso de fallo.
- **Falla también en destino.** Es preexistente: no se atribuye al PR y se informa en el cierre. Si impide ejecutar un escenario del cambio, ese escenario queda sin ejecutar.
- **Falla por el entorno** (un servicio caído, un dato que falta). Corregir el entorno y repetir; no se reporta como defecto.

## Sin escenarios completos no se aprueba

Para aprobar, todos los escenarios del cambio y de regresión tienen que haberse ejecutado y pasado en el commit revisado, con la suite en verde. Si quedó alguno sin ejecutar, no se aprueba: el comentario global `(AI)` de la sección 5 de la skill nombra cada escenario pendiente, explica por qué no se pudo ejecutar y qué haría falta.

Un PR que solo cambia documentación o comentarios no ejecuta nada, así que no aplica; se dice en el cierre.

## Limpieza

Al terminar, aunque la corrida haya fallado, eliminar las bases, los hosts, los procesos y los checkouts creados para ella. Solo se conserva la evidencia de `<outdir>/e2e/`.
