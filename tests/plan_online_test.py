import contextlib
import datetime
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = (Path(__file__).resolve().parents[1] / "scripts" / "pull_metrics.py").read_text()
NOW = datetime.datetime(2026, 10, 11, 16, 0, tzinfo=datetime.timezone.utc)

def module():
    m = type(sys)("plan_reader_under_test")
    m.__file__ = str(Path(tempfile.gettempdir()) / "plan_reader_under_test.py")
    exec(compile(SOURCE, m.__file__, "exec"), m.__dict__)
    return m

def dto():
    return {"schema": 1, "fuente": "plan_potencial_sheet", "ok": True,
            "leido_en": NOW.isoformat(), "zona_horaria": "America/Hermosillo",
            "periodo": "historico", "completo": True,
            "conteos": {"leads_historicos": 6, "citas_historicas_confirmadas": 4,
                       "leads_con_campana": 2, "sin_campana": 4,
                       "pruebas_excluidas": 1, "duplicados_identicos": 0},
            "campanas": [{"utm_campaign": "fb-2026-10-10", "utm_content": "pieza-fixture",
                         "leads": 2, "citas": 1}]}

class PlanReader(unittest.TestCase):
    def read(self, m, data=None, mutate_receipt=None, mutate_after=None):
        data = data if data is not None else dto()
        raw = json.dumps(data, sort_keys=True).encode()
        receipt = {"schema": 1, "code_sha": "a" * 40, "data_sha": "b" * 64,
                   "snapshot_id": 33, "run_id": "77",
                   "plan_sha256": hashlib.sha256(raw).hexdigest()}
        if mutate_receipt:
            mutate_receipt(receipt)
        after = dict(receipt)
        if mutate_after:
            mutate_after(after)
        responses = [io.BytesIO(json.dumps(receipt).encode()), io.BytesIO(raw),
                     io.BytesIO(json.dumps(after).encode())]
        with patch.object(m.urllib.request, "urlopen", side_effect=responses):
            return m.fetch_plan_online(now=NOW)

    def test_recorrido_valido_y_conteos_agrupados_no_se_expanden(self):
        m = module()
        records, state = self.read(m)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["_online_counts"], (2, 1))
        self.assertEqual(state["estado"], "confirmada")
        self.assertEqual(state["conteos"]["leads_historicos"], 6)
        totals = m.leads_por_campana(records)
        self.assertEqual(totals["fb-2026-10-10"], {"leads": 2, "citas": 1})
        self.assertEqual(totals["fb-2026-10-10|pieza-fixture"], {"leads": 2, "citas": 1})

    def test_hash_incorrecto_no_es_lectura_valida(self):
        with self.assertRaises(ValueError):
            self.read(module(), mutate_receipt=lambda r: r.update(plan_sha256="0" * 64))

    def test_recibo_sin_hash_plan_se_rechaza(self):
        with self.assertRaises(ValueError):
            self.read(module(), mutate_receipt=lambda r: r.pop("plan_sha256"))

    def test_publicacion_concurrente_se_rechaza(self):
        with self.assertRaises(ValueError):
            self.read(module(), mutate_after=lambda r: r.update(data_sha="c" * 64))

    def test_lectura_antigua_marca_falla(self):
        data = dto()
        data["leido_en"] = (NOW - datetime.timedelta(hours=4)).isoformat()
        with self.assertRaises(ValueError):
            self.read(module(), data)

    def test_fecha_futura_no_acredita_frescura(self):
        data = dto()
        data["leido_en"] = (NOW + datetime.timedelta(minutes=10)).isoformat()
        with self.assertRaises(ValueError):
            self.read(module(), data)

    def test_200_con_ok_false_no_es_cero_valido(self):
        data = dto()
        data["ok"] = False
        with self.assertRaises(ValueError):
            self.read(module(), data)

    def test_cero_acreditado_se_acepta(self):
        data = dto()
        data["conteos"] = {k: 0 for k in data["conteos"]}
        data["campanas"] = []
        records, state = self.read(module(), data)
        self.assertEqual(records, [])
        self.assertEqual(state["estado"], "confirmada")

    def test_filas_o_pii_fuera_del_contrato_no_entran(self):
        data = dto()
        data["Nombre"] = "fixture privada"
        with self.assertRaises(ValueError):
            self.read(module(), data)
        data = dto()
        data["campanas"][0]["email"] = "fixture@example.invalid"
        with self.assertRaises(ValueError):
            self.read(module(), data)

    def test_utm_con_pii_o_url_no_entra(self):
        for value in ("fixture@example.invalid", "https://example.invalid"):
            data = dto()
            data["campanas"][0]["utm_campaign"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(module(), data)

    def test_agregados_duplicados_o_totales_inconsistentes_no_suman(self):
        data = dto()
        data["campanas"].append(dict(data["campanas"][0]))
        with self.assertRaises(ValueError):
            self.read(module(), data)
        data = dto()
        data["conteos"]["leads_con_campana"] = 3
        data["conteos"]["sin_campana"] = 3
        with self.assertRaises(ValueError):
            self.read(module(), data)

    def test_booleanos_y_floats_no_sustituyen_conteos(self):
        for value in (True, 2.5):
            data = dto()
            data["campanas"][0]["leads"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(module(), data)

    def test_citas_no_pueden_superar_leads(self):
        data = dto()
        data["campanas"][0]["citas"] = 3
        with self.assertRaises(ValueError):
            self.read(module(), data)

    def test_schema_booleano_no_se_acepta(self):
        with self.assertRaises(ValueError):
            self.read(module(), mutate_receipt=lambda r: r.update(schema=True))

    def test_legacy_no_puede_inyectar_tupla_mediante_json(self):
        m = module()
        total = m.leads_por_campana([{"utm_campaign": "fb-2026-10-10",
                                      "_online_counts": [100, 99]}])
        self.assertEqual(total["fb-2026-10-10"], {"leads": 1, "citas": 0})

    def test_colector_consulta_plan_publicado_y_conserva_arquitectura(self):
        m = module()
        rows, state = self.read(m)
        arch = io.BytesIO(json.dumps({"ok": True, "leads_utm": []}).encode())
        with patch.object(m, "BOARD_URLS", {"potencial": m.PLAN_ONLINE_URL,
                  "arquitectura": "https://example.invalid/leads"}), \
             patch.object(m, "fetch_plan_online", return_value=(rows, state)) as plan, \
             patch.object(m.urllib.request, "urlopen", return_value=arch):
            result, failed = m.fetch_leads_utm()
        self.assertFalse(failed)
        self.assertEqual(result, rows)
        self.assertEqual(m.PLAN_STATE, state)
        plan.assert_called_once()

    def test_error_plan_no_se_convierte_en_lista_vacia_confirmada(self):
        m = module()
        with patch.object(m, "BOARD_URLS", {"potencial": m.PLAN_ONLINE_URL,
                  "arquitectura": "https://example.invalid/leads"}), \
             patch.object(m, "fetch_plan_online", side_effect=ValueError("desactualizado")), \
             patch.object(m.urllib.request, "urlopen", return_value=io.BytesIO(b'{"ok":true,"leads_utm":[]}')), \
             patch.object(m.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            rows, failed = m.fetch_leads_utm()
        self.assertTrue(failed)
        self.assertIsNone(m.PLAN_STATE)

if __name__ == "__main__":
    unittest.main()
