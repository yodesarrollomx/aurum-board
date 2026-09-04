# aurum-board — *Tablero de medición del embudo comercial (redes + leads)*

Contexto para Claude Code. Lee este archivo completo antes de tocar nada.

## Qué es esto
Tablero interno de **medición** de Yo Desarrollo / Aurum Arquitectos. Muestra en una sola
pantalla: leads y citas del CRM, el embudo de los dos imanes (Arquitectura de Autor y Plan
de Potencial), el gasto de pauta y el rendimiento de cada publicación de FB/IG. No es un
sitio para clientes (`README.md`). Título de la pestaña: "Tablero · Yo Desarrollo"
(`index.html:19`).

- **En vivo:** `https://yodesarrollomx.github.io/aurum-board/` — **HTTP 200**, comprobado con
  `curl -L` el **2026-09-04**.
- **Casa vieja (cascarón que reenvía):** `https://alexpueblag.github.io/aurum-board/` — 200,
  mismo día. Declarado como cascarón en `scripts/guardian_uptime.py:54`.
- **`https://tableros.yodesarrollo.mx/aurum-board/` NO responde** (curl = `000`, sin DNS,
  2026-09-04). Varias memorias todavía escriben esa dirección: es la casa futura, no la de hoy.
- **Repo:** `https://github.com/yodesarrollomx/aurum-board.git` (`git remote -v`). Clon local
  en `/Users/a./aurum-board`.
- **Ojo con el nombre:** `board-aurum` (guion al revés) es OTRO tablero — el de tareas del
  equipo, código de Portero `TA`. Ver memoria [[board-tareas-aurum-yod]].

## Reglas INVIOLABLES

1. **SIEMPRE `git pull --rebase` antes de empujar.** Una GitHub Action commitea sola cada
   hora (`.github/workflows/refresh-board.yml`, cron `0 * * * *`), así que el clon local casi
   nunca está al día. Corre `git rev-list --count HEAD..origin/main` antes de empujar: casi
   nunca es 0. Empujar sin rebase = conflicto o pisar el refresco.
2. **No editar `metrics.json` ni `covers/` a mano.** Son generados por
   `scripts/pull_metrics.py`; la Action los pisa en la siguiente corrida y ante conflicto
   manda lo recién jalado (`refresh-board.yml`, `git rebase -X ours`).
3. **Cero secretos en el HTML.** El repo es público. El deployment viejo se archivó el
   2026-07-12 justo porque el HTML público traía el secreto compartido
   (`apps-script/portero-auth.gs:4-8`). Hoy la credencial la valida el Portero del lado del
   servidor; el front solo manda `k`.
4. **Cero PII.** El backend entrega agregados sin datos personales (`README.md`). Si un campo
   nuevo trae correo o teléfono, no entra al tablero.
5. **No borrar históricos.** Para "resetear" se usa la línea de arranque
   `var ARRANQUE = "2026-07-29"` (`index.html:356`): recorta la vista, el Sheet queda entero.
6. **El embudo arranca en "Inicio" y enseña el rebote crudo.** Decisión de Alejandro: la
   caída Inicio→Estilo (~−88%) va a la vista, no escondida (memoria
   [[aurum-board-embudo-realidad]]). No volver a arrancar el embudo en "Estilo".
7. **Si no hay lectura, se dice — no se inventa.** El `SNAP` de ejemplo está prohibido como
   respaldo silencioso (`index.html:347`); el fallback real es `datos.json`, y si tampoco,
   `PAUSA` con ceros y letrero (`index.html:370`).
8. **`apps-script/portero-auth.gs` es ESPEJO.** Lo que corre es lo pegado en el editor de
   Apps Script. Cambiarlo aquí no cambia nada en vivo.
9. **Ligas medibles: nunca a mano.** Se generan con la caja del propio tablero
   (`window.genLinkFB`, `index.html:1271`) o con `liga.py`; convención
   `utm_campaign=<fb|ig>-<fecha>` (memoria [[yod-ligas-convencion]]).

## Archivos
- `index.html` — el tablero completo, un solo archivo de ~128 KB, sin build. Ahí viven
  `CONFIG` (líneas 300-310), el Portero, el `SNAP`/`PAUSA`, el render de todas las secciones
  y el generador de ligas UTM.
- `metrics.json` — foto de las publicaciones FB/IG (posts, reach, ER, portadas). La escribe
  `scripts/pull_metrics.py`. Para saber de cuándo es la foto: `grep generado metrics.json`.
- `covers/` — las portadas .jpg comprimidas de los posts; las escribe `scripts/pull_metrics.py`
  y crecen solas con cada corrida de la Action, así que el conteo cambia todos los días.
- `datos.json` — respaldo empacado de la última foto real del Sheet; se usa solo si el
  backend no contesta (`index.html:451`).
- `metrics_history.jsonl` — histórico local; está en `.gitignore` a propósito (no se versiona
  para no inflar el repo). Por eso en la nube el sparkline por post arranca sin serie.
- `scripts/pull_metrics.py` — jala Graph API con `YOD_META_TOKEN` (env) y escribe a la raíz
  del repo (`BASE = parents[1]`, línea 20). Si existe `AURUM_QAA_TOKEN`, además pide
  `?recurso=leads` al backend del cuestionario para atribuir leads por post (líneas 33-37).
- `scripts/guardian_uptime.py` — vigila que páginas y backends de los formularios respondan
  (lista `TARGETS`, líneas 24-56). Abre y cierra issues de GitHub solo.
- `scripts/guardian_salud.py` (corrida exitosa reciente) y `scripts/guardian_token.py` (token
  vivo) — los otros dos vigilantes; avisan por issue de GitHub.
- `.github/workflows/` — 4 workflows: `refresh-board.yml` (cada hora), `guardian-uptime.yml`
  (cada 30 min), `guardian-salud.yml` (cada hora al minuto 30), `guardian-token.yml` (13:15 UTC).
- `apps-script/portero-auth.gs` — ESPEJO del validador de credencial pegado en el Apps Script del CRM.

## Arquitectura de datos

```
Meta Graph API (FB pág. Yo Desarrollo + IG @yodesarrollo + cuenta Aurum)
      │  Secret YOD_META_TOKEN (repo) · nunca en el código
      ▼
GitHub Action "refresh-board" (cron 0 * * * *)  ──corre──> scripts/pull_metrics.py
      │                                          escribe: metrics.json + covers/
      └── commit "Actualizar metricas (Action …) [skip ci]" + push con reintento
                                   │
Sheet "CRM - YOD" (LEADS-WEB / GASTO / ACTIVIDAD)
      │
      ├─ Apps Script del cuestionario  GET ?recurso=board ──┐
      │    /exec  AKfycbztAKA7K5QwO6k45Pqjix…QQOg           │
      │    (CONFIG.SHEET_URL, index.html:301)               │
      │                                                     ▼
      ├─ Apps Script Plan de Potencial GET ?recurso=board ──> index.html (fetchData /
      │    /exec  AKfycbw3EB-6Q9Mq-ouDU-JvKMrRUaw4…lDB3A       fetchPot / fetchMetrics)
      │    (CONFIG.POT_URL, index.html:309)                    │
      │                                                        ├─ si no hay lectura → datos.json
      └─ POST {gasto, k}  (única escritura del tablero)        └─ si tampoco → PAUSA (ceros)
                                   ▲
                                   │  k = credencial del Portero (localStorage pyod_clave_v1)
Portero YOD  /exec AKfycbwlDDCWWzOWYZsUpBU9uqsQ7ae…zlzFg   +  portero.js
      (PYOD_EXEC, index.html:313; PORTERO_EXEC en portero-auth.gs:34)
      https://yodesarrollomx.github.io/potenciales-yod/portero.js  (index.html:1415)
      https://yodesarrollomx.github.io/yod-portal/os/shell.js|css  (index.html:259,1416)
```

**ADVERTENCIA: el repo es ESPEJO del backend.** `apps-script/portero-auth.gs` es una copia.
Lo que corre es lo pegado en el editor de Apps Script; tras cualquier cambio hay que pegarlo
allá y publicar **Nueva versión** en Administrar implementaciones.

**Probado hoy (2026-09-04, GET sin credencial, sin POST):** los dos `?recurso=board`
contestan **HTTP 200 con `{"ok":false,"error":"liga"}`** — o sea, protegidos. Coincide con el
barrido de la memoria [[barrido-backends-1ago]].

## Decisiones (fechadas, con autor y porqué)
- **2026-06-20 · Alejandro** — la medición de publicaciones vive DENTRO de este tablero, no en
  un visor aparte (memoria [[yod-board-metrics]]).
- **2026-06-24 · hallazgo del embudo** — ~96% se va en la portada del cuestionario. Se rediseñó
  el embudo a "2 actos" y se retiró la portada-puerta del cuestionario ([[yod-board-metrics]]).
- **2026-06-25 · Alejandro** — el tablero debe "parecer de la misma página" que yodesarrollo.mx.
  Lección cara: los colores se sacan de la página **renderizada**, no del CSS ([[yod-board-metrics]]).
- **2026-06-29 · Alejandro (clic por clic, el token nunca pasó por el chat)** — token de usuario
  de sistema **sin caducidad**, para dejar de renovar cada 2 horas ([[yod-board-metrics]]).
- **2026-07-12 · contención de seguridad** — deployment archivado porque el HTML público traía
  el secreto; se pasa a validar la credencial con el Portero server-side (`portero-auth.gs:4-8`).
- **2026-07-21 · Alejandro** — el refresco se muda a la nube para no depender de la Mac prendida
  ni del WiFi (commit `643316e`). ~~Antes corría por launchd en la Mac a las 8:00 y 20:00.~~
  **OBSOLETO desde 2026-07-22: la nube es el ÚNICO escritor** ([[yod-board-metrics]]).
- **2026-07-28 · Alejandro** — línea de arranque `2026-07-29`: medir desde los esfuerzos nuevos
  sin borrar lo viejo (commits `0b6e852`, `3a04589`, `b563ef4`). Se quitó la tabla de últimos leads.
- **2026-07-29 · Alejandro** — Experiencia Aurum deja de aparecer como "retirado": es el
  cuestionario oficial (commit `a89613b`). ~~"Arquitectura de Autor está pausado"~~ **OBSOLETO**:
  lo retirado el 22-jul fue el **Google Form viejo**, que este tablero nunca leyó
  (`index.html:341-346`, `var ARQ_PAUSADO = false` línea 348).
- **2026-07-29/30** — una sola galería para las dos cuentas; Aurum conectado con 16 publicaciones
  (commits `2a4719f`, `322874e`). **2026-08-03** — contraste tokenizado, 0 fallas WCAG (`daafe62`).
- **2026-08-13** — resiliencia: si el backend rechaza la clave, se muestra el respaldo real
  (`datos.json`) en vez de ceros (commit `0da85ea`).
- **2026-08-27** — ~~URLs de tableros a `tableros.yodesarrollo.mx`~~ (commit `d396918`).
  **OBSOLETO desde 2026-09-01**: la mudanza dejó todo en `yodesarrollomx.github.io` y la puerta
  vieja reenviando (commit `ec3384f`); el dominio propio aún no existe en el DNS (curl 000 hoy).
- **2026-09-02** — el cuestionario vive en `aurumarquitectos.github.io/experiencia`; el tablero
  ya apunta ahí (commit `b3f0641`, `index.html:1263`).

## Pendientes
- **Atribución de leads por publicación** — dueño: Alejandro. Hoy casi todo cae en "(directo)"
  (`datos.json`: `por_fuente` = 11 leads en "(directo)"). Evidencia para cerrarlo: que la
  sección "Por fuente / campaña" muestre leads con `utm_campaign` real de al menos una
  publicación. Instagram no permite liga por post: es estructuralmente parcial
  ([[yod-board-metrics]]).
- **Gasto de pauta sin registrar** — dueño: Alejandro. `datos.json` trae
  `gasto.semana_actual = 0` y una alerta amarilla: sin gasto no hay costo por cita. Evidencia:
  una semana con gasto capturado desde la sección "Registrar gasto de la semana".
- **11 leads NUEVO sin tocar +24h** — dueño: Alejandro. Alerta roja en `datos.json`
  (prometió estimado en <24h). Evidencia: que la alerta desaparezca del tablero.
- **Día del corte del DNS** — dueño: Alejandro / Miguel Reina (cPanel). Cuando exista
  `tableros.yodesarrollo.mx`, hay que cambiar las entradas "Cascaron" de
  `TARGETS` en `scripts/guardian_uptime.py` (hoy son cuatro, líneas 47-55) de
  `espera:"cualquiera"` a `"reenvio"`. Evidencia: curl
  al dominio nuevo devolviendo 200.

## Por confirmar (NO afirmar sin preguntar)
- **¿Sigue existiendo la clave de vista `aurum2026`?** La memoria [[yod-board-metrics]] la
  menciona, pero el `index.html` de hoy solo carga `portero.js` y valida con el Portero. Pregunta
  exacta: *"¿El tablero de métricas todavía se abre con la clave `aurum2026`, o ya es solo Portero?"*
- **¿El token `YOD_META_TOKEN` sigue vivo?** No se verifica desde aquí sin el secreto.
  Pregunta: *"¿El guardián del token ha abierto algún issue últimamente?"*
- **¿Cuál es el `scriptId` del Apps Script del cuestionario?** El repo solo guarda la URL `/exec`.
  Pregunta: *"¿Me pasas el enlace del editor de Apps Script para pegar cambios del `.gs`?"*
