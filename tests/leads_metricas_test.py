import contextlib
import importlib.util
import io
import json
import os
import pathlib
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'scripts' / 'pull_metrics.py').read_text(encoding='utf-8')

def module(source):
    m = type(sys)("collector_under_test")
    m.__file__ = str(pathlib.Path(tempfile.gettempdir()) / "collector_under_test.py")
    exec(compile(source, m.__file__, "exec"), m.__dict__)
    return m

class LeadReadTest(unittest.TestCase):
    def query(self, m, payload, urls=None):
        response = io.BytesIO(json.dumps(payload).encode())
        def fresh(*a, **kw):
            return io.BytesIO(response.getvalue())
        with patch.object(m, "BOARD_URLS", urls or {"arquitectura": "https://example.invalid/leads"}), \
             patch.object(m.urllib.request, "urlopen", side_effect=fresh), \
             patch.object(m.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            return m.fetch_leads_utm()

    def test_rechazo_http_200_es_falla_de_lectura(self):
        fixed = module(SOURCE)
        self.assertEqual(self.query(fixed, {"ok": False, "error": "liga"}), ([], True))

    def test_lista_vacia_real_se_acepta(self):
        fixed = module(SOURCE)
        self.assertEqual(self.query(fixed, {"ok": True, "leads_utm": []}), ([], False))

    def test_contrato_ausente_avisa_falla(self):
        fixed = module(SOURCE)
        self.assertEqual(self.query(fixed, {"ok": True}), ([], True))

    def test_un_iman_sin_configuracion_no_certifica_ambos(self):
        fixed = module(SOURCE)
        self.assertEqual(self.query(fixed, {"leads_utm": []},
                                   {"potencial": "https://example.invalid/board"}), ([], True))

    def collect(self, source):
        m = module(source)
        previous = {
            "posts": [{"id": "p1", "leads_atribuidos": 4, "citas_atribuidas": 2}],
            "leads_utm_activo": True,
            "leads_por_pieza": {"pieza-previa": {"leads": 4, "citas": 2}}
        }
        posts = [{"id": "p1", "red": "FB", "fecha": "2026-10-10", "titulo": "Ficha"}]
        accounts = [{"clave": "yod", "nombre": "Prueba", "page_id": "p", "page_token": "fixture", "ig_id": None}]
        partial = [{"utm_campaign": "fb-2026-10-10", "utm_content": "pieza-nueva", "cita": False}]
        with tempfile.TemporaryDirectory() as td, patch.object(m, "BASE", td), \
             patch.dict(os.environ, {"YOD_META_TOKEN": "fixture"}), \
             patch.object(m, "resolve_todas", return_value=accounts), \
             patch.object(m, "pull_fb", return_value=(posts, None)), \
             patch.object(m, "fetch_leads_utm", return_value=(partial, True)), \
             patch.object(signal, "alarm"), patch.object(signal, "signal"), \
             contextlib.redirect_stdout(io.StringIO()):
            path = pathlib.Path(td) / "metrics.json"
            path.write_text(json.dumps(previous))
            m.main()
            return json.loads(path.read_text())

    def test_falla_parcial_conserva_atribucion_ya_acreditada(self):
        fixed = self.collect(SOURCE)
        self.assertEqual(fixed["posts"][0]["leads_atribuidos"], 4)
        self.assertEqual(fixed["posts"][0]["citas_atribuidas"], 2)
        self.assertEqual(fixed["leads_por_pieza"], {"pieza-previa": {"leads": 4, "citas": 2}})
        self.assertTrue(fixed["leads_medicion_caida"])

    def test_datos_validos_solo_cuentan_campanas_medibles(self):
        fixed = module(SOURCE)
        leads = [{"utm_campaign": "fb-2026-10-10"}, {"utm_campaign": ""}]
        self.assertEqual(self.query(fixed, {"ok": True, "leads_utm": leads}), (leads[:1], False))

if __name__ == "__main__":
    unittest.main()
