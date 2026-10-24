"""Tests use small temporary fixtures and fake HTTPS; no live GitHub calls."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import stream_release as release


class FakeGitHub:
    repo = "owner/repo"
    release_id = 7

    def __init__(self):
        self.items = []
        self.data = {}
        self.uploaded = []
        self.downloaded = []
        self.fail_upload = False

    def assets(self):
        return list(self.items)

    def add(self, name, body):
        asset = {"id": len(self.items) + 1, "name": name,
                 "size": len(body), "state": "uploaded"}
        self.items.append(asset)
        self.data[asset["id"]] = body
        return asset

    def upload(self, inventory, volume):
        if self.fail_upload:
            raise OSError("simulated failure")
        sink = io.BytesIO()
        release.stream_volume(inventory, volume, sink)
        self.uploaded.append(volume["name"])
        return self.add(volume["name"], sink.getvalue())

    def remote_sha(self, asset):
        self.downloaded.append(asset["name"])
        return hashlib.sha256(self.data[asset["id"]]).hexdigest()


class StreamReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "work").mkdir()
        (self.root / "outputs").mkdir()
        self.audit = self.root / release.DEFAULT_AUDIT
        self.inventory_path = self.audit / "frozen.json"
        self.state_path = self.audit / "state.json"

    def write(self, relative, data=b"example"):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def freeze(self, limit=release.MAX_VOLUME_BYTES, external=()):
        return release.freeze(self.root, self.inventory_path, limit, external)

    def test_unicode_empty_files_directories_deterministic_length(self):
        """输入中文/长PAX名/空文件/空目录 → 两次流式TAR → 字节相同、长度准确、可完整读取。"""
        self.write("work/人物姿势/空文件.png", b"")
        self.write("work/人物姿势/" + "古风人物" * 30 + ".txt", "你好".encode())
        (self.root / "outputs/empty").mkdir()
        inv = self.freeze()
        a, b = io.BytesIO(), io.BytesIO()
        release.stream_volume(inv, inv["volumes"][0], a)
        release.stream_volume(inv, inv["volumes"][0], b)
        self.assertEqual(a.getvalue(), b.getvalue())
        self.assertEqual(len(a.getvalue()), inv["volumes"][0]["size"])
        with tarfile.open(fileobj=io.BytesIO(a.getvalue())) as tar:
            names = tar.getnames()
            self.assertIn("work/人物姿势/空文件.png", names)
            self.assertIn("outputs/empty", names)
            self.assertEqual(tar.extractfile("work/人物姿势/空文件.png").read(), b"")
            self.assertTrue(all(x.uid == x.gid == x.mtime == 0 for x in tar.getmembers()))

    def test_volume_boundary_partition_and_hardlink_contents(self):
        """输入3个5KB文件含硬链接 → 10KiB分卷 → 无遗漏/重复、各卷独立可读且硬链接内容保留。"""
        a = self.write("work/a.bin", b"a" * 5000)
        self.write("work/b.bin", b"b" * 5000)
        os.link(a, self.root / "work/c.bin")
        inv = self.freeze(10240)
        self.assertGreater(len(inv["volumes"]), 1)
        recovered = {}
        for volume in inv["volumes"]:
            data = io.BytesIO()
            release.stream_volume(inv, volume, data)
            self.assertLessEqual(len(data.getvalue()), 10240)
            with tarfile.open(fileobj=io.BytesIO(data.getvalue())) as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        self.assertNotIn(member.name, recovered)
                        recovered[member.name] = tar.extractfile(member).read()
        self.assertEqual(recovered, {"work/a.bin": b"a" * 5000,
                                     "work/b.bin": b"b" * 5000,
                                     "work/c.bin": b"a" * 5000})

    def test_exact_limit_and_one_block_over(self):
        """EXTRA-1 输入单项恰好可放或多1字节 → 规划 → 前者10KiB，后者拒绝而不丢文件。"""
        f = self.write("work/a", b"a" * 8704)
        entries = release.collect_entries(self.root, [{"source": str(f), "archive": "a"}])
        self.assertEqual(release.plan_volumes(entries, 10240)[0]["size"], 10240)
        f.write_bytes(b"a" * 8705)
        entries = release.collect_entries(self.root, [{"source": str(f), "archive": "a"}])
        with self.assertRaisesRegex(release.PublicationError, "cannot fit"):
            release.plan_volumes(entries, 10240)

    def test_invalid_volume_limits(self):
        """EXTRA-2 输入负/零/小于最小/超1GiB/非整数 → 分卷 → 全拒绝。"""
        for value in (-1, 0, 10239, release.MAX_VOLUME_BYTES + 1, 1.5, True):
            with self.subTest(value=value), self.assertRaises(release.PublicationError):
                release.plan_volumes([], value)

    def test_excludes_audit_metadata_and_includes_explicit_external(self):
        """输入审计/DS_Store/明确外部文件 → 冻结 → 前两者排除，外部按映射收入，源不改动。"""
        self.write("work/github-publication-audit/ignored.json", b"ignore")
        self.write("outputs/.DS_Store", b"ignore")
        external = self.write("external-source/image.png", b"original")
        inv = self.freeze(external=[{"source": str(external), "archive": "external-generated-images/x/image.png"}])
        self.assertEqual([e["archive"] for e in inv["entries"]],
                         ["external-generated-images/x/image.png", "outputs", "work"])
        self.assertEqual(external.read_bytes(), b"original")
        release.verify_sources(inv)

    def test_external_manifest_and_unsafe_or_overlapping_paths(self):
        """输入明确外部清单/穿越路径/重叠映射 → 解析冻结 → 合法接受，危险和重叠拒绝。"""
        f = self.write("external-source/image.png")
        manifest = self.root / "external.json"
        manifest.write_text(json.dumps({"files": [{"path": str(f)}]}))
        mapping = release.external_mappings(manifest=manifest)
        self.assertEqual(mapping[0]["archive"], "external-generated-images/external-source/image.png")
        for name in ("../escape", "/absolute", "C:/drive", "a\\b", "a/./b"):
            with self.subTest(name=name), self.assertRaises(release.PublicationError):
                release.safe_archive_name(name)
        with self.assertRaisesRegex(release.PublicationError, "Overlapping"):
            self.freeze(external=[{"source": str(self.root / "work"), "archive": "work"}])

    def test_inventory_digest_and_changed_tree_or_source_detected(self):
        """输入冻结源 → 改内容但恢复mtime/新增文件/篡清单 → 校验 → 每项均失败。"""
        f = self.write("work/a", b"old")
        inv = self.freeze()
        release.load_inventory(self.inventory_path)
        before = f.stat()
        f.write_bytes(b"new")
        os.utime(f, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaises(release.PublicationError):
            release.verify_sources(inv)
        with self.assertRaises(release.PublicationError):
            release.stream_volume(inv, inv["volumes"][0])
        f.write_bytes(b"old")
        os.utime(f, ns=(before.st_atime_ns, before.st_mtime_ns))
        release.verify_sources(inv)
        self.write("outputs/added", b"new")
        with self.assertRaises(release.PublicationError):
            release.verify_sources(inv)
        d = json.loads(self.inventory_path.read_text())
        d["volumes"][0]["size"] += 512
        self.inventory_path.write_text(json.dumps(d))
        with self.assertRaisesRegex(release.PublicationError, "integrity"):
            release.load_inventory(self.inventory_path)

    def test_symlinks_rejected_without_following(self):
        """EXTRA-3 输入指向范围外的符号链接 → 冻结 → 拒绝，不跟随或归档外部数据。"""
        f = self.write("outside", b"private")
        (self.root / "work/link").symlink_to(f)
        with self.assertRaisesRegex(release.PublicationError, "Symlink"):
            self.freeze()

    def test_upload_resume_and_verify_existing_file_asset(self):
        """输入多卷+已有ZIP → 上传后恢复+独立verify → 既有卷SHA正确才跳过，所有资产逐个下载。"""
        self.write("work/a", b"a" * 5000)
        zipfile = self.write("outputs/原名.zip", b"zip" * 1700)
        inv = self.freeze(10240)
        client = FakeGitHub()
        first = release.upload_all(inv, client, self.state_path)
        self.assertFalse(first["verified"])
        self.assertTrue(all(v["status"] == "uploaded_pending_verification" for v in first["volumes"].values()))
        uploaded = len(client.uploaded)
        resumed = release.upload_all(inv, client, self.state_path)
        self.assertEqual(len(client.uploaded), uploaded)
        self.assertTrue(all(v["status"] == "verified_existing" for v in resumed["volumes"].values()))
        client.add("existing-ascii.zip", zipfile.read_bytes())
        verified = release.verify_all(inv, client, self.state_path,
                                      [str(zipfile) + "=existing-ascii.zip"])
        self.assertTrue(verified["verified"])
        self.assertEqual(len(verified["verified_assets"]), len(inv["volumes"]) + 1)
        self.assertIn("existing-ascii.zip", client.downloaded)

    def test_upload_failures_and_corrupt_remote_never_succeed(self):
        """输入网络失败或同长度坏资产 → 上传/恢复 → 抛错且状态failed，不覆盖/删除远端。"""
        self.write("work/a")
        inv = self.freeze()
        client = FakeGitHub()
        client.fail_upload = True
        with self.assertRaises(OSError):
            release.upload_all(inv, client, self.state_path)
        self.assertFalse(json.loads(self.state_path.read_text())["verified"])
        self.assertEqual(json.loads(self.state_path.read_text())["volumes"][inv["volumes"][0]["name"]]["status"], "failed")
        client.fail_upload = False
        client.add(inv["volumes"][0]["name"], b"x" * inv["volumes"][0]["size"])
        with self.assertRaisesRegex(release.PublicationError, "SHA256"):
            release.upload_all(inv, client, self.state_path)
        self.assertEqual(client.uploaded, [])

    def test_verify_missing_asset_source_change_and_stale_success(self):
        """输入曾verify成功状态 → 资产消失/本地变化 → 再verify → verified立即复位为false。"""
        f = self.write("work/a")
        inv = self.freeze()
        client = FakeGitHub()
        release.upload_all(inv, client, self.state_path)
        release.verify_all(inv, client, self.state_path)
        client.items = []
        with self.assertRaisesRegex(release.PublicationError, "Missing"):
            release.verify_all(inv, client, self.state_path)
        self.assertFalse(json.loads(self.state_path.read_text())["verified"])
        f.write_bytes(b"changed")
        with self.assertRaisesRegex(release.PublicationError, "changed"):
            release.verify_all(inv, client, self.state_path)
        self.assertFalse(json.loads(self.state_path.read_text())["verified"])

    def test_https_upload_real_stream_length_and_no_token_logging(self):
        """输入小清单+模拟HTTPS → 真上传方法 → Content-Length=实际TAR、无chunked、token不入日志。"""
        self.write("work/中文", b"content")
        inv = self.freeze()
        volume = inv["volumes"][0]
        token = "test-secret-do-not-print"
        captured = {}

        class Connection:
            def __init__(self, host, timeout):
                captured["host"], self.body, self.headers = host, bytearray(), {}
            def putrequest(self, method, path):
                captured["method"], captured["path"] = method, path
            def putheader(self, key, value):
                self.headers[key] = value
            def endheaders(self):
                pass
            def send(self, data):
                self.body.extend(data)
            def getresponse(self):
                captured["body"], captured["headers"] = bytes(self.body), self.headers
                response = type("Response", (), {})()
                response.status = 201
                response.read = lambda n: json.dumps({"name": volume["name"], "id": 1,
                                                       "size": len(self.body), "state": "uploaded"}).encode()
                return response
            def close(self):
                pass

        console = io.StringIO()
        with patch.object(release.http.client, "HTTPSConnection", Connection), contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
            release.GitHub("owner/repo", 7, token).upload(inv, volume)
        self.assertEqual(captured["host"], "uploads.github.com")
        self.assertEqual(int(captured["headers"]["Content-Length"]), len(captured["body"]))
        self.assertEqual(len(captured["body"]), volume["size"])
        self.assertNotIn("Transfer-Encoding", captured["headers"])
        self.assertEqual(hashlib.sha256(captured["body"]).hexdigest(), volume["sha256"])
        self.assertNotIn(token, console.getvalue())

    def test_redirect_does_not_forward_credentials(self):
        """EXTRA-4 输入GitHub API到签名资产域302 → 流式GET → 第二跳不带Authorization。"""
        requests = []
        body = b"asset"

        class Connection:
            def __init__(self, host, timeout):
                self.host = host
            def request(self, method, path, headers):
                requests.append((self.host, headers))
            def getresponse(self):
                response = type("Response", (), {})()
                response.status = 302 if self.host == "api.github.com" else 200
                response.getheader = lambda key: "https://release-assets.githubusercontent.com/path?signed=secret"
                response.read = io.BytesIO(body).read
                return response
            def close(self):
                pass

        with patch.object(release.http.client, "HTTPSConnection", Connection):
            actual = release.GitHub("owner/repo", 7, "token").remote_sha({"id": 1, "size": len(body)})
        self.assertEqual(actual, hashlib.sha256(body).hexdigest())
        self.assertIn("Authorization", requests[0][1])
        self.assertNotIn("Authorization", requests[1][1])

    def test_auth_failure_and_invalid_repo_without_secrets(self):
        """EXTRA-5 输入gh失败并返回敏感stderr → 取token → 通用错误，不暴露stderr/凭据。"""
        result = subprocess.CompletedProcess([], 1, "", "super-secret")
        with patch.object(release.subprocess, "run", return_value=result):
            with self.assertRaises(release.PublicationError) as error:
                release.gh_token()
            self.assertNotIn("super-secret", str(error.exception))
        with self.assertRaises(release.PublicationError):
            release.GitHub("bad/repo/escape", 7, "token")

    def test_upload_changed_source_clears_old_verified_status(self):
        """EXTRA-6 输入旧verified=true后源改变 → 再upload → 拒绝且落盘verified=false。"""
        f = self.write("work/a")
        inv = self.freeze()
        client = FakeGitHub()
        release.upload_all(inv, client, self.state_path)
        release.verify_all(inv, client, self.state_path)
        f.write_bytes(b"changed")
        with self.assertRaises(release.PublicationError):
            release.upload_all(inv, client, self.state_path)
        self.assertFalse(json.loads(self.state_path.read_text())["verified"])

    def test_writing_state_outside_audit_rejected(self):
        """EXTRA-7 输入将清单或状态写到原文件的路径 → freeze/upload → 拒绝且原字节不变。"""
        f = self.write("outputs/keep.bin", b"keep")
        with self.assertRaisesRegex(release.PublicationError, "inside"):
            release.freeze(self.root, f)
        inv = self.freeze()
        with self.assertRaisesRegex(release.PublicationError, "inside"):
            release.upload_all(inv, FakeGitHub(), f)
        self.assertEqual(f.read_bytes(), b"keep")
        self.assertEqual(list(self.root.rglob("*.tar")), [])

    def test_https_error_or_false_completion_rejected(self):
        """EXTRA-8 输入HTTP失败或201但wrong-size/starter → 真upload → 全部拒绝。"""
        self.write("work/a")
        inv = self.freeze()
        volume = inv["volumes"][0]
        for code, size, state in ((502, volume["size"], "uploaded"),
                                  (201, 1, "uploaded"),
                                  (201, volume["size"], "starter")):
            class Connection:
                def __init__(self, host, timeout):pass
                def putrequest(self, *a):pass
                def putheader(self, *a):pass
                def endheaders(self):pass
                def send(self, *a):pass
                def getresponse(self):
                    r = type("Response", (), {})()
                    r.status = code
                    r.read = lambda n: json.dumps({"id": 1, "name": volume["name"],
                                                    "size": size, "state": state}).encode()
                    return r
                def close(self):pass
            with self.subTest(code=code, size=size, state=state), patch.object(release.http.client, "HTTPSConnection", Connection):
                with self.assertRaises(release.PublicationError):
                    release.GitHub("owner/repo", 7, "token").upload(inv, volume)


if __name__ == "__main__":
    unittest.main(verbosity=2)
