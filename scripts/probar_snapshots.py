"""Diagnóstico técnico de capacidades propias: no genera datos comerciales."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshots_online as so


def main():
    s = so.Snapshots()
    site = s.api.request("pages")
    before = site.get("build_type", "desconocido")
    switch = "no_necesario"
    if before != "workflow":
        try:
            s.api.request("pages", {"build_type": "workflow"}, method="PUT")
            switch = "confirmado"
        except so.OnlineError as exc:
            switch = str(exc)
    after = s.api.request("pages").get("build_type", "desconocido")
    release = s.api.request("releases", {
        "tag_name": "online-storage-probe-" + s.run + "-" + s.attempt,
        "target_commitish": "main", "name": "Prueba técnica privada",
        "draft": True, "body": "Prueba sintética temporal; ningún registro de negocio."})
    try:
        path = "metrics.json" if s.repo.endswith("/aurum-board") else "datos/revision.json"
        raw, manifest = so.encode_archive(s.repo, s.head(), {path: b'{"prueba_tecnica":true}'},
                                           [], s.prefixes)
        asset = s.api.upload(release, raw)
        back = s.api.asset(asset["id"])
        so.decode_archive(back, s.repo, s.prefixes, so.digest(raw), manifest["data_sha"])
        check = s.api.request("releases/" + str(release["id"]))
        assert check["draft"] is True
    finally:
        s.api.request("releases/" + str(release["id"]), method="DELETE")
    print("ALMACEN_ONLINE " + json.dumps({"ok": True, "draft_privado_verificado": True,
          "prueba_eliminada": True, "pages_antes": before, "pages_despues": after,
          "cambio_pages": switch, "escrituras_negocio": 0}))


if __name__ == "__main__":
    main()
