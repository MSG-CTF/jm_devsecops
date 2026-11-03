#!/usr/bin/env python3
"""Restricted SSH entry point for the frontend development VM deploy key."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any


CONFIG_PATH = Path("/etc/msg-dev/frontend-deploy-gateway.json")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")


class GatewayError(ValueError):
    pass


def parse_command(command: str) -> tuple[str, str, str]:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError as error:
        raise GatewayError("invalid SSH command") from error

    if len(parts) != 4 or parts[0] != "deploy-frontend":
        raise GatewayError("only deploy-frontend is allowed")

    commit_sha, run_id, pipeline_ref = parts[1:]
    if not COMMIT_PATTERN.fullmatch(commit_sha):
        raise GatewayError("invalid commit SHA")
    if not RUN_PATTERN.fullmatch(run_id):
        raise GatewayError("invalid GitHub run identifier")
    if not COMMIT_PATTERN.fullmatch(pipeline_ref):
        raise GatewayError("invalid pipeline reference")
    return commit_sha, run_id, pipeline_ref


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.is_absolute() or not path.is_file():
        raise GatewayError("gateway config is missing")
    stat = path.stat()
    if stat.st_uid != 0 or stat.st_mode & 0o022:
        raise GatewayError("gateway config ownership or permissions are unsafe")

    config = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "pipeline_ref",
        "repository",
        "public_domain",
        "deploy_script",
        "deploy_script_sha256",
        "bridge_helper",
        "bridge_helper_sha256",
    }
    if set(config) != required:
        raise GatewayError("gateway config keys do not match the contract")
    if not COMMIT_PATTERN.fullmatch(config["pipeline_ref"]):
        raise GatewayError("configured pipeline reference is invalid")
    if config["repository"] != "MSG-CTF/front-team":
        raise GatewayError("configured repository is not allowed")
    if config["public_domain"] != "msgctf.kr":
        raise GatewayError("configured domain is not allowed")

    for path_key, digest_key in (
        ("deploy_script", "deploy_script_sha256"),
        ("bridge_helper", "bridge_helper_sha256"),
    ):
        target = Path(config[path_key])
        if not target.is_absolute() or not target.is_file():
            raise GatewayError(f"{path_key} is missing")
        target_stat = target.stat()
        if target_stat.st_uid != 0 or target_stat.st_mode & 0o022:
            raise GatewayError(f"{path_key} ownership or permissions are unsafe")
        if sha256(target) != config[digest_key]:
            raise GatewayError(f"{path_key} digest does not match")
    return config


def build_deploy_command(
    config: dict[str, Any], commit_sha: str, run_id: str
) -> list[str]:
    return [
        "/usr/bin/bash",
        config["deploy_script"],
        "frontend",
        config["repository"],
        commit_sha,
        config["public_domain"],
        f"gh-{run_id}",
        config["bridge_helper"],
    ]


def main() -> int:
    try:
        commit_sha, run_id, pipeline_ref = parse_command(
            os.environ.get("SSH_ORIGINAL_COMMAND", "")
        )
        config = load_config()
        if pipeline_ref != config["pipeline_ref"]:
            raise GatewayError("pipeline reference is not installed on this VM")
        command = build_deploy_command(config, commit_sha, run_id)
    except (GatewayError, json.JSONDecodeError, OSError, TypeError) as error:
        print(f"deployment request rejected: {error}", file=sys.stderr)
        return 2

    environment = {
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
