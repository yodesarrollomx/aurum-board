#!/usr/bin/env python3
"""Datos públicos versionados en releases draft privados; código continúa por PR.

Identidad: GITHUB_TOKEN del propio workflow. Un mutex Git con fast-forward
serializa escritores sin cancelar procesos distintos en la cola de Actions.
Nunca contiene credenciales ni registros privados. Antes de montar se exige
el recibo exacto de Pages y el hash de cada archivo.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SCHEMA = 1
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
PREFIX = "yod-online-"
LOCK_REF = "heads/runtime/online-data-lease"
MAX_ZIP = 100 * 1024 * 1024
MAX_PLAIN = 500 * 1024 * 1024
MAX_FILE = 25 * 1024 * 1024
MAX_FILES = 10000
SALA_PATHS = {
    "arranque": ("laminas/", "datos/tiras/", "datos/arranque.json"),
    "mesa": ("laminas/", "datos/tiras/"),
    "relevo": ("datos/manifiesto.json",),
    "maquinas": ("datos/maquinas.json",),
    "chinches": ("datos/encargos.json", "datos/piezas.json", "datos/motores_puntaje.json"),
    "revision": ("datos/revision.json",),
}
MARKETING_PATHS = {"metricas": ("metrics.json", "covers/")}


class OnlineError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def allowed(path, prefixes):
    parts = PurePosixPath(path).parts
    if (not isinstance(path, str) or not path or "\\" in path or "\x00" in path
            or path.startswith("/") or ".." in parts or "." in parts
            or str(PurePosixPath(path)) != path
            or any(p.startswith(".") for p in parts)):
        return False
    if Path(path).suffix.lower() not in {".json", ".jpg", ".jpeg", ".png", ".webp"}:
        return False
    return any(path.startswith(p) if p.endswith("/") else path == p for p in prefixes)


def validate_file(path, data):
    if len(data) > MAX_FILE:
        raise OnlineError("Archivo público demasiado grande")
    if path.endswith(".json"):
        try:
            json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise OnlineError("JSON público inválido") from exc
    elif path.lower().endswith((".jpg", ".jpeg")) and not data.startswith(b"\xff\xd8\xff"):
        raise OnlineError("JPEG inválido")
    elif path.lower().endswith(".png") and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise OnlineError("PNG inválido")
    elif path.lower().endswith(".webp") and not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise OnlineError("WEBP inválido")


def encode_archive(repo, source_sha, files, deleted, prefixes):
    if not SHA.fullmatch(source_sha):
        raise OnlineError("SHA de código inválido")
    records = []
    for path, data in sorted(files.items()):
        if not allowed(path, prefixes):
            raise OnlineError("Archivo fuera del contrato público")
        validate_file(path, data)
        records.append({"path": path, "sha256": digest(data), "bytes": len(data)})
    if (len(records) > MAX_FILES or sum(r["bytes"] for r in records) > MAX_PLAIN
            or len(deleted) != len(set(deleted))
            or any(not allowed(p, prefixes) or p in files for p in deleted)):
        raise OnlineError("Manifiesto de snapshot inválido")
    manifest = {"schema": SCHEMA, "repo": repo, "source_sha": source_sha,
                "files": records, "deleted": sorted(deleted)}
    data_sha = digest(canonical({"files": records, "deleted": sorted(deleted)}))
    manifest["data_sha"] = data_sha
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("snapshot.json", canonical(manifest))
        for path, data in sorted(files.items()):
            z.writestr("files/" + path, data)
    result = buf.getvalue()
    if len(result) > MAX_ZIP:
        raise OnlineError("Snapshot comprimido demasiado grande")
    return result, manifest


def decode_archive(data, repo, prefixes, expected_hash=None, expected_data=None):
    if len(data) > MAX_ZIP or (expected_hash and digest(data) != expected_hash):
        raise OnlineError("Hash o tamaño del snapshot incorrecto")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            if (len(names) != len(set(names)) or len(names) > MAX_FILES + 1
                    or "snapshot.json" not in names
                    or sum(i.file_size for i in infos) > MAX_PLAIN + 1024 * 1024):
                raise OnlineError("ZIP duplicado, incompleto o demasiado grande")
            for i in infos:
                if (i.is_dir() or stat.S_ISLNK(i.external_attr >> 16)
                        or i.file_size > MAX_FILE or i.flag_bits & 1):
                    raise OnlineError("ZIP contiene un tipo o tamaño no admitido")
            manifest = json.loads(z.read("snapshot.json"))
            if (manifest.get("schema") != SCHEMA or manifest.get("repo") != repo
                    or not SHA.fullmatch(str(manifest.get("source_sha", "")))
                    or not isinstance(manifest.get("files"), list)
                    or not isinstance(manifest.get("deleted"), list)):
                raise OnlineError("Manifiesto fuera del contrato")
            files, declared = {}, {"snapshot.json"}
            for r in manifest["files"]:
                if (set(r) != {"path", "sha256", "bytes"} or not allowed(r["path"], prefixes)
                        or r["path"] in files or not DIGEST.fullmatch(str(r["sha256"]))
                        or type(r["bytes"]) is not int or not 0 <= r["bytes"] <= MAX_FILE):
                    raise OnlineError("Registro de archivo inválido")
                name = "files/" + r["path"]
                raw = z.read(name)
                validate_file(r["path"], raw)
                if len(raw) != r["bytes"] or digest(raw) != r["sha256"]:
                    raise OnlineError("El archivo no coincide con su manifiesto")
                files[r["path"]] = raw
                declared.add(name)
            deleted = manifest["deleted"]
            if (len(deleted) != len(set(deleted)) or any(
                    not allowed(p, prefixes) or p in files for p in deleted)
                    or set(names) != declared):
                raise OnlineError("ZIP con archivos no declarados")
            actual = digest(canonical({"files": manifest["files"], "deleted": deleted}))
            if manifest.get("data_sha") != actual or (expected_data and actual != expected_data):
                raise OnlineError("Identidad de los datos incorrecta")
            return manifest, files
    except OnlineError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise OnlineError("No se pudo validar el snapshot") from exc


def receipt_matches(receipt, *, code_sha, data_sha, snapshot_id, run_id):
    return (isinstance(receipt, dict) and receipt.get("schema") == SCHEMA
            and receipt.get("code_sha") == code_sha and receipt.get("data_sha") == data_sha
            and receipt.get("snapshot_id") == snapshot_id and receipt.get("run_id") == run_id)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, repo, token):
        self.repo, self.token = repo, token

    def request(self, path, body=None, method=None, raw=False):
        if not path.startswith("/"):
            path = "/repos/" + self.repo + "/" + path
        req = urllib.request.Request("https://api.github.com" + path,
              data=None if body is None else canonical(body),
              method=method or ("GET" if body is None else "POST"),
              headers={"Authorization": "Bearer " + self.token,
                       "Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28",
                       "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                content = r.read()
        except urllib.error.HTTPError as exc:
            # No cuerpo remoto ni URL firmada/token en logs.
            raise OnlineError("GitHub HTTP " + str(exc.code)) from None
        except (OSError, urllib.error.URLError) as exc:
            raise OnlineError("GitHub no confirmó la operación") from None
        if raw:
            return content
        try:
            return json.loads(content) if content else None
        except ValueError:
            raise OnlineError("GitHub no devolvió JSON válido") from None

    def asset(self, asset_id):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}/releases/assets/{asset_id}",
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/octet-stream"})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=90) as r:
                data = r.read(MAX_ZIP + 1)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise OnlineError("No se pudo recuperar el snapshot privado") from None
            url = exc.headers.get("Location", "")
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme != "https" or parsed.username or parsed.password
                    or not parsed.hostname or not parsed.hostname.endswith(".githubusercontent.com")):
                raise OnlineError("Destino de descarga no permitido")
            # No reenviar Authorization al destino firmado.
            try:
                with urllib.request.urlopen(url, timeout=90) as r:
                    data = r.read(MAX_ZIP + 1)
            except (OSError, urllib.error.URLError):
                raise OnlineError("Descarga del activo no confirmada") from None
        if len(data) > MAX_ZIP:
            raise OnlineError("Snapshot demasiado grande")
        return data

    def upload(self, release, data):
        url = release["upload_url"].split("{")[0]
        expected = f"https://uploads.github.com/repos/{self.repo}/releases/{release['id']}/assets"
        if url != expected:
            raise OnlineError("Destino de subida no permitido")
        req = urllib.request.Request(url + "?name=snapshot.zip", data=data, method="POST",
              headers={"Authorization": "Bearer " + self.token,
                       "Content-Type": "application/zip",
                       "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except (ValueError, OSError, urllib.error.URLError):
            raise OnlineError("Subida del snapshot no confirmada") from None


class Snapshots:
    def __init__(self, root=None, env=None, api=None, clock=time.monotonic, sleep=time.sleep):
        self.env = os.environ if env is None else env
        self.root = Path(root or Path(__file__).resolve().parent.parent).resolve()
        self.repo = self.env.get("GITHUB_REPOSITORY", "")
        self.run = self.env.get("GITHUB_RUN_ID", "")
        self.attempt = self.env.get("GITHUB_RUN_ATTEMPT", "")
        token = self.env.get("GH_TOKEN") or self.env.get("GITHUB_TOKEN")
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo)
                or not self.run.isdecimal() or not self.attempt.isdecimal() or not token):
            raise OnlineError("Se requiere la identidad del workflow de este repositorio")
        self.paths = MARKETING_PATHS if self.repo.endswith("/aurum-board") else SALA_PATHS
        self.prefixes = tuple(dict.fromkeys(p for ps in self.paths.values() for p in ps))
        self.api = api or GitHub(self.repo, token)
        self.clock, self.sleep = clock, sleep
        state_name = self.repo.replace("/", "-") + "-" + self.run + "-" + self.attempt
        self.state_dir = Path(self.env.get("RUNNER_TEMP") or tempfile.gettempdir()) / state_name
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "online.json"

    def git(self, *args, binary=False):
        proc = subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                              text=not binary, timeout=120)
        if proc.returncode:
            raise OnlineError("No se pudo comprobar el árbol de código")
        return proc.stdout if binary else proc.stdout.strip()

    def head(self):
        sha = self.git("rev-parse", "HEAD")
        if not SHA.fullmatch(sha):
            raise OnlineError("Código sin SHA válido")
        return sha

    def main(self):
        return self.api.request("git/ref/heads/main")["object"]["sha"]

    def state(self, required=True):
        if not self.state_file.exists():
            if required:
                raise OnlineError("Falta hidratar y validar el snapshot inicial")
            return {}
        return json.loads(self.state_file.read_text())

    def save(self, state):
        self.state_file.write_text(json.dumps(state, sort_keys=True) + "\n")

    def output(self, **values):
        target = self.env.get("GITHUB_OUTPUT")
        if target:
            with open(target, "a", encoding="utf-8") as f:
                for key, value in values.items():
                    if "\n" in str(value):
                        raise OnlineError("Salida multilinea no admitida")
                    f.write(f"{key}={value}\n")
        return values

    def _lock_info(self):
        try:
            sha = self.api.request("git/ref/" + LOCK_REF)["object"]["sha"]
        except OnlineError as exc:
            if str(exc) == "GitHub HTTP 404":
                return None, None
            raise
        commit = self.api.request("git/commits/" + sha)
        try:
            info = json.loads(commit["message"])
        except (ValueError, KeyError):
            raise OnlineError("Mutex de datos sin contrato") from None
        if (info.get("schema") != SCHEMA or info.get("state") not in ("held", "released")
                or not str(info.get("run_id", "")).isdecimal() or type(info.get("expires")) is not int):
            raise OnlineError("Mutex de datos inválido")
        return sha, info

    def acquire(self, process, timeout=1800):
        deadline = self.clock() + timeout
        tree = self.api.request("git/trees", {"tree": [{
            "path": "README.md", "mode": "100644", "type": "blob",
            "content": "Runtime mutex for validated public data; no business records.\n"}]})["sha"]
        while True:
            before, info = self._lock_info()
            busy = info and info["state"] == "held"
            if busy and info["expires"] <= time.time():
                busy = False
            if busy:
                owner = self.api.request("actions/runs/" + str(info["run_id"]))
                busy = owner["status"] != "completed"
            if not busy:
                payload = {"schema": SCHEMA, "state": "held", "run_id": self.run,
                           "attempt": self.attempt, "process": process,
                           "expires": int(time.time()) + 7200}
                commit = self.api.request("git/commits", {
                    "message": canonical(payload).decode(), "tree": tree,
                    "parents": [] if before is None else [before]})["sha"]
                try:
                    if before is None:
                        self.api.request("git/refs", {"ref": "refs/" + LOCK_REF, "sha": commit})
                    else:
                        # Non-fast-forward rejects sibling commits: compare-and-swap.
                        self.api.request("git/refs/" + LOCK_REF, {"sha": commit, "force": False},
                                         method="PATCH")
                    state = self.state(required=False)
                    state["lease"] = commit
                    state["process"] = process
                    self.save(state)
                    return commit
                except OnlineError as exc:
                    if str(exc) not in ("GitHub HTTP 409", "GitHub HTTP 422"):
                        raise
            if self.clock() >= deadline:
                raise OnlineError("Mutex ocupado: datos conservados, publicación pendiente")
            self.sleep(10)

    def assert_lease(self, lease=None):
        expected = lease or self.state().get("lease")
        sha, info = self._lock_info()
        if (not expected or sha != expected or not info or info["state"] != "held"
                or info["expires"] <= time.time()):
            raise OnlineError("La publicación perdió el mutex; no se puede confirmar")
        return info

    def release(self):
        state = self.state(required=False)
        if not state.get("lease"):
            return
        sha, info = self._lock_info()
        if sha != state["lease"] or info["run_id"] != self.run:
            raise OnlineError("No se libera un mutex de otro proceso")
        info["state"] = "released"
        commit = self.api.request("git/commits", {
            "message": canonical(info).decode(),
            "tree": self.api.request("git/commits/" + sha)["tree"]["sha"],
            "parents": [sha]})["sha"]
        self.api.request("git/refs/" + LOCK_REF, {"sha": commit, "force": False}, method="PATCH")
        state.pop("lease", None)
        self.save(state)

    def latest(self):
        releases = []
        for page in range(1, 6):
            batch = self.api.request(f"releases?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise OnlineError("No se pudo leer el historial de snapshots")
            releases.extend(batch)
            if len(batch) < 100:
                break
        found = []
        for release in releases:
            if not release.get("draft") or not release.get("tag_name", "").startswith(PREFIX):
                continue
            try:
                body = json.loads(release["body"] or "")
            except ValueError:
                raise OnlineError("Snapshot privado sin contrato") from None
            if body.get("schema") == SCHEMA and body.get("repo") == self.repo and body.get("state") == "published":
                self.validate_release(release, body)
                found.append((release, body))
        return max(found, key=lambda r: r[0]["id"]) if found else (None, None)

    def validate_release(self, release, body):
        if (not release.get("draft") or not release.get("tag_name", "").startswith(PREFIX)
                or body.get("schema") != SCHEMA or body.get("repo") != self.repo
                or body.get("state") not in ("prepared", "published")
                or not SHA.fullmatch(str(body.get("source_sha", "")))
                or not DIGEST.fullmatch(str(body.get("archive_sha256", "")))
                or not DIGEST.fullmatch(str(body.get("data_sha", "")))
                or type(body.get("parent_id")) is not int
                or type(body.get("asset_id")) is not int
                or not any(a["id"] == body["asset_id"] and a["name"] == "snapshot.zip"
                           for a in release.get("assets", []))):
            raise OnlineError("Versión privada inválida")

    def download(self, release, body):
        self.validate_release(release, body)
        return decode_archive(self.api.asset(body["asset_id"]), self.repo, self.prefixes,
                              body["archive_sha256"], body["data_sha"])

    def fingerprint(self):
        files = {}
        for path in self.root.rglob("*"):
            rel = path.relative_to(self.root).as_posix()
            if allowed(rel, self.prefixes):
                if path.is_symlink():
                    raise OnlineError("Symlink dentro de datos públicos")
                if path.is_file():
                    files[rel] = digest(path.read_bytes())
        return files

    def apply(self, manifest, files):
        # Todo el ZIP ya validado antes de la primera escritura.
        for path in list(files) + manifest["deleted"]:
            target = self.root / path
            if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != self.root):
                raise OnlineError("Destino de snapshot con symlink")
        for path in manifest["deleted"]:
            target = self.root / path
            if target.exists():
                target.unlink()
        for path, raw in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".online-tmp")
            tmp.write_bytes(raw)
            tmp.replace(target)

    def bootstrap(self, process=None, lock=False):
        if process and process not in self.paths:
            raise OnlineError("Proceso sin contrato")
        if lock:
            self.acquire(process)
        state = self.state(required=False)
        release, body = self.latest()
        if release:
            manifest, files = self.download(release, body)
            self.apply(manifest, files)
        state.update({"source_sha": self.head(), "parent_id": release["id"] if release else 0,
                      "baseline": self.fingerprint(), "data_sha": body["data_sha"] if body else digest(canonical({"files": [], "deleted": []}))})
        self.save(state)
        return self.output(snapshot_id=state["parent_id"], hidratado="si")

    def prepare(self, process):
        if process not in self.paths:
            raise OnlineError("Proceso sin contrato")
        state = self.state()
        self.assert_lease()
        source = self.head()
        if source != state["source_sha"] or self.main() != source:
            raise OnlineError("main avanzó; se requiere una nueva ejecución")
        current = self.fingerprint()
        changed = {p for p in set(current) | set(state["baseline"])
                   if current.get(p) != state["baseline"].get(p)}
        if any(not allowed(p, self.paths[process]) for p in changed):
            raise OnlineError("El productor cambió datos de otro proceso")
        if not changed:
            state["prepared_id"] = 0
            self.save(state)
            return self.output(hubo="no", merge_sha=source, snapshot_id=state["parent_id"])
        paths = set(self.git("diff", "--name-only", "HEAD").splitlines())
        if any(not allowed(p, self.prefixes) for p in paths):
            raise OnlineError("El productor alteró código; se requiere PR")
        untracked = self.git("ls-files", "--others", "--exclude-standard").splitlines()
        candidates = sorted(p for p in paths | set(untracked) if allowed(p, self.prefixes))
        files = {p: (self.root / p).read_bytes() for p in candidates if (self.root / p).is_file()}
        deleted = [p for p in candidates if not (self.root / p).exists()]
        raw, manifest = encode_archive(self.repo, source, files, deleted, self.prefixes)
        release = self.api.request("releases", {
            "tag_name": PREFIX + self.run + "-" + self.attempt + "-" + process,
            "target_commitish": source, "name": "Datos online " + process,
            "draft": True, "body": canonical({"schema": SCHEMA, "repo": self.repo,
                                            "state": "uploading"}).decode()})
        asset = self.api.upload(release, raw)
        body = {"schema": SCHEMA, "repo": self.repo, "state": "prepared",
                "process": process, "source_sha": source, "parent_id": state["parent_id"],
                "archive_sha256": digest(raw), "data_sha": manifest["data_sha"],
                "asset_id": asset["id"], "lease": state["lease"], "run_id": self.run}
        self.api.request("releases/" + str(release["id"]),
                         {"body": canonical(body).decode()}, method="PATCH")
        if digest(self.api.asset(asset["id"])) != body["archive_sha256"]:
            raise OnlineError("El snapshot subido no coincide")
        state.update({"prepared_id": release["id"], "data_sha": body["data_sha"]})
        self.save(state)
        return self.output(hubo="si", merge_sha=source, snapshot_id=release["id"],
                           data_sha=body["data_sha"])

    def dispatch_publish(self, sha, timeout=1200):
        state = self.state()
        if not SHA.fullmatch(sha) or self.main() != sha:
            raise OnlineError("Código de publicación sin confirmar")
        self.assert_lease()
        rid = state.get("prepared_id") or state["parent_id"]
        publication = f"datos-{self.run}-{self.attempt}-{state.get('process', 'manual')}"
        self.api.request("actions/workflows/publicar.yml/dispatches", {
            "ref": "main", "inputs": {"sha": sha, "publicacion_id": publication,
                                      "snapshot_id": str(rid), "lease": state["lease"]}})
        deadline = self.clock() + timeout
        title = f"Sala · publicacion={publication} · sha={sha}"
        while self.clock() < deadline:
            runs = self.api.request("actions/workflows/publicar.yml/runs?event=workflow_dispatch&per_page=30")["workflow_runs"]
            run = next((r for r in runs if r["display_title"] == title
                        and r["head_sha"] == sha and r["head_branch"] == "main"), None)
            if run and run["status"] == "completed":
                if run["conclusion"] != "success":
                    raise OnlineError("Pages falló; snapshot conservado sin montar")
                release = self.api.request("releases/" + str(rid)) if rid else None
                body = json.loads(release["body"]) if release else None
                if release:
                    self.validate_release(release, body)
                    if body["state"] != "published":
                        raise OnlineError("Pages no confirmó el snapshot")
                self.verify_receipt(sha, state["data_sha"], rid, str(run["id"]))
                state["published_receipt"] = {"schema": SCHEMA, "code_sha": sha,
                    "data_sha": state["data_sha"], "snapshot_id": rid, "run_id": str(run["id"])}
                self.save(state)
                return self.output(publicado="si", snapshot_id=rid, pages_run=run["id"])
            self.sleep(10)
        raise OnlineError("Pages sin recibo a tiempo; snapshot conservado")

    def stage(self, sha, snapshot_id=0, lease=None):
        if not SHA.fullmatch(sha) or self.main() != sha or self.head() != sha:
            raise OnlineError("Sólo se publica código de main confirmado")
        if snapshot_id:
            if not lease:
                raise OnlineError("Falta mutex del productor")
            self.assert_lease(lease)
            release = self.api.request("releases/" + str(snapshot_id))
            body = json.loads(release["body"])
            self.validate_release(release, body)
            latest, _ = self.latest()
            if body["state"] == "prepared" and (body["source_sha"] != sha
                    or body.get("lease") != lease or body["parent_id"] != (latest["id"] if latest else 0)):
                raise OnlineError("Snapshot concurrente o código diferente")
        else:
            release, body = self.latest()
        if release:
            manifest, files = self.download(release, body)
            self.apply(manifest, files)
            data_sha, rid = body["data_sha"], release["id"]
        else:
            data_sha, rid = digest(canonical({"files": [], "deleted": []})), 0
        receipt = {"schema": SCHEMA, "code_sha": sha, "data_sha": data_sha,
                   "snapshot_id": rid, "run_id": self.run}
        (self.root / "version-online.json").write_bytes(canonical(receipt) + b"\n")
        state = {"source_sha": sha, "snapshot_id": rid, "data_sha": data_sha,
                 "lease": lease, "published_receipt": receipt}
        self.save(state)
        return self.output(snapshot_id=rid, data_sha=data_sha)

    def build_site(self, destination):
        import shutil
        dest = Path(destination).resolve()
        if dest == self.root or self.root in dest.parents:
            raise OnlineError("El bundle debe estar fuera del checkout")
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        # Sólo archivos de Git aprobado y outputs allowlisted. Nunca logs ni credenciales.
        tracked = self.git("ls-files", "-z").split("\0")
        public = set(tracked) | set(self.fingerprint()) | {"version-online.json"}
        for rel in public:
            if not rel or rel.startswith(".github/") or rel.startswith(".git"):
                continue
            path = self.root / rel
            if path.is_symlink():
                raise OnlineError("Bundle con symlink")
            if path.is_file():
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        return self.output(bundle="si")

    def verify_receipt(self, sha, data_sha, snapshot_id, run_id, timeout=300):
        site = self.api.request("pages")["html_url"].rstrip("/") + "/"
        parsed = urllib.parse.urlsplit(site)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise OnlineError("Sitio Pages sin HTTPS")
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            url = site + "version-online.json?run=" + urllib.parse.quote(run_id)
            try:
                req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=30) as response:
                    receipt = json.loads(response.read(32768))
                if receipt_matches(receipt, code_sha=sha, data_sha=data_sha,
                                   snapshot_id=snapshot_id, run_id=run_id):
                    return receipt
            except (ValueError, OSError, urllib.error.URLError):
                pass
            self.sleep(5)
        raise OnlineError("El sitio no acredita la versión exacta")

    def confirm(self):
        state = self.state()
        sha, rid = state["source_sha"], state["snapshot_id"]
        self.verify_receipt(sha, state["data_sha"], rid, self.run)
        if self.main() != sha:
            raise OnlineError("main avanzó durante la publicación")
        if rid:
            release = self.api.request("releases/" + str(rid))
            body = json.loads(release["body"])
            self.validate_release(release, body)
            if body["state"] == "prepared":
                self.assert_lease(state.get("lease"))
                latest, _ = self.latest()
                if body["parent_id"] != (latest["id"] if latest else 0):
                    raise OnlineError("La versión padre cambió")
                body.update({"state": "published", "published_code_sha": sha,
                             "published_run": self.run, "confirmed_at": int(time.time())})
                self.api.request("releases/" + str(rid),
                                 {"body": canonical(body).decode()}, method="PATCH")
        return self.output(publicado="si", snapshot_id=rid)

    def publish_raw(self):
        """Board: datos públicos separados del código de Pages, sin permiso admin."""
        import base64
        state = self.state()
        self.assert_lease()
        source = self.head()
        if self.main() != source or source != state["source_sha"]:
            raise OnlineError("El código cambió durante la medición")
        rid = state.get("prepared_id") or state["parent_id"]
        receipt = {"schema": SCHEMA, "code_sha": source, "data_sha": state["data_sha"],
                   "snapshot_id": rid, "run_id": self.run}
        if self.repo.endswith("/aurum-board"):
            metric_file = self.root / "metrics.json"
            if not metric_file.is_file():
                raise OnlineError("Métricas públicas ausentes")
            receipt["metrics_sha256"] = digest(metric_file.read_bytes())
        ref = "heads/datos-online"
        try:
            before = self.api.request("git/ref/" + ref)["object"]["sha"]
        except OnlineError as exc:
            if str(exc) != "GitHub HTTP 404":
                raise
            before = None
        base_tree = None
        existing = {}
        if before:
            base_tree = self.api.request("git/commits/" + before)["tree"]["sha"]
            entries = self.api.request("git/trees/" + base_tree + "?recursive=1")
            if entries.get("truncated"):
                raise OnlineError("Árbol de datos incompleto")
            for entry in entries["tree"]:
                if entry["type"] == "tree":
                    continue
                path = entry["path"]
                if (entry["mode"] != "100644" or entry["type"] != "blob"
                        or (path != "version-online.json" and not allowed(path, self.prefixes))):
                    raise OnlineError("La rama de datos contiene código o un archivo no permitido")
                existing[path] = entry["sha"]
        files = {p: (self.root / p).read_bytes() for p in self.fingerprint()}
        for path, data in files.items():
            validate_file(path, data)
        files["version-online.json"] = canonical(receipt) + b"\n"
        tree = []
        for path, data in sorted(files.items()):
            blob_sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if existing.get(path) == blob_sha:
                continue
            blob = self.api.request("git/blobs", {
                "encoding": "base64", "content": base64.b64encode(data).decode()})["sha"]
            if blob != blob_sha:
                raise OnlineError("GitHub no confirmó los bytes públicos")
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob})
        for path in set(existing) - set(files):
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        payload = {"tree": tree}
        if base_tree:
            payload["base_tree"] = base_tree
        tree_sha = self.api.request("git/trees", payload)["sha"]
        commit = self.api.request("git/commits", {
            "message": "Datos públicos online " + self.run + " · " + state["data_sha"][:12],
            "tree": tree_sha, "parents": [before] if before else []})["sha"]
        self.assert_lease()
        if self.main() != source:
            raise OnlineError("main avanzó antes de publicar datos")
        if before:
            self.api.request("git/refs/" + ref, {"sha": commit, "force": False}, method="PATCH")
        else:
            self.api.request("git/refs", {"ref": "refs/" + ref, "sha": commit})
        url = f"https://raw.githubusercontent.com/{self.repo}/datos-online/version-online.json"
        deadline = self.clock() + 300
        while self.clock() < deadline:
            try:
                req = urllib.request.Request(url + "?run=" + self.run,
                                              headers={"Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=30) as response:
                    observed = json.loads(response.read(32768))
                if receipt_matches(observed, code_sha=source, data_sha=state["data_sha"],
                                   snapshot_id=rid, run_id=self.run):
                    break
            except (ValueError, OSError, urllib.error.URLError):
                pass
            self.sleep(5)
        else:
            raise OnlineError("La rama pública aún no acredita el recibo exacto")
        self.assert_lease()
        if self.api.request("git/ref/" + ref)["object"]["sha"] != commit:
            raise OnlineError("La publicación de datos cambió concurrentemente")
        if rid and state.get("prepared_id"):
            release = self.api.request("releases/" + str(rid))
            body = json.loads(release["body"])
            self.validate_release(release, body)
            body.update({"state": "published", "published_code_sha": source,
                         "published_run": self.run, "confirmed_at": int(time.time()),
                         "raw_commit": commit})
            self.api.request("releases/" + str(rid), {"body": canonical(body).decode()}, method="PATCH")
        state["published_receipt"] = receipt
        self.save(state)
        return self.output(publicado="si", data_commit=commit, snapshot_id=rid)

    def file_is_published(self, path):
        state = self.state()
        receipt = state.get("published_receipt")
        if not receipt or not receipt_matches(receipt, code_sha=self.head(),
                data_sha=state["data_sha"], snapshot_id=receipt["snapshot_id"],
                run_id=receipt["run_id"]):
            return False
        if not receipt["snapshot_id"]:
            relative = Path(path).resolve().relative_to(self.root).as_posix()
            try:
                return allowed(relative, self.prefixes) and digest(Path(path).read_bytes()) == digest(
                    self.git("show", self.head() + ":" + relative, binary=True))
            except (OnlineError, OSError):
                return False
        release = self.api.request("releases/" + str(receipt["snapshot_id"]))
        body = json.loads(release["body"])
        if body["state"] != "published" or body["data_sha"] != state["data_sha"]:
            return False
        manifest, files = self.download(release, body)
        relative = Path(path).resolve().relative_to(self.root).as_posix()
        local = self.root / relative
        # Un archivo base que nunca cambió también debe acreditar su hash Git.
        if relative in files:
            return local.is_file() and digest(local.read_bytes()) == digest(files[relative])
        if relative in manifest["deleted"] or not allowed(relative, self.prefixes):
            return False
        try:
            return digest(local.read_bytes()) == digest(self.git("show", self.head() + ":" + relative, binary=True))
        except (OnlineError, OSError):
            return False


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("accion", choices=["bootstrap", "preparar", "publicar", "stage", "confirmar", "liberar", "bundle", "publicar-raw"])
    parser.add_argument("--proceso")
    parser.add_argument("--lock", action="store_true")
    parser.add_argument("--sha")
    parser.add_argument("--snapshot-id", type=int, default=0)
    parser.add_argument("--lease")
    parser.add_argument("--destino")
    args = parser.parse_args()
    s = Snapshots()
    if args.accion == "bootstrap":
        value = s.bootstrap(args.proceso, args.lock)
    elif args.accion == "preparar":
        value = s.prepare(args.proceso)
    elif args.accion == "publicar":
        value = s.dispatch_publish(args.sha)
    elif args.accion == "stage":
        value = s.stage(args.sha, args.snapshot_id, args.lease)
    elif args.accion == "confirmar":
        value = s.confirm()
    elif args.accion == "publicar-raw":
        value = s.publish_raw()
    elif args.accion == "bundle":
        value = s.build_site(args.destino)
    else:
        s.release()
        value = {"liberado": "si"}
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    try:
        cli()
    except OnlineError as exc:
        print("ERROR_ONLINE: " + str(exc))
        raise SystemExit(1)
