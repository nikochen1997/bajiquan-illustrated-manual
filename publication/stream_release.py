#!/usr/bin/env python3
"""Freeze sources, stream independent TAR volumes to a Release, then verify.

No archive files are written locally. Source files are never changed or removed.
Each volume is an independently extractable deterministic UTF-8 PAX TAR.
"""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlencode, urlsplit


MAX_VOLUME_BYTES = 1024 ** 3
CHUNK = 1024 ** 2
DEFAULT_AUDIT = "work/github-publication-audit"


class PublicationError(Exception):
    pass


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for data in iter(lambda: f.read(CHUNK), b""):
            h.update(data)
    return h.hexdigest()


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as f:
            temporary = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def safe_archive_name(name):
    p = PurePosixPath(name)
    if (not name or name in (".", "..") or p.is_absolute()
            or ".." in p.parts or "\\" in name or "\x00" in name
            or re.match(r"^[A-Za-z]:", name) or str(p) != name):
        raise PublicationError("Unsafe archive path: " + name)
    return name


def audit_path(root, path):
    root = Path(root).resolve()
    path = Path(path)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root / DEFAULT_AUDIT):
        raise PublicationError("Inventory/state must remain inside " + DEFAULT_AUDIT)
    return path


def stamp(s):
    return {"size": s.st_size, "mtime_ns": s.st_mtime_ns,
            "mode": stat.S_IMODE(s.st_mode), "device": s.st_dev,
            "inode": s.st_ino}


def collect_entries(root, mappings, hash_contents=True):
    """Only named roots are traversed; symlinks are rejected, never followed."""
    root = Path(root).resolve()
    excluded = root / DEFAULT_AUDIT
    entries = []

    def visit(source, archive):
        source = Path(source)
        if source == excluded or excluded in source.parents or source.name == ".DS_Store":
            return
        safe_archive_name(archive)
        s = source.lstat()
        if stat.S_ISLNK(s.st_mode):
            raise PublicationError("Symlink sources are not supported: " + str(source))
        if stat.S_ISDIR(s.st_mode):
            entries.append({"source": str(source), "archive": archive,
                            "kind": "directory", "mode": stat.S_IMODE(s.st_mode)})
            for child in sorted(source.iterdir(), key=lambda p: p.name):
                visit(child, archive + "/" + child.name)
        elif stat.S_ISREG(s.st_mode):
            entry = {"source": str(source), "archive": archive,
                     "kind": "file", **stamp(s)}
            if hash_contents:
                entry["sha256"] = sha_file(source)
                if stamp(source.stat()) != stamp(s):
                    raise PublicationError("Source changed while freezing: " + str(source))
            entries.append(entry)
        else:
            raise PublicationError("Unsupported source type: " + str(source))

    for mapping in mappings:
        source = Path(mapping["source"]).absolute()
        visit(source, mapping["archive"])
    entries.sort(key=lambda e: e["archive"])
    names = [e["archive"] for e in entries]
    if len(names) != len(set(names)):
        raise PublicationError("Overlapping source mappings or duplicate archive paths")
    kinds = {e["archive"]: e["kind"] for e in entries}
    for name in names:
        if any(kinds.get(str(parent)) == "file" for parent in PurePosixPath(name).parents):
            raise PublicationError("Archive path has a file as parent: " + name)
    return entries


def tar_info(entry):
    info = tarfile.TarInfo(entry["archive"])
    info.mode = entry["mode"]
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    if entry["kind"] == "directory":
        info.type = tarfile.DIRTYPE
        info.size = 0
    else:
        info.size = entry["size"]
    return info


def entry_tar_bytes(entry):
    header = tar_info(entry).tobuf(format=tarfile.PAX_FORMAT, encoding="utf-8")
    size = entry.get("size", 0) if entry["kind"] == "file" else 0
    return len(header) + ((size + 511) // 512) * 512


def complete_tar_bytes(content_bytes):
    # tarfile writes two end blocks and pads the final 10 KiB record.
    record = tarfile.RECORDSIZE
    return ((content_bytes + 1024 + record - 1) // record) * record


def plan_volumes(entries, limit=MAX_VOLUME_BYTES):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 10240 <= limit <= MAX_VOLUME_BYTES:
        raise PublicationError("Volume limit must be an integer from 10240 to 1 GiB")
    volumes = []
    current, content = [], 0
    for index, entry in enumerate(entries):
        size = entry_tar_bytes(entry)
        if complete_tar_bytes(size) > limit:
            raise PublicationError("Single entry cannot fit in one volume: " + entry["archive"])
        if current and complete_tar_bytes(content + size) > limit:
            volumes.append({"entries": current, "size": complete_tar_bytes(content)})
            current, content = [], 0
        current.append(index)
        content += size
    if current:
        volumes.append({"entries": current, "size": complete_tar_bytes(content)})
    if not volumes:
        raise PublicationError("No sources to archive")
    return volumes


class HashWriter:
    def __init__(self, sink=None):
        self.sink = sink
        self.count = 0
        self.hash = hashlib.sha256()

    def write(self, data):
        if self.sink is not None:
            result = self.sink.write(data)
            if result is not None and result != len(data):
                raise PublicationError("Short archive write")
        self.hash.update(data)
        self.count += len(data)
        return len(data)

    def flush(self):
        pass


class HashReader:
    def __init__(self, f):
        self.f = f
        self.hash = hashlib.sha256()

    def read(self, n=-1):
        data = self.f.read(n)
        self.hash.update(data)
        return data


def check_file_stat(entry, s):
    if stamp(s) != {k: entry[k] for k in stamp(s)}:
        raise PublicationError("Frozen source metadata changed: " + entry["source"])


def stream_volume(inventory, volume, sink=None):
    output = HashWriter(sink)
    with tarfile.open(fileobj=output, mode="w|", format=tarfile.PAX_FORMAT,
                      encoding="utf-8", bufsize=tarfile.RECORDSIZE) as archive:
        for index in volume["entries"]:
            entry = inventory["entries"][index]
            info = tar_info(entry)
            if entry["kind"] == "directory":
                if not Path(entry["source"]).is_dir():
                    raise PublicationError("Frozen directory is missing: " + entry["source"])
                archive.addfile(info)
            else:
                with open(entry["source"], "rb") as f:
                    check_file_stat(entry, os.fstat(f.fileno()))
                    reader = HashReader(f)
                    archive.addfile(info, reader)
                    check_file_stat(entry, os.fstat(f.fileno()))
                    if reader.hash.hexdigest() != entry["sha256"]:
                        raise PublicationError("Frozen source content changed: " + entry["source"])
    if output.count != volume["size"]:
        raise PublicationError("Computed TAR length does not match streamed length")
    actual = output.hash.hexdigest()
    if volume.get("sha256") and actual != volume["sha256"]:
        raise PublicationError("Streamed TAR SHA256 differs from frozen volume")
    return actual


def external_mappings(specs=(), manifest=None):
    result = []
    for spec in specs:
        if "=" not in spec:
            raise PublicationError("External mapping must be SOURCE=ARCHIVE_PATH")
        source, archive = spec.split("=", 1)
        result.append({"source": str(Path(source).absolute()),
                       "archive": safe_archive_name(archive)})
    if manifest:
        data = json.loads(Path(manifest).read_text())
        if isinstance(data, dict):
            data = data.get("files")
        if not isinstance(data, list):
            raise PublicationError("External manifest must be a list or an object with files[]")
        for item in data:
            if not isinstance(item, dict) or not item.get("path"):
                raise PublicationError("Each external manifest item needs path")
            source = Path(item["path"]).absolute()
            archive = item.get("archive") or "external-generated-images/" + source.parent.name + "/" + source.name
            result.append({"source": str(source), "archive": safe_archive_name(archive)})
    return result


def freeze(root, inventory_path, limit=MAX_VOLUME_BYTES, external=(), progress=None):
    root = Path(root).resolve()
    inventory_path = audit_path(root, inventory_path)
    mappings = [{"source": str(root / n), "archive": n} for n in ("work", "outputs")]
    mappings.extend(external)
    entries = collect_entries(root, mappings)
    inventory = {"schema": 1, "root": str(root), "sources": mappings,
                 "excluded": [DEFAULT_AUDIT, "**/.DS_Store"],
                 "hardlink_deduplication": False, "volume_limit": limit,
                 "entries": entries, "volumes": plan_volumes(entries, limit)}
    for i, volume in enumerate(inventory["volumes"], 1):
        if progress:
            progress("Freezing volume %s/%s (%s bytes)" % (i, len(inventory["volumes"]), volume["size"]))
        volume["sha256"] = stream_volume(inventory, volume)
        volume["number"] = i
    identity = hashlib.sha256(canonical(inventory)).hexdigest()
    for volume in inventory["volumes"]:
        volume["name"] = "full-project-" + identity[:16] + "-part%04d.tar" % volume["number"]
    inventory["inventory_sha256"] = hashlib.sha256(canonical(inventory)).hexdigest()
    save_json(inventory_path, inventory)
    return inventory


def load_inventory(path):
    data = json.loads(Path(path).read_text())
    expected = data.pop("inventory_sha256", None)
    actual = hashlib.sha256(canonical(data)).hexdigest()
    data["inventory_sha256"] = expected
    if expected != actual or data.get("schema") != 1:
        raise PublicationError("Frozen inventory integrity check failed")
    names = [safe_archive_name(e["archive"]) for e in data["entries"]]
    if len(names) != len(set(names)):
        raise PublicationError("Duplicate frozen archive paths")
    indices = [i for v in data["volumes"] for i in v["entries"]]
    if indices != list(range(len(data["entries"]))):
        raise PublicationError("Frozen volume entry partition is invalid")
    for volume in data["volumes"]:
        size = complete_tar_bytes(sum(entry_tar_bytes(data["entries"][i]) for i in volume["entries"]))
        if size != volume["size"] or size > data["volume_limit"] or size > MAX_VOLUME_BYTES:
            raise PublicationError("Frozen volume length is invalid")
    return data


def verify_sources(inventory):
    current = collect_entries(inventory["root"], inventory["sources"])
    if current != inventory["entries"]:
        raise PublicationError("Frozen source tree or content changed")


def gh_token():
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    token = result.stdout.strip()
    if result.returncode or not token or "\r" in token or "\n" in token:
        raise PublicationError("gh auth token failed; authentication details suppressed")
    return token


class GitHub:
    def __init__(self, repo, release_id, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise PublicationError("Repository must be OWNER/REPO")
        if not isinstance(release_id, int) or release_id <= 0:
            raise PublicationError("Release id must be positive")
        self.repo, self.release_id, self.token = repo, release_id, token

    def headers(self, accept="application/vnd.github+json"):
        return {"Authorization": "Bearer " + self.token,
                "Accept": accept, "User-Agent": "project-stream-release/1",
                "X-GitHub-Api-Version": "2022-11-28"}

    def json_request(self, path):
        connection = http.client.HTTPSConnection("api.github.com", timeout=120)
        try:
            connection.request("GET", path, headers=self.headers())
            response = connection.getresponse()
            if response.status != 200:
                raise PublicationError("GitHub metadata request failed (HTTP %s)" % response.status)
            return json.loads(response.read(4 * CHUNK))
        finally:
            connection.close()

    def assets(self):
        assets = []
        page = 1
        while True:
            batch = self.json_request("/repos/%s/releases/%s/assets?per_page=100&page=%s" %
                                      (self.repo, self.release_id, page))
            if not isinstance(batch, list):
                raise PublicationError("Invalid GitHub asset list")
            assets.extend(batch)
            if len(batch) < 100:
                return assets
            page += 1

    def upload(self, inventory, volume):
        connection = http.client.HTTPSConnection("uploads.github.com", timeout=180)
        path = "/repos/%s/releases/%s/assets?%s" % (self.repo, self.release_id,
                                                  urlencode({"name": volume["name"]}))
        try:
            connection.putrequest("POST", path)
            for key, value in self.headers().items():
                connection.putheader(key, value)
            connection.putheader("Content-Type", "application/x-tar")
            connection.putheader("Content-Length", str(volume["size"]))
            connection.endheaders()

            class Sender:
                def write(self, data):
                    connection.send(data)
                    return len(data)

            stream_volume(inventory, volume, Sender())
            response = connection.getresponse()
            if response.status != 201:
                raise PublicationError("GitHub upload failed (HTTP %s)" % response.status)
            asset = json.loads(response.read(4 * CHUNK))
            if asset.get("name") != volume["name"] or asset.get("size") != volume["size"] or asset.get("state") != "uploaded":
                raise PublicationError("GitHub did not confirm the complete uploaded asset")
            return asset
        finally:
            connection.close()

    def remote_sha(self, asset):
        # API redirects to a signed public asset URL. Never forward the token there.
        url = "https://api.github.com/repos/%s/releases/assets/%s" % (self.repo, asset["id"])
        for _ in range(6):
            u = urlsplit(url)
            host = u.hostname or ""
            if u.scheme != "https" or u.username or u.password or u.port not in (None, 443) or not (
                    host in ("api.github.com", "github.com") or host.endswith(".githubusercontent.com")):
                raise PublicationError("Unsafe asset download redirect")
            headers = {"Accept": "application/octet-stream", "User-Agent": "project-stream-release/1"}
            if host == "api.github.com":
                headers.update(self.headers("application/octet-stream"))
            connection = http.client.HTTPSConnection(host, timeout=180)
            try:
                connection.request("GET", u.path + ("?" + u.query if u.query else ""), headers=headers)
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    url = response.getheader("Location") or ""
                    continue
                if response.status != 200:
                    raise PublicationError("Asset download failed (HTTP %s)" % response.status)
                h, count = hashlib.sha256(), 0
                for data in iter(lambda: response.read(CHUNK), b""):
                    h.update(data)
                    count += len(data)
                if count != asset["size"]:
                    raise PublicationError("Downloaded asset length mismatch")
                return h.hexdigest()
            finally:
                connection.close()
        raise PublicationError("Too many asset download redirects")


def indexed_assets(client):
    result = {}
    for asset in client.assets():
        name = asset["name"]
        if name in result:
            raise PublicationError("Duplicate remote asset name: " + name)
        result[name] = asset
    return result


def check_asset(client, asset, expected):
    if asset.get("state") != "uploaded" or asset.get("size") != expected["size"]:
        raise PublicationError("Remote asset is incomplete or has wrong length: " + expected["name"])
    if client.remote_sha(asset) != expected["sha256"]:
        raise PublicationError("Remote asset SHA256 mismatch: " + expected["name"])


def state_for(inventory, client, state_path):
    state_path = audit_path(inventory["root"], state_path)
    if Path(state_path).exists():
        state = json.loads(Path(state_path).read_text())
        if (state.get("inventory_sha256"), state.get("repo"), state.get("release_id")) != (
                inventory["inventory_sha256"], client.repo, client.release_id):
            raise PublicationError("Existing state belongs to another inventory or Release")
        return state
    return {"inventory_sha256": inventory["inventory_sha256"], "repo": client.repo,
            "release_id": client.release_id, "volumes": {}, "verified": False}


def upload_all(inventory, client, state_path, progress=None):
    state_path = audit_path(inventory["root"], state_path)
    state = state_for(inventory, client, state_path)
    state["verified"] = False
    save_json(state_path, state)
    verify_sources(inventory)
    assets = indexed_assets(client)
    for number, volume in enumerate(inventory["volumes"], 1):
        name = volume["name"]
        try:
            if progress:
                progress("%s volume %s/%s: %s (%s bytes)" % (
                    "Checking existing" if name in assets else "Uploading", number,
                    len(inventory["volumes"]), name, volume["size"]))
            if name in assets:
                asset = assets[name]
                check_asset(client, asset, volume)
                status = "verified_existing"
            else:
                asset = client.upload(inventory, volume)
                status = "uploaded_pending_verification"
            state["volumes"][name] = {"asset_id": asset["id"], "size": volume["size"],
                                      "sha256": volume["sha256"], "status": status}
            save_json(state_path, state)
            if progress:
                progress("Volume %s/%s: %s" % (number, len(inventory["volumes"]), status))
        except Exception:
            state["volumes"][name] = {"status": "failed"}
            save_json(state_path, state)
            raise
    return state


def existing_expectations(inventory, specs):
    by_source = {str(Path(e["source"]).resolve()): e for e in inventory["entries"] if e["kind"] == "file"}
    result = []
    for spec in specs:
        source, sep, name = spec.partition("=")
        path = Path(source).resolve()
        entry = by_source.get(str(path))
        if entry is None:
            raise PublicationError("Existing asset source is not in frozen inventory: " + str(path))
        result.append({"name": name if sep else path.name,
                       "size": entry["size"], "sha256": entry["sha256"]})
    return result


def verify_all(inventory, client, state_path, existing_files=(), progress=None):
    state_path = audit_path(inventory["root"], state_path)
    state = state_for(inventory, client, state_path)
    state["verified"] = False
    save_json(state_path, state)
    try:
        verify_sources(inventory)
        assets = indexed_assets(client)
        expected = inventory["volumes"] + existing_expectations(inventory, existing_files)
        verified = []
        for number, item in enumerate(expected, 1):
            if item["name"] not in assets:
                raise PublicationError("Missing remote asset: " + item["name"])
            if progress:
                progress("Verifying asset %s/%s: %s" % (number, len(expected), item["name"]))
            check_asset(client, assets[item["name"]], item)
            verified.append({"name": item["name"], "asset_id": assets[item["name"]]["id"],
                             "size": item["size"], "sha256": item["sha256"]})
        verify_sources(inventory)
        state["verified_assets"] = verified
        state["verified"] = True
        save_json(state_path, state)
        return state
    except Exception:
        state["verified"] = False
        save_json(state_path, state)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    f = commands.add_parser("freeze")
    f.add_argument("--root", default=".")
    f.add_argument("--inventory", default=DEFAULT_AUDIT + "/frozen-inventory.json")
    f.add_argument("--volume-bytes", type=int, default=MAX_VOLUME_BYTES)
    f.add_argument("--external", action="append", default=[])
    f.add_argument("--external-manifest")
    for name in ("upload", "verify"):
        p = commands.add_parser(name)
        p.add_argument("--inventory", default=DEFAULT_AUDIT + "/frozen-inventory.json")
        p.add_argument("--state", default=DEFAULT_AUDIT + "/release-state.json")
        p.add_argument("--repo", required=True)
        p.add_argument("--release-id", type=int, required=True)
        if name == "verify":
            p.add_argument("--existing-file", action="append", default=[])
    args = parser.parse_args(argv)
    progress = lambda message: print(message, file=sys.stderr, flush=True)
    try:
        if args.command == "freeze":
            inventory = freeze(args.root, args.inventory, args.volume_bytes,
                               external_mappings(args.external, args.external_manifest), progress)
            print(json.dumps({"frozen": True, "entries": len(inventory["entries"]),
                              "volumes": len(inventory["volumes"]),
                              "tar_bytes": sum(v["size"] for v in inventory["volumes"])}, ensure_ascii=False))
        else:
            inventory = load_inventory(args.inventory)
            client = GitHub(args.repo, args.release_id, gh_token())
            if args.command == "upload":
                state = upload_all(inventory, client, args.state, progress)
                print(json.dumps({"uploaded_volumes": len(state["volumes"]),
                                  "verified": False, "next": "verify"}))
            else:
                state = verify_all(inventory, client, args.state, args.existing_file, progress)
                print(json.dumps({"verified": True, "assets": len(state["verified_assets"])}))
        return 0
    except Exception as exc:
        # Do not print HTTP request objects, response bodies, signed URLs or token output.
        message = str(exc) if isinstance(exc, PublicationError) else type(exc).__name__
        print("ERROR: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
