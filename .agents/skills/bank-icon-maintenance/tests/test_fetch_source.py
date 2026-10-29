"""HTTPS mechanics with controlled transfers and real loopback TLS; no bank research."""
from session_fixture import make_session
import argparse
from contextlib import redirect_stdout
import copy
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_source as fs


class SourceFetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("openssl") or not shutil.which("curl"):
            raise unittest.SkipTest("openssl/curl unavailable for local HTTPS fixtures")
        cls.temp = tempfile.TemporaryDirectory(prefix="test-source-ca-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.certs = Path(cls.temp.name)
        cls.key = cls.certs / "key.pem"
        cls.pem = cls.certs / fs.CERT_NAMES[0]
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(cls.key), "-out", str(cls.pem), "-days", "1",
                        "-subj", "/CN=local-source-test", "-addext", "subjectAltName=DNS:localhost",
                        "-addext", "basicConstraints=critical,CA:TRUE"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        for name in fs.CERT_NAMES[1:]:
            (cls.certs / name).write_bytes(cls.pem.read_bytes())

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="source-fetch-")
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name).resolve()
        (self.session / "session.json").write_text('{"fixture": true}\n')
        self.args = argparse.Namespace(session=str(self.session), id=10, url="https://bank.example/icon.svg",
                                      output="evidence/10/fetch", curl=shutil.which("curl"))
        self.calls = []
        self.steps = []
        for patcher in (patch.object(fs, "CERT_DIR", self.certs),
                        patch.object(fs, "_session_context", return_value={"baseCommit": "a" * 40})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fixture(self, command, directory):
        self.calls.append(command)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            (directory / "stderr.txt").write_bytes(b"fixture process failed")
            raise step
        code, status, diagnostic, body, headers = step
        (directory / "stderr.txt").write_text(diagnostic)
        (directory / "body.bin").write_bytes(body)
        (directory / "headers.txt").write_text(headers or f"HTTP/1.1 {status} Test\r\n\r\n")
        result = f"{status}\nimage/svg+xml\n"
        (directory / "transfer.txt").write_text(result)
        return code, result

    def fetch(self, steps):
        self.steps = list(steps)
        with patch.object(fs, "_run", side_effect=self.fixture):
            result = fs.fetch(self.args)
        self.assertFalse(list(self.session.glob(".https-ca-*")))
        self.assertEqual((self.session / "session.json").read_text(), '{"fixture": true}\n')
        return result

    def ok(self, body=b"<svg/>"):
        return (0, 200, "", body, "")

    def bad_ca(self, message="SSL certificate problem: unable to get local issuer certificate"):
        return (60, 0, message, b"", "")

    def test_default_success_has_no_ca_or_browser_dependency(self):
        result = self.fetch([self.ok()])
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["caRetry"], "not-needed")
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("--cacert", self.calls[0])
        self.assertEqual(json.loads(Path(result["receipt"]).read_text())["attempts"], result["attempts"])

    def test_issuer_failure_retries_same_url_with_only_packaged_certificates(self):
        result = self.fetch([self.bad_ca(), self.ok()])
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["caRetry"], "used")
        self.assertEqual([row["url"] for row in result["attempts"]], [self.args.url] * 2)
        self.assertEqual([row["trust"] for row in result["attempts"]], ["default", "packaged-ca"])
        self.assertEqual([row["name"] for row in result["ca"]["certificates"]], list(fs.CERT_NAMES))
        self.assertIn("local issuer", result["attempts"][0]["diagnostic"])
        path = Path(self.calls[1][self.calls[1].index("--cacert") + 1])
        self.assertFalse(path.exists())

    def test_failed_ca_retry_is_saved_once_and_never_insecure(self):
        result = self.fetch([self.bad_ca(), self.bad_ca()])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.calls), 2)
        for command in self.calls:
            self.assertEqual(command[1], "--disable")
            for option in ("-k", "--insecure", "--proxy-insecure", "--location", "--retry"):
                self.assertNotIn(option, command)
            self.assertIn("--max-time", command)
            self.assertIn("--max-filesize", command)

    def test_expiry_hostname_network_and_http_failures_do_not_widen_trust(self):
        failures = [self.bad_ca("SSL certificate problem: certificate has expired"),
                    self.bad_ca("no alternative certificate subject name matches"),
                    self.bad_ca("unable to get local issuer certificate; certificate expired"),
                    (6, 0, "Could not resolve host", b"", ""),
                    (28, 0, "Connection timed out", b"", ""),
                    (0, 403, "", b"Access denied", ""), (0, 429, "", b"Slow down", "")]
        for i, failure in enumerate(failures):
            with self.subTest(failure=failure[:3]):
                self.args.output = f"evidence/10/failure-{i}"
                result = self.fetch([failure])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(len(result["attempts"]), 1)
                self.assertNotIn("ca", result)

    def test_gate_page_is_saved_not_claimed_as_logo_or_currentness(self):
        result = self.fetch([self.bad_ca(), self.ok(b"<html>JavaScript challenge</html>")])
        self.assertEqual(result["status"], "fetched")
        self.assertNotIn("verified", result)
        self.assertNotIn("lastVerifiedAt", result)

    def test_redirect_starts_with_normal_trust_after_ca_attempt(self):
        redirect = (0, 302, "", b"", "HTTP/1.1 302 Found\r\nLocation: https://cdn.example/x.svg\r\n\r\n")
        result = self.fetch([self.bad_ca(), redirect, self.ok()])
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["finalUrl"], "https://cdn.example/x.svg")
        self.assertNotIn("--cacert", self.calls[2])
        self.assertEqual(result["attempts"][1]["url"], self.args.url)

    def test_only_one_ca_retry_across_redirect_chain(self):
        redirect = (0, 302, "", b"", "HTTP/1.1 302 Found\r\nLocation: https://cdn.example/x.svg\r\n\r\n")
        result = self.fetch([self.bad_ca(), redirect, self.bad_ca()])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(sum("--cacert" in command for command in self.calls), 1)

    def test_downgrade_credentials_loop_and_ambiguous_redirects_stop(self):
        for i, location in enumerate(("http://bank.example/x", "https://user:pass@bank.example/", self.args.url,
                                     "https://ok.example/x\r\nLocation: https://other.example/")):
            with self.subTest(location=location):
                self.args.output = f"evidence/10/redirect-{i}"
                result = self.fetch([(0, 302, "", b"", f"HTTP/1.1 302 Found\r\nLocation: {location}\r\n\r\n")])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(len(result["attempts"]), 1)

    def test_redirect_limit_is_finite(self):
        result = self.fetch([(0, 302, "", b"", f"HTTP/1.1 302 Found\r\nLocation: /{i}\r\n\r\n")
                             for i in range(4)])
        self.assertEqual(result["reason"], "redirect limit reached")
        self.assertEqual(len(self.calls), 4)

    def test_missing_certs_are_a_retry_limitation_not_a_silent_success(self):
        with patch.object(fs, "CERT_DIR", self.session / "absent"):
            result = self.fetch([self.bad_ca()])
        self.assertEqual(result["caRetry"], "unavailable")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.calls), 1)

    def test_timeout_and_interruption_keep_attempts_and_clean_ca_bundle(self):
        for i, exception in enumerate((subprocess.TimeoutExpired("curl", 35), InterruptedError("test"))):
            self.args.output = f"evidence/10/interrupted-{i}"
            result = self.fetch([self.bad_ca(), exception])
            self.assertEqual(result["status"], "failed")
            self.assertIsNotNone(result["attempts"][-1].get("failure"))
            self.assertTrue(Path(result["receipt"]).exists())

    def test_existing_output_and_symlinks_are_never_replaced(self):
        result = self.fetch([self.ok()])
        previous = Path(result["receipt"]).read_bytes()
        with self.assertRaises(FileExistsError):
            self.fetch([])
        self.assertEqual(Path(result["receipt"]).read_bytes(), previous)
        alias = self.session / "evidence/10/alias"
        alias.symlink_to(Path(result["output"]), target_is_directory=True)
        self.args.output = "evidence/10/alias/new"
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.fetch([])

    def test_path_escape_wrong_id_and_unsafe_url_are_rejected_before_network(self):
        for output in ("/tmp/x", "evidence/20/wrong", "evidence/10/../x", "evidence/10//x"):
            self.args.output = output
            with self.assertRaises(ValueError):
                self.fetch([])
        self.args.output = "evidence/10/ok"
        for url in ("http://bank.example", "file:///tmp/a", "https://x/#fragment",
                    "https://user:pass@x/", "https://x/\nabc"):
            self.args.url = url
            with self.assertRaises(ValueError):
                self.fetch([])
        self.assertEqual(self.calls, [])

    def test_json_cli_failure_and_no_installer(self):
        self.args.curl = str(self.session / "missing-curl")
        result = fs.fetch(self.args)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse((self.session / self.args.output).exists())
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = fs.main(["--bad-option"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stream.getvalue())["status"], "blocked")

    def test_process_timeout_terminates_children_holding_output_pipes(self):
        directory = self.session / "process"
        directory.mkdir()
        command = [sys.executable, "-c", "import subprocess,sys,time; "
                   "subprocess.Popen([sys.executable,'-c','import time; time.sleep(12)']); "
                   "time.sleep(12)"]
        started = time.monotonic()
        with patch.object(fs, "TIMEOUT", 0.3), self.assertRaises(subprocess.TimeoutExpired):
            fs._run(command, directory)
        self.assertLess(time.monotonic() - started, 5)

    def test_real_loopback_tls_uses_ca_and_keeps_hostname_verification(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                content = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            def log_message(self, *args):
                pass
        class Server(http.server.ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                # Rejected TLS handshakes intentionally reset the loopback socket.
                pass
        server = Server(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.pem), str(self.key))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.args.url = f"https://localhost:{server.server_port}/logo.svg"
        with patch.dict(os.environ, {"NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1"}):
            result = fs.fetch(self.args)
            self.assertEqual(result["status"], "fetched", result)
            self.assertEqual(result["caRetry"], "used")
            self.assertEqual([a["exitCode"] for a in result["attempts"]], [60, 0])
            self.args.url = f"https://127.0.0.1:{server.server_port}/logo.svg"
            self.args.output = "evidence/10/wrong-host"
            result = fs.fetch(self.args)
            self.assertEqual(result["status"], "failed", result)
            self.assertNotEqual(result["attempts"][-1]["exitCode"], 0)
        self.assertFalse(list(self.session.glob(".https-ca-*")))


class FullCheckoutIntegration(unittest.TestCase):
    @unittest.skipUnless((ROOT / "scripts/state_results.py").is_file(), "complete checkout required")
    def test_real_session_reader_allows_research_but_rejects_paused_session(self):
        from test_attempts import AttemptCLI
        fixture = AttemptCLI()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        started = make_session(fixture)
        data = fs._session_context(fixture.session, 10)
        self.assertEqual(data["baseCommit"], started["baseCommit"])
        fixture.cli("pause")
        with self.assertRaises(ValueError):
            fs._session_context(fixture.session, 10)

    @unittest.skipUnless((ROOT / "scripts/evidence.py").is_file(), "complete checkout required")
    def test_historical_period_confirmation_preserves_all_positive_gates(self):
        from evidence import assessment_of
        value = {aspect: {"finding": "confirmed", "basis": "Fixture: same bank's final evidenced mark in 2020; historical record."}
                 for aspect in ("identity", "currency", "variant")}
        value.update(independenceRationale="Fixture archival primary and independent sources.", uncertainties=[])
        assessment_of(value, "unchanged")
        for aspect in ("identity", "currency", "variant"):
            uncertain = copy.deepcopy(value)
            uncertain[aspect]["finding"] = "uncertain"
            with self.assertRaises(ValueError):
                assessment_of(uncertain, "unchanged")
        value["uncertainties"] = ["Cannot establish the relevant final historical variant."]
        with self.assertRaises(ValueError):
            assessment_of(value, "unchanged")


if __name__ == "__main__":
    unittest.main()
