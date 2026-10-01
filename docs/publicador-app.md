# Publicador de métricas y GitHub App

Propuesta: `CHG-PUBLICADORES-APP-001`, atlas `2026-10-01.2`.

El cambio está preparado y verificado con datos sintéticos. La instalación de la
App y el recorrido operativo PR → checks → merge → Pages siguen pendientes.
Las pruebas locales no acreditan publicación ni acceso a las fuentes de métricas.

## Identidades y alcance

La App privada publicadora se instala únicamente en los repositorios autorizados
de Sala y Aurum. Sus permisos de repositorio son `Contents: read` y
`Pull requests: write`, además del acceso de metadatos obligatorio. No requiere
permisos de Actions ni una excepción a las protecciones de `main`.

Cada corrida solicita un token para este repositorio con
`actions/create-github-app-token`, fijada al commit
`bcd2ba49218906704ab6c1aa796996da409d3eb1` de v3. El workflow recibe el ID desde
`vars.YOD_PUBLISHER_APP_ID` y la clave desde
`secrets.YOD_PUBLISHER_APP_PRIVATE_KEY`. No se guardan sus valores en Git ni en
argumentos de comandos. La acción revoca el token al finalizar el job.

`PR_CREATION_TOKEN` se convierte en `GH_TOKEN` únicamente para el proceso
`gh api --method POST repos/…/pulls`. Los demás procesos no heredan
`PR_CREATION_TOKEN`: el push de la rama usa el checkout de Actions y las
consultas, el dispatch del guard, los checks, el merge y Pages conservan
`GH_TOKEN` de Actions. El helper rechaza credenciales ausentes o reutilizadas
antes de modificar el manifiesto, crear una rama o hacer push.

## Secuencia y comprobaciones

1. Sin `YOD_META_TOKEN`, la corrida conserva su salida sin trabajo.
2. Con fuente configurada, comprueba la configuración de la App antes de generar
   métricas. La clave privada sólo se entrega a ese paso y a la acción de token.
3. Genera los archivos y ejecuta `--check-changes`, que consulta `git status` y
   valida las rutas permitidas. Sin cambios, omite el token y la publicación.
4. Acuña el token inmediatamente antes de publicar. Abre el PR con la App para
   que las comprobaciones de `pull_request` puedan ejecutarse con normalidad.
5. Conserva el dispatch manual de `Arquitectura YOD` con base y head exactos,
   espera su corrida nueva y exige todos los checks obligatorios aprobados.
   Un rollup vacío espera hasta el límite; nunca habilita la integración.
6. Revalida el head del PR y solicita el merge protegido con
   `--match-head-commit`. Sólo después de confirmar el merge solicita Pages.

La propuesta de esta corrección cambia la identidad del publicador
(`SYS-MARKETING`, `EXT-ACTIONS`, `EXT-GITHUB-PUBLISHER-APP`). Los datos generados
siguen registrándose bajo `CHG-METRICS-001`; sus fuentes y contratos permanecen
en el atlas y los motores correspondientes.

## Validación y activación pendiente

`node --test tests/publicar-metricas.test.cjs` cubre separación de credenciales,
el entorno real de un subproceso local, no-op sin token, fallo antes de mutaciones,
rollup vacío recuperable y permanente, guard fallido o de otro SHA, checks
obligatorios y rechazo de merge sin bypass. `git diff --check` verifica el diff.

Antes de activar, hace falta registrar e instalar la App, configurar la variable
y el secreto, e integrar la propuesta del atlas y su pin. Una corrida observada
debe acreditar por separado el autor del PR, los checks del commit exacto, el
merge y el resultado final de Pages. No se incluyen ni se han ejecutado escrituras
de prueba sobre las fuentes de negocio.

Rollback: revertir el cambio mediante PR y checks. Si se necesita contener una
incidencia operativa, suspender el workflow o revocar la instalación de la App;
conservar los datos generados y el historial. No habilitar push directo ni bypass.

Referencias oficiales: [acción y entradas admitidas](https://github.com/actions/create-github-app-token),
[checks de GitHub CLI](https://cli.github.com/manual/gh_pr_checks).
