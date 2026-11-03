#!/usr/bin/env python3
"""Keep a verified backend serving while its Compose container is replaced."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


class BridgeError(Exception):
    pass


class Docker:
    def __init__(self, domain, network="msg-dev_default", port=8000):
        self.domain = domain
        self.network, self.port = network, port

    def run(self, *args, data=None):
        result = subprocess.run(
            ["docker", *args], input=data, capture_output=True, timeout=30
        )
        if result.returncode:
            # Container logs, configurations and command environments stay private
            raise BridgeError("Docker bridge operation failed")
        return result.stdout

    def identity(self, name):
        result = json.loads(self.run("inspect", name))[0]
        if not result["State"]["Running"]:
            raise BridgeError("Required container is not running")
        network = result["NetworkSettings"]["Networks"].get(self.network, {})
        address = network.get("IPAddress", "")
        if not ipaddress.ip_address(address).is_private:
            raise BridgeError("Expected a private Compose network address")
        return result["Id"], address

    def read_config(self, frontend):
        return self.run("exec", frontend, "cat", "/etc/nginx/conf.d/default.conf")

    def write_config(self, frontend, config):
        self.run(
            "exec",
            "--user",
            "0",
            "-i",
            frontend,
            "sh",
            "-c",
            "cat > /etc/nginx/conf.d/default.conf",
            data=config,
        )
        self.run("exec", frontend, "nginx", "-t")

    def workers(self, frontend):
        # Preserve PID and start time so PID reuse cannot masquerade as a survivor
        text = self.run("top", frontend, "-eo", "pid,lstart,args").decode()
        return {
            tuple(line.split()[:6])
            for line in text.splitlines()
            if "nginx: worker process" in line
        }

    def reload(self, frontend):
        self.run("exec", frontend, "nginx", "-s", "reload")

    def ready(self, address):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )
        for path, expected in [("/healthz", 200), ("/api/v1/auth/me", 401)]:
            request = urllib.request.Request(
                "http://" + address + ":" + str(self.port) + path,
                headers={
                    "Host": self.domain,
                    "X-Forwarded-Proto": "https",
                    "Accept": "application/json",
                },
            )
            try:
                response = opener.open(request, timeout=3)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                if response.code != expected:
                    raise BridgeError("Backend contract returned an unexpected status")
                if response.headers.get_content_type() != "application/json":
                    raise BridgeError("Backend contract must return JSON")
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise BridgeError("Backend contract response exceeds limit")
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise BridgeError("Backend contract must return an object")
                if path == "/healthz" and any(
                    body.get(key) != "ok" for key in ["status", "database", "cache"]
                ):
                    raise BridgeError("Backend database or Redis is not ready")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def candidate_config(config, address):
    """Route only the known backend upstream, retaining URI and auth directives."""
    literal = rb"(\bproxy_pass\s+)http://backend:8000(?=[/;])"
    variable = rb'(?m)^(\s*set\s+\$backend_url\s+)"http://backend:8000"(\s*;\s*)$'
    dynamic_proxy = rb"\bproxy_pass\s+\$backend_url\$request_uri\s*;"
    literal_count = len(re.findall(literal, config))
    variable_count = len(re.findall(variable, config))
    definitions = len(re.findall(rb"\bset\s+\$backend_url\s", config))
    dynamic_count = len(re.findall(dynamic_proxy, config))
    if b"location = /healthz" not in config:
        raise BridgeError("Expected the backend API and health proxy routes")
    target = b"http://" + address.encode() + b":8000"
    if literal_count >= 2 and variable_count == dynamic_count == 0:
        return re.sub(literal, lambda match: match[1] + target, config)
    if variable_count == definitions == 1 and dynamic_count >= 2 and literal_count == 0:
        return re.sub(
            variable, lambda match: match[1] + b'"' + target + b'"' + match[2], config
        )
    raise BridgeError("Expected the backend API and health proxy routes")


class Bridge:
    def __init__(self, state_dir, docker, wait=75, sleep=time.sleep):
        self.state_dir = Path(state_dir)
        self.state = self.state_dir / "backend-bridge.json"
        self.backup = self.state_dir / "backend-bridge.nginx.conf"
        self.docker, self.wait, self.sleep = docker, wait, sleep

    def save(self, value):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.state_dir, prefix=".bridge-")
        try:
            with os.fdopen(descriptor, "w") as file:
                json.dump(value, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.state)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def load(self):
        value = json.loads(self.state.read_text())
        if self.docker.identity(value["frontend"])[0] != value["frontend"]:
            raise BridgeError("Frontend container changed during backend deployment")
        return value

    def ready(self, container):
        deadline = time.monotonic() + self.wait
        while True:
            try:
                _, address = self.docker.identity(container)
                self.docker.ready(address)
                return address
            except (BridgeError, OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise BridgeError(
                        "Backend did not pass database, Redis and API verification"
                    ) from None
                self.sleep(0.25)

    def route(self, value, config):
        frontend = value["frontend"]
        previous = self.docker.workers(frontend)
        if not previous:
            raise BridgeError("Cannot establish the current Nginx worker set")
        self.docker.write_config(frontend, config)
        self.docker.reload(frontend)
        deadline = time.monotonic() + self.wait
        while True:
            current = self.docker.workers(frontend)
            if current and not previous.intersection(current):
                return
            if time.monotonic() >= deadline:
                raise BridgeError(
                    "Nginx workers did not drain; keep both backends running"
                )
            self.sleep(0.25)

    def begin(self, candidate, frontend):
        if self.state.exists() or self.backup.exists():
            raise BridgeError("An unfinished backend bridge needs operator recovery")
        candidate_id, address = self.docker.identity(candidate)
        frontend_id, _ = self.docker.identity(frontend)
        self.ready(candidate_id)
        config = self.docker.read_config(frontend_id)
        candidate_config(config, address)
        self.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self.backup.open("xb") as file:
            file.write(config)
        value = {
            "frontend": frontend_id,
            "candidate": candidate_id,
            "candidate_name": candidate,
            "phase": "routing",
        }
        # Record before any proxy mutation; cleanup must retain this candidate on failure
        self.save(value)
        self.hold()

    def hold(self):
        value = self.load()
        address = self.ready(value["candidate"])
        config = self.backup.read_bytes()
        routed = candidate_config(config, address)
        self.route(value, routed)
        value["phase"] = "serving"
        self.save(value)

    def finish(self, primary):
        value = self.load()
        self.ready(primary)
        config = self.backup.read_bytes()
        self.route(value, config)
        value["phase"] = "drained"
        self.save(value)
        self.state.unlink()
        self.backup.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["begin", "hold", "finish", "ready"])
    parser.add_argument("--container", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--frontend")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.container):
        raise BridgeError("Invalid container identity")
    if not re.fullmatch(r"[a-z0-9.-]+", args.domain):
        raise BridgeError("Invalid public domain")
    bridge = Bridge("/opt/msg-dev/deploy-state", Docker(args.domain))
    if args.action == "begin":
        if not args.frontend or not re.fullmatch(r"[a-f0-9]{12,64}", args.frontend):
            raise BridgeError("Expected the current frontend container ID")
        bridge.begin(args.container, args.frontend)
    elif args.action == "finish":
        bridge.finish(args.container)
    elif args.action == "hold":
        bridge.hold()
    else:
        bridge.ready(args.container)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(
            "Backend bridge failed; retain the serving candidate and inspect private state",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
