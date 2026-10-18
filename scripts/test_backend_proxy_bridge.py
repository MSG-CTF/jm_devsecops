import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import uuid

from backend_proxy_bridge import Bridge, BridgeError, Docker


CONFIG = b"""server {
 listen 80;
 location = /healthz { proxy_pass http://backend:8000/healthz; }
 location /api/v1/ { proxy_pass http://backend:8000/api/v1/; }
 location = /internal/v1/runtime-secrets/resolve {
  proxy_pass http://backend:8000/internal/v1/runtime-secrets/resolve;
  allow 34.9.255.41; deny all;
 }
}
"""


class FakeDocker:
    def __init__(self):
        self.config = CONFIG
        self.frontend = "f" * 64
        self.available = {
            "candidate": ("c" * 64, "172.18.0.3"),
            "primary": ("p" * 64, "172.18.0.4"),
        }
        self.broken = set()
        self.generation = 1
        self.stuck = False
        self.reload_fails = False
        self.reloads = 0

    def identity(self, name):
        if name in ["frontend", self.frontend]:
            return self.frontend, "172.18.0.2"
        for alias, value in self.available.items():
            if name in [alias, value[0]]:
                return value
        raise BridgeError("missing container")

    def ready(self, address):
        if address in self.broken:
            raise BridgeError("failed health")

    def read_config(self, _):
        return self.config

    def write_config(self, _, config):
        self.config = config

    def workers(self, _):
        return {(str(self.generation), "worker-start")}

    def reload(self, _):
        self.reloads += 1
        if self.reload_fails:
            raise BridgeError("reload failed")
        if not self.stuck:
            self.generation += 1


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.docker = FakeDocker()
        self.bridge = Bridge(
            Path(self.directory.name), self.docker, wait=0, sleep=lambda _: None
        )

    def test_verified_candidate_serves_until_primary_ready_and_workers_drained(self):
        self.bridge.begin("candidate", "frontend")
        self.assertIn(b"http://172.18.0.3:8000", self.docker.config)
        self.assertNotIn(b"http://backend:8000", self.docker.config)
        self.assertIn(b"allow 34.9.255.41; deny all;", self.docker.config)
        self.assertEqual(json.loads(self.bridge.state.read_text())["phase"], "serving")
        self.bridge.finish("primary")
        self.assertEqual(self.docker.config, CONFIG)
        self.assertFalse(self.bridge.state.exists())
        self.assertFalse(self.bridge.backup.exists())
        self.assertEqual(self.docker.reloads, 2)

    def test_failed_candidate_leaves_primary_configuration_untouched(self):
        self.docker.broken.add("172.18.0.3")
        with self.assertRaises(BridgeError):
            self.bridge.begin("candidate", "frontend")
        self.assertFalse(self.bridge.state.exists())
        self.assertEqual(self.docker.config, CONFIG)
        self.assertEqual(self.docker.reloads, 0)

    def test_failed_primary_preserves_candidate_and_recovery_state(self):
        self.bridge.begin("candidate", "frontend")
        serving = self.docker.config
        self.docker.broken.add("172.18.0.4")
        with self.assertRaises(BridgeError):
            self.bridge.finish("primary")
        self.assertEqual(self.docker.config, serving)
        self.assertTrue(self.bridge.state.exists())
        self.docker.broken.clear()
        self.bridge.finish("primary")
        self.assertFalse(self.bridge.state.exists())

    def test_partial_finish_is_repointed_to_candidate_before_rollback(self):
        self.bridge.begin("candidate", "frontend")
        self.docker.stuck = True
        with self.assertRaises(BridgeError):
            self.bridge.finish("primary")
        self.assertEqual(self.docker.config, CONFIG)
        self.assertTrue(self.bridge.state.exists())
        self.docker.stuck = False
        self.bridge.hold()
        self.assertIn(b"http://172.18.0.3:8000", self.docker.config)
        self.bridge.finish("primary")

    def test_ambiguous_reload_keeps_candidate_and_backup(self):
        self.docker.reload_fails = True
        with self.assertRaises(BridgeError):
            self.bridge.begin("candidate", "frontend")
        self.assertTrue(self.bridge.state.exists())
        self.assertEqual(self.bridge.backup.read_bytes(), CONFIG)

    def test_worker_drain_timeout_cannot_authorize_container_replacement(self):
        self.docker.stuck = True
        with self.assertRaises(BridgeError):
            self.bridge.begin("candidate", "frontend")
        self.assertNotEqual(
            json.loads(self.bridge.state.read_text())["phase"], "serving"
        )

    def test_recreated_frontend_fails_closed(self):
        self.bridge.begin("candidate", "frontend")
        self.docker.frontend = "e" * 64
        with self.assertRaises(BridgeError):
            self.bridge.finish("primary")
        self.assertTrue(self.bridge.state.exists())

    def test_existing_bridge_cannot_be_overwritten(self):
        self.bridge.begin("candidate", "frontend")
        original = self.bridge.state.read_bytes()
        with self.assertRaises(BridgeError):
            self.bridge.begin("candidate", "frontend")
        self.assertEqual(self.bridge.state.read_bytes(), original)

    def test_unknown_routing_layout_is_rejected_before_reload(self):
        self.docker.config = (
            b"server { location / { proxy_pass http://unknown:8080; } }"
        )
        with self.assertRaises(BridgeError):
            self.bridge.begin("candidate", "frontend")
        self.assertEqual(self.docker.reloads, 0)


class ContractTests(unittest.TestCase):
    def test_real_http_requires_health_components_json_and_auth_boundary(self):
        settings = {
            "health": {"status": "ok", "database": "ok", "cache": "ok"},
            "auth": 401,
            "html": False,
            "redirect": False,
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (
                    settings["health"]
                    if self.path == "/healthz"
                    else {"code": "AUTH_REQUIRED"}
                )
                code = 200 if self.path == "/healthz" else settings["auth"]
                self.send_response(302 if settings["redirect"] else code)
                self.send_header(
                    "Content-Type",
                    "text/html" if settings["html"] else "application/json",
                )
                if settings["redirect"]:
                    self.send_header("Location", "/login")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                docker = Docker("msgctf.kr", port=server.server_port)
                docker.ready("127.0.0.1")
                for key, bad in [
                    ("html", True),
                    ("auth", 200),
                    ("redirect", True),
                    ("health", {"status": "ok", "database": "ok", "cache": "error"}),
                ]:
                    with self.subTest(key=key):
                        before = settings[key]
                        settings[key] = bad
                        with self.assertRaises(BridgeError):
                            docker.ready("127.0.0.1")
                        settings[key] = before
            finally:
                server.shutdown()
                worker.join(timeout=3)

    def test_worker_generation_includes_start_time_and_shutting_down_workers(self):
        docker = Docker("msgctf.kr")
        sample = b"PID STARTED COMMAND\n12 Sun Oct 18 16:00:00 2026 nginx: worker process\n13 Sun Oct 18 16:00:00 2026 nginx: worker process is shutting down\n1 Sun Oct 18 15:00:00 2026 nginx: master process nginx\n"
        with patch.object(docker, "run", return_value=sample):
            self.assertEqual(len(docker.workers("frontend")), 2)


@unittest.skipUnless(
    os.environ.get("MSG_BRIDGE_DOCKER_TESTS") == "1", "explicit isolated Docker fixture"
)
class RealDockerTests(unittest.TestCase):
    def test_real_nginx_serves_candidate_during_primary_recreation_and_rollback(self):
        suffix = uuid.uuid4().hex[:12]
        prefix = "msg-bridge-test-" + suffix
        containers = []
        nginx_image = "nginxinc/nginx-unprivileged:1.31-alpine@sha256:aa8c9087d36d93e9d650c5365f883b421e8214aedbad24ade52b844c583358f1"
        python_image = "python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31"

        def run(*args):
            return (
                subprocess.check_output(["docker", *args], stderr=subprocess.PIPE)
                .decode()
                .strip()
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "default.conf"
            config.write_bytes(CONFIG)
            app = root / "app.py"
            app.write_text(
                """import json,os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200 if self.path=='/healthz' else 401)
  self.send_header('Content-Type','application/json'); self.end_headers()
  self.wfile.write(json.dumps({'status':'ok','database':'ok','cache':'ok','version':os.environ['VERSION']}).encode())
 def log_message(self,*args):pass
ThreadingHTTPServer(('0.0.0.0',8000),Handler).serve_forever()
"""
            )
            run("network", "create", prefix)

            def backend(name, version, primary=False):
                containers.append(name)
                aliases = ["--network-alias", "backend"] if primary else []
                run(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    prefix,
                    *aliases,
                    "--memory",
                    "64m",
                    "--cpus",
                    "0.25",
                    "--user",
                    "65534:65534",
                    "-e",
                    "VERSION=" + version,
                    "-v",
                    str(app) + ":/app.py:ro",
                    python_image,
                    "python",
                    "/app.py"
                )

            try:
                # Explicit traversal permissions for nonroot fixture containers
                root.chmod(0o755)
                app.chmod(0o644)
                config.chmod(0o644)
                primary = prefix + "-primary"
                candidate = prefix + "-candidate"
                frontend = prefix + "-frontend"
                backend(primary, "old", primary=True)
                backend(candidate, "candidate")
                containers.append(frontend)
                run(
                    "run",
                    "-d",
                    "--name",
                    frontend,
                    "--network",
                    prefix,
                    "--memory",
                    "64m",
                    "--cpus",
                    "0.25",
                    "--user",
                    "0",
                    "-v",
                    str(config) + ":/etc/nginx/conf.d/default.conf",
                    "-p",
                    "127.0.0.1::80",
                    nginx_image,
                )
                docker = Docker("msgctf.kr", network=prefix)
                bridge = Bridge(root / "state", docker, wait=15)
                bridge.ready(primary)
                # Nginx may still be starting while the backend is ready
                for _ in range(50):
                    if docker.workers(frontend):
                        break
                    time.sleep(0.1)
                frontend_id, frontend_ip = docker.identity(frontend)
                stop = threading.Event()
                observations = []

                def observe():
                    import urllib.request

                    while not stop.is_set():
                        try:
                            request = urllib.request.Request(
                                "http://" + frontend_ip + "/healthz",
                                headers={"Host": "msgctf.kr"},
                            )
                            with urllib.request.urlopen(request, timeout=2) as response:
                                observations.append(
                                    (
                                        response.code,
                                        json.loads(response.read())["version"],
                                    )
                                )
                        except Exception as error:
                            observations.append((type(error).__name__, None))
                        stop.wait(0.025)

                watcher = threading.Thread(target=observe, daemon=True)
                watcher.start()
                try:
                    bridge.begin(candidate, frontend_id)
                    run("rm", "-f", primary)
                    time.sleep(0.5)
                    backend(primary, "new", primary=True)
                    bridge.finish(primary)
                    time.sleep(0.15)
                    # Re-enter the bridge before simulating a failed new primary
                    bridge.begin(candidate, frontend_id)
                    run("rm", "-f", primary)
                    with self.assertRaises(BridgeError):
                        Bridge(root / "state", docker, wait=0).finish(primary)
                    self.assertTrue(bridge.state.exists())
                    bridge.hold()
                    backend(primary, "rollback", primary=True)
                    bridge.finish(primary)
                    time.sleep(0.15)
                finally:
                    stop.set()
                    watcher.join(timeout=3)
                self.assertGreater(len(observations), 20)
                self.assertTrue(
                    all(code == 200 for code, _ in observations), observations
                )
                self.assertTrue(
                    {"candidate", "new", "rollback"}.issubset(
                        {v for _, v in observations}
                    ),
                    observations,
                )
                self.assertFalse(bridge.state.exists())
            finally:
                for name in reversed(list(dict.fromkeys(containers))):
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
                subprocess.run(["docker", "network", "rm", prefix], capture_output=True)


if __name__ == "__main__":
    unittest.main()
