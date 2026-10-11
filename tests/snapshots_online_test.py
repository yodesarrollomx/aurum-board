"""Contratos de almacenamiento y publicación: dobles, sin red ni datos reales."""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ("nube" if (ROOT / "nube").is_dir() else "scripts")))
import snapshots_online as so


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.repo = "yodesarrollomx/sala-edicion"
        self.prefixes = ("datos/tiras/", "laminas/")
        self.path = "datos/tiras/prueba.json"
        self.data, self.manifest = so.encode_archive(
            self.repo, "a" * 40, {self.path: b'{"prueba":true}'}, [], self.prefixes)

    def test_roundtrip_and_hash(self):
        manifest, files = so.decode_archive(self.data, self.repo, self.prefixes,
                                            so.digest(self.data), self.manifest["data_sha"])
        self.assertEqual(files[self.path], b'{"prueba":true}')
        self.assertEqual(manifest["data_sha"], self.manifest["data_sha"])

    def test_wrong_archive_hash(self):
        with self.assertRaises(so.OnlineError):
            so.decode_archive(self.data, self.repo, self.prefixes, "0" * 64)

    def test_wrong_repo(self):
        with self.assertRaises(so.OnlineError):
            so.decode_archive(self.data, "otro/repositorio", self.prefixes)

    def test_wrong_data_hash(self):
        with self.assertRaises(so.OnlineError):
            so.decode_archive(self.data, self.repo, self.prefixes, expected_data="0" * 64)

    def test_paths_and_secret_files_rejected(self):
        for path in ("../datos/tiras/x.json", "datos/tiras/../x.json",
                     "datos/tiras//x.json", "/datos/tiras/x.json", ".env",
                     "nube/secreto.json", "datos/tiras/.secreto.json", "laminas/ejecutar.py"):
            with self.subTest(path=path):
                self.assertFalse(so.allowed(path, self.prefixes))
                with self.assertRaises(so.OnlineError):
                    so.encode_archive(self.repo, "a" * 40, {path: b"{}"}, [], self.prefixes)

    def test_invalid_json(self):
        with self.assertRaises(so.OnlineError):
            so.encode_archive(self.repo, "a" * 40, {self.path: b"no es JSON"}, [], self.prefixes)

    def altered_zip(self, mutation):
        src, out = zipfile.ZipFile(io.BytesIO(self.data)), io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for info in src.infolist():
                name, data = info.filename, src.read(info.filename)
                if name == "snapshot.json":
                    m = json.loads(data)
                    mutation(m)
                    data = so.canonical(m)
                z.writestr(name, data)
        return out.getvalue()

    def test_file_checksum_tampering(self):
        altered = self.altered_zip(lambda m: m["files"][0].update(sha256="0" * 64))
        with self.assertRaises(so.OnlineError):
            so.decode_archive(altered, self.repo, self.prefixes)

    def test_undeclared_and_duplicate_entry(self):
        for path in ("files/nube/secret.json", "snapshot.json"):
            out = io.BytesIO(self.data)
            with zipfile.ZipFile(out, "a") as z:
                z.writestr(path, b"{}")
            with self.assertRaises(so.OnlineError):
                so.decode_archive(out.getvalue(), self.repo, self.prefixes)

    def test_symlink(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("snapshot.json", so.canonical(self.manifest))
            info = zipfile.ZipInfo("files/" + self.path)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, b"secret")
        with self.assertRaises(so.OnlineError):
            so.decode_archive(out.getvalue(), self.repo, self.prefixes)

    def test_declared_size_and_limits(self):
        altered = self.altered_zip(lambda m: m["files"][0].update(bytes=999))
        with self.assertRaises(so.OnlineError):
            so.decode_archive(altered, self.repo, self.prefixes)
        with patch.object(so, "MAX_ZIP", 10):
            with self.assertRaises(so.OnlineError):
                so.decode_archive(self.data, self.repo, self.prefixes)

    def test_deletion_contract(self):
        raw, _ = so.encode_archive(self.repo, "a" * 40, {}, [self.path], self.prefixes)
        m, files = so.decode_archive(raw, self.repo, self.prefixes)
        self.assertEqual(m["deleted"], [self.path])
        self.assertEqual(files, {})
        with self.assertRaises(so.OnlineError):
            so.encode_archive(self.repo, "a" * 40, {}, ["nube/private.json"], self.prefixes)

    def test_receipt_is_exact(self):
        expected = {"code_sha": "a" * 40, "data_sha": "b" * 64, "snapshot_id": 12, "run_id": "34"}
        receipt = {"schema": 1, **expected}
        self.assertTrue(so.receipt_matches(receipt, **expected))
        for key, value in expected.items():
            self.assertFalse(so.receipt_matches({**receipt, key: str(value) + "x"}, **expected))


class FakeGitHub:
    def __init__(self, main):
        self.main = main
        self.lock = None
        self.commits, self.releases, self.assets = {}, {}, {}
        self.number = 0

    def request(self, path, body=None, method=None):
        if path == "git/ref/heads/main":
            return {"object": {"sha": self.main}}
        if path == "git/ref/" + so.LOCK_REF:
            if not self.lock:
                raise so.OnlineError("GitHub HTTP 404")
            return {"object": {"sha": self.lock}}
        if path == "git/trees":
            return {"sha": "d" * 40}
        if path == "git/commits":
            self.number += 1
            sha = f"{self.number:040x}"
            self.commits[sha] = {**copy.deepcopy(body), "tree": {"sha": body["tree"]}}
            return {"sha": sha}
        if path.startswith("git/commits/"):
            return copy.deepcopy(self.commits[path.rsplit("/", 1)[-1]])
        if path == "git/refs":
            if self.lock:
                raise so.OnlineError("GitHub HTTP 422")
            self.lock = body["sha"]
            return {}
        if path == "git/refs/" + so.LOCK_REF:
            if self.lock not in self.commits[body["sha"]]["parents"]:
                raise so.OnlineError("GitHub HTTP 422")
            self.lock = body["sha"]
            return {}
        if path.startswith("actions/runs/"):
            return {"status": "in_progress"}
        if path.startswith("releases?"):
            return copy.deepcopy(list(self.releases.values()))
        if path == "releases":
            self.number += 1
            rid = self.number
            self.releases[rid] = {**copy.deepcopy(body), "id": rid, "assets": [],
                                   "upload_url": "unused"}
            return copy.deepcopy(self.releases[rid])
        if path.startswith("releases/"):
            rid = int(path.split("/")[1])
            if body:
                self.releases[rid].update(copy.deepcopy(body))
            return copy.deepcopy(self.releases[rid])
        raise AssertionError("API no esperada: " + path)

    def upload(self, release, data):
        self.number += 1
        aid = self.number
        self.assets[aid] = data
        asset = {"id": aid, "name": "snapshot.zip"}
        self.releases[release["id"]]["assets"].append(asset)
        return asset

    def asset(self, aid):
        return self.assets[aid]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.path = self.root / "datos/manifiesto.json"
        self.path.parent.mkdir()
        self.path.write_text('{"version":1}')
        (self.root / "index.html").write_text("<!doctype html><title>Prueba</title>")
        self.run_git("init", "-q")
        self.run_git("add", ".")
        self.run_git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
        head = self.run_git("rev-parse", "HEAD")
        self.api = FakeGitHub(head)
        self.env = {"GITHUB_REPOSITORY": "yodesarrollomx/sala-edicion", "GITHUB_RUN_ID": "12",
                    "GITHUB_RUN_ATTEMPT": "1", "GH_TOKEN": "doble",
                    "RUNNER_TEMP": str(Path(self.tmp.name) / "state")}
        self.s = so.Snapshots(self.root, self.env, api=self.api)
        self.s.bootstrap("relevo", lock=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, text=True,
                              capture_output=True, check=True).stdout.strip()

    def prepare(self):
        self.path.write_text('{"version":2}')
        out = self.s.prepare("relevo")
        return out, self.api.releases[out["snapshot_id"]]

    def test_no_change_is_not_a_new_snapshot(self):
        result = self.s.prepare("relevo")
        self.assertEqual(result["hubo"], "no")
        self.assertEqual(self.api.releases, {})

    def test_prepare_is_private_and_not_published(self):
        out, release = self.prepare()
        self.assertTrue(release["draft"])
        self.assertEqual(json.loads(release["body"])["state"], "prepared")
        self.assertFalse(self.s.file_is_published(self.path))
        self.assertEqual(self.s.latest(), (None, None))

    def test_other_process_output_rejected(self):
        (self.root / "datos/maquinas.json").write_text("{}")
        with self.assertRaises(so.OnlineError):
            self.s.prepare("relevo")

    def test_code_change_needs_pr(self):
        self.path.write_text('{"version":2}')
        (self.root / "index.html").write_text("alterado")
        with self.assertRaises(so.OnlineError):
            self.s.prepare("relevo")

    def test_main_move_rejected(self):
        self.path.write_text('{"version":2}')
        self.api.main = "b" * 40
        with self.assertRaises(so.OnlineError):
            self.s.prepare("relevo")

    def test_lease_fences_old_writer(self):
        self.path.write_text('{"version":2}')
        self.api.lock = "e" * 40
        self.api.commits[self.api.lock] = {"message": json.dumps({
            "schema": 1, "state": "held", "run_id": "13", "expires": int(time.time()) + 100})}
        with self.assertRaises(so.OnlineError):
            self.s.prepare("relevo")
        with self.assertRaises(so.OnlineError):
            self.s.release()

    def test_stage_rejects_wrong_code_or_parent(self):
        out, release = self.prepare()
        with self.assertRaises(so.OnlineError):
            self.s.stage("b" * 40, out["snapshot_id"], self.s.state()["lease"])
        body = json.loads(release["body"])
        body["parent_id"] = 999
        self.api.releases[release["id"]]["body"] = json.dumps(body)
        with self.assertRaises(so.OnlineError):
            self.s.stage(self.s.head(), out["snapshot_id"], self.s.state()["lease"])
        self.assertFalse((self.root / "version-online.json").exists())

    def test_stage_and_receipt_confirm_exact_file(self):
        out, release = self.prepare()
        self.s.stage(self.s.head(), out["snapshot_id"], self.s.state()["lease"])
        with patch.object(self.s, "verify_receipt", return_value={"ok": True}):
            self.s.confirm()
        self.assertEqual(json.loads(self.api.releases[release["id"]]["body"])["state"], "published")
        self.assertTrue(self.s.file_is_published(self.path))
        self.path.write_text('{"version":3}')
        self.assertFalse(self.s.file_is_published(self.path))

    def test_confirm_failure_never_marks_published(self):
        out, release = self.prepare()
        self.s.stage(self.s.head(), out["snapshot_id"], self.s.state()["lease"])
        with patch.object(self.s, "verify_receipt", side_effect=so.OnlineError("sin recibo")):
            with self.assertRaises(so.OnlineError):
                self.s.confirm()
        self.assertEqual(json.loads(self.api.releases[release["id"]]["body"])["state"], "prepared")

    def test_bundle_excludes_private_runtime_outputs(self):
        (self.root / "mesa.log").write_text("contenido privado")
        (self.root / "secreto.json").write_text("credencial")
        self.s.stage(self.s.head())
        bundle = Path(self.tmp.name) / "site"
        self.s.build_site(bundle)
        self.assertTrue((bundle / "index.html").exists())
        self.assertTrue((bundle / "version-online.json").exists())
        self.assertFalse((bundle / "mesa.log").exists())
        self.assertFalse((bundle / "secreto.json").exists())

    def test_untagged_draft_is_identified_by_kind_and_hash(self):
        out, release = self.prepare()
        self.api.releases[release["id"]]["tag_name"] = "untagged-github-generated"
        observed = self.api.request("releases/" + str(release["id"]))
        self.s.validate_release(observed, json.loads(observed["body"]))

    def test_unrelated_draft_does_not_become_a_snapshot(self):
        self.api.releases[999] = {"id": 999, "draft": True, "tag_name": "untagged-human",
                                  "body": "Notas privadas de otra publicación"}
        self.assertEqual(self.s.latest(), (None, None))

    def test_expired_lease_can_recover_without_old_run(self):
        before = self.s.state()["lease"]
        info = json.loads(self.api.commits[before]["message"])
        info["expires"] = 0
        self.api.commits[before]["message"] = json.dumps(info)
        original = self.api.request
        def guarded(path, *args, **kwargs):
            if path.startswith("actions/runs/"):
                raise AssertionError("No consultar un run desaparecido para un mutex caducado")
            return original(path, *args, **kwargs)
        with patch.object(self.api, "request", side_effect=guarded):
            self.s.acquire("relevo")
        self.assertNotEqual(self.s.state()["lease"], before)

    def test_release_preserves_history(self):
        before = self.s.state()["lease"]
        self.s.release()
        self.assertEqual(json.loads(self.api.commits[self.api.lock]["message"])["state"], "released")
        self.assertIn(before, self.api.commits[self.api.lock]["parents"])


if __name__ == "__main__":
    unittest.main()
