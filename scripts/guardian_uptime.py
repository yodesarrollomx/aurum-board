#!/usr/bin/env python3
# Guardian de UPTIME — corre en GitHub Actions cada ~30 min.
# Revisa que los formularios (paginas) y sus backends (Apps Script) respondan de verdad,
# no solo que el servidor conteste. Si algo se cae, abre/actualiza UN issue de GitHub
# (etiqueta guardian-uptime); al recuperarse, comenta y lo cierra. Sin secretos.
#
# MUDANZA (ago-2026): el sistema se pasa a yodesarrollomx.github.io y la casa vieja
# (alexpueblag.github.io) se queda como CASCARON de reenvio, para los QR impresos y
# las ligas que ya andan en WhatsApp. Eso rompia este guardian de raiz: urlopen sigue
# los reenvios solo, asi que pedir la URL vieja daria 200 porque contesto la casa
# NUEVA — verde por la razon equivocada, y ciego a la caida real de la casa vieja.
# Por eso ahora cada objetivo declara COMO espera que le contesten:
#   "directo"  = 200 sin un solo reenvio (la casa que de verdad sirve la pagina)
#   "reenvio"  = tiene que llegar por 30x, y ademas hacia donde dice "hacia"
#   "cualquiera" = durante la mudanza vale cualquiera de las dos (cascaron)
import json, os, socket, subprocess, sys, datetime
import urllib.request, urllib.error, urllib.parse

CASA_NUEVA = "https://yodesarrollomx.github.io"   # 1-sep: la casa por omision de la org; el dominio propio llega despues
CASA_VIEJA = "https://alexpueblag.github.io"

# Endpoints que importan para que ENTREN leads. Backend del cuestionario = webhook VIVO
# al que el formulario manda los leads (recurso=textos); no la URL de lectura del board.
TARGETS = [
    {"n": "Pagina · Plan de Potencial", "url": CASA_NUEVA + "/plan-potencial/",
     "need": "Despierta tu Terreno", "espera": "directo"},
    {"n": "Pagina · Cuestionario", "url": CASA_NUEVA + "/aurum-experiencia/",
     "need": "Arquitectura de Autor", "espera": "directo"},
    {"n": "Pagina · Board", "url": CASA_NUEVA + "/aurum-board/",
     "need": "Tablero · Yo Desarrollo", "espera": "directo"},
    # Los backends NO se mudan (viven en script.google.com) pero SIEMPRE contestan por
    # reenvio a script.googleusercontent.com: asi funciona Apps Script. Declararlo evita
    # confundir ese reenvio de siempre con el cascaron de la mudanza.
    {"n": "Backend · Plan de Potencial",
     "url": "https://script.google.com/macros/s/AKfycbw3EB-6Q9Mq-ouDU-JvKMrRUaw4auYVeGkKja783yJ7_dEpCOW8xoMhs8IQMDojmlDB3A/exec?recurso=board",
     # Lo pide SIN credencial a proposito: aqui lo sano es que el Portero lo
     # rechace. Antes se exigia ok=true y el guardian llevaba tiempo gritando
     # por un backend que estaba perfecto — y de paso ya nadie le hacia caso.
     "rechazo": "liga", "espera": "reenvio", "hacia": "https://script.googleusercontent.com"},
    {"n": "Backend · Cuestionario",
     "url": "https://script.google.com/macros/s/AKfycbztAKA7K5QwO6k45PqjixYLNppLypzCpoz2KvNIkML8kciBLZVKKoais8__0DnYuEQQOg/exec?recurso=textos",
     "json_any": True, "espera": "reenvio", "hacia": "https://script.googleusercontent.com"},
    # EL CASCARON. Mientras dura la mudanza contesta directo (todavia es la casa) y
    # despues del corte tiene que contestar por reenvio a la casa nueva: por eso hoy
    # espera "cualquiera". EL DIA DEL CORTE: cambiar estos tres a "reenvio" y quedan
    # vigilando que el cascaron siga en pie (si se cae, mueren los QR impresos).
    {"n": "Cascaron · Plan de Potencial", "url": CASA_VIEJA + "/plan-potencial/",
     "espera": "cualquiera", "hacia": CASA_NUEVA},
    {"n": "Cascaron · Cuestionario", "url": CASA_VIEJA + "/aurum-experiencia/",
     "espera": "cualquiera", "hacia": CASA_NUEVA},
    {"n": "Cascaron · Board", "url": CASA_VIEJA + "/aurum-board/",
     "espera": "cualquiera", "hacia": CASA_NUEVA},
]
TITLE = "Guardian: un servicio o formulario esta caido"
LABEL = "guardian-uptime"
SALTOS = 4          # reenvios que se siguen a mano antes de rendirse
REENVIOS = (301, 302, 303, 307, 308)


class _NoSeguir(urllib.request.HTTPRedirectHandler):
    """Que urllib NO siga los reenvios solo: queremos VERLOS."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoSeguir)


def pedir(url, saltos=SALTOS):
    """GET que sigue los reenvios A MANO. -> (code, body, cadena, err)
    'cadena' = [(codigo, destino), ...]: vacia si contesto directo."""
    cadena = []
    for _ in range(saltos + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "yod-guardian"})
        try:
            with _OPENER.open(req, timeout=35) as r:
                return r.status, r.read().decode("utf-8", "ignore"), cadena, None
        except urllib.error.HTTPError as e:
            if e.code in REENVIOS:
                destino = urllib.parse.urljoin(url, e.headers.get("Location") or "")
                cadena.append((e.code, destino))
                if not destino:
                    return e.code, "", cadena, "reenvio sin Location"
                url = destino
                continue
            return e.code, "", cadena, "HTTP %d" % e.code
        except Exception as e:
            return 0, "", cadena, type(e).__name__
    return 0, "", cadena, "demasiados reenvios (%d)" % saltos


def hay_dns(url):
    """False solo cuando el nombre NO existe todavia en el DNS (casa nueva sin
    levantar). Un timeout o un reset si son caida de verdad, no 'aun no existe'."""
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        socket.getaddrinfo(host, 443)
        return True
    except socket.gaierror:
        return False
    except Exception:
        return True


def check(t):
    """-> (estado, mensaje) con estado en {'ok','falla','pendiente'}."""
    if not hay_dns(t["url"]):
        return "pendiente", "el dominio todavia no existe en el DNS (mudanza en curso)"
    url = t["url"] + ("&" if "?" in t["url"] else "?") + "cb=" + str(os.getpid())
    code, body, cadena, err = pedir(url)
    if err:
        return "falla", err + (" tras reenvio a " + cadena[-1][1] if cadena else "")
    if code != 200:
        return "falla", "HTTP %d" % code

    espera = t.get("espera", "directo")
    como = ("reenvio (%s → %s)" % (cadena[0][0], cadena[-1][1].split("?")[0])
            if cadena else "directo")
    if espera == "directo" and cadena:
        return "falla", ("responde 200 pero NO lo contesta esta casa: " + como +
                         " — un cascaron de reenvio lo esta tapando")
    if espera == "reenvio":
        if not cadena:
            return "falla", "responde 200 DIRECTO y se esperaba reenvio hacia " + t.get("hacia", "?")
        if t.get("hacia") and not cadena[-1][1].startswith(t["hacia"]):
            return "falla", "reenvia a " + cadena[-1][1].split("?")[0] + " y no a " + t["hacia"]

    # El cascaron de redireccion tambien es una pagina con <title>: si medimos
    # "trae html", saldria VERDE con el tablero caido. Se le busca su huella.
    if t.get("espera") == "directo" and 'http-equiv="refresh"' in body:
        return "falla", ("responde 200 pero es el CASCARON de redireccion, no el tablero"
                         " — el tablero real esta caido o no se publico")
    if t.get("need") and t["need"] not in body:
        return "falla", ("responde 200 (" + como + ") pero no trae su marca propia ("
                         + t["need"] + ")")
    if t.get("json_ok") or t.get("json_any") or t.get("rechazo"):
        try:
            j = json.loads(body)
        except Exception:
            return "falla", "responde 200 (" + como + ") pero no es JSON valido"
        # Backend con candado: lo sano es que RECHACE sin credencial. Un ok=true
        # aqui significaria que el candado se cayo, y eso si es para alarmarse.
        if t.get("rechazo"):
            if j.get("ok"):
                return "falla", "ENTREGA DATOS SIN CREDENCIAL — el candado se cayo"
            if j.get("error") != t["rechazo"]:
                return "falla", "rechaza con '%s' y se esperaba '%s'" % (j.get("error"), t["rechazo"])
            return "ok", "candado firme (rechaza: " + t["rechazo"] + ") · " + como
        if t.get("json_ok") and not j.get("ok"):
            return "falla", "200 pero ok=%s (%s)" % (j.get("ok"), j.get("error"))
    return "ok", "ok · " + como


def gh(*args):
    return subprocess.run(["gh"] + list(args), capture_output=True, text=True)


def main():
    fails, pendientes = [], []
    for t in TARGETS:
        estado, msg = check(t)
        etiqueta = {"ok": "OK    ", "falla": "FALLA ", "pendiente": "AUN NO"}[estado]
        print(etiqueta + " " + t["n"] + " -> " + msg)
        if estado == "falla":
            fails.append((t["n"], t["url"].split("?")[0], msg))
        elif estado == "pendiente":
            pendientes.append(t["n"])

    gh("label", "create", LABEL, "--color", "d73a4a",
       "--description", "Aviso automatico de servicio caido", "--force")
    listado = gh("issue", "list", "--label", LABEL, "--state", "open",
                 "--json", "number,title", "--limit", "20").stdout
    try:
        abiertos = [i for i in json.loads(listado or "[]") if i["title"] == TITLE]
    except Exception:
        abiertos = []
    ahora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    nota = ("\n\nAun no existen (mudanza en curso, no es caida): " +
            ", ".join(pendientes) if pendientes else "")

    if fails:
        cuerpo = ("Estos servicios no respondieron bien (" + ahora + "):\n\n" +
                  "\n".join(f"- **{n}** — {m}\n  {u}" for n, u, m in fails) +
                  nota +
                  "\n\nGuardian automatico (uptime). Se cierra solo al recuperarse todo.")
        if abiertos:
            gh("issue", "comment", str(abiertos[0]["number"]), "--body", "Sigue caido:\n\n" + cuerpo)
        else:
            gh("issue", "create", "--title", TITLE, "--label", LABEL, "--body", cuerpo)
        print(f"\n{len(fails)} servicio(s) caido(s) — issue gestionado.")
    else:
        for i in abiertos:
            gh("issue", "comment", str(i["number"]), "--body", "Todo recuperado (" + ahora + "). Cierro este aviso.")
            gh("issue", "close", str(i["number"]))
        print("\nTodo en pie." + (" (%d aun no existen)" % len(pendientes) if pendientes else ""))


if __name__ == "__main__":
    main()
