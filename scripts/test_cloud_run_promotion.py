"""Replay the workflow shell with local CLI doubles; never contact GCP."""

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml


WORKFLOW = Path(
    os.environ.get(
        "WORKFLOW_UNDER_TEST",
        Path(__file__).resolve().parents[1]
        / ".github/workflows/reusable-backend-deploy-dev.yml",
    )
)
DOCUMENT = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
STEPS = DOCUMENT["jobs"]["migrate-deploy-smoke"]["steps"]


def condition(expression, steps):
    """Evaluate only the Boolean subset used by these actual workflow gates."""
    expression = (
        str(expression).replace("always()", "True").replace("cancelled()", "False")
    )

    def replace(match):
        step, key, field = match.groups()
        value = steps.get(step, {}).get(key, "skipped" if key == "outcome" else {})
        if field:
            value = value.get(field, "")
        return repr(value)

    expression = re.sub(
        r"steps\.([\w-]+)\.(outcome|outputs)(?:\.([\w-]+))?", replace, expression
    )
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression).strip()

    def visit(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
            return node.value
        if isinstance(node, ast.BoolOp):
            values = [visit(value) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not visit(node.operand)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = visit(node.left), visit(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        raise ValueError("Unsupported expression in controlled workflow replay")

    return bool(visit(ast.parse(expression, mode="eval").body))


GCLOUD = """#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
path=Path(os.environ['CLI_STATE'])
state=json.loads(path.read_text())
args=sys.argv[1:]
mode=state['mode']
if args[:4]==['run','services','update-traffic','fixture-service']:
    wanted=next(value.split('=',1)[1] for value in args if value.startswith('--to-revisions='))
    previous=wanted=='fixture-old=100'
    state['mutations'].append(wanted)
    code=0
    if previous:
        if mode!='rollback-rejected': state['traffic']=[{'revisionName':'fixture-old','percent':100}]
        if mode in ('rollback-response-loss','rollback-rejected'): code=1
    elif mode=='not-applied': code=1
    elif mode=='other-owner':
        state['traffic']=[{'revisionName':'someone-else','percent':100}];code=1
    elif mode=='partial':
        state['traffic']=[{'revisionName':'fixture-new','percent':50},{'revisionName':'fixture-old','percent':50}];code=1
    else:
        state['traffic']=[{'revisionName':'fixture-new','percent':100}]
        if mode in ('response-loss','response-loss-smoke-failure'): code=1
    path.write_text(json.dumps(state))
    raise SystemExit(code)
if args[:4]==['run','services','describe','fixture-service']:
    state['queries']+=1;path.write_text(json.dumps(state))
    if mode=='query-unavailable': raise SystemExit(1)
    print(json.dumps({'status':{'traffic':state['traffic']}}))
    raise SystemExit(0)
raise SystemExit('Unexpected command in local GCP double')
"""

CURL = """#!/usr/bin/env python3
import json,os
from pathlib import Path
path=Path(os.environ['CLI_STATE'])
state=json.loads(path.read_text());state['smoke_calls']+=1;path.write_text(json.dumps(state))
raise SystemExit(1 if state['mode'] in ('response-loss-smoke-failure','smoke-failure','rollback-response-loss','rollback-rejected') else 0)
"""


class PromotionReplayTests(unittest.TestCase):
    def replay(self, mode):
        with tempfile.TemporaryDirectory(prefix="cloud-run-replay-") as temporary:
            root = Path(temporary)
            binary = root / "bin"
            binary.mkdir()
            for name, code in (
                ("gcloud", GCLOUD),
                ("curl", CURL),
                ("sleep", "#!/bin/sh\nexit 0\n"),
            ):
                path = binary / name
                path.write_text(code, encoding="utf-8")
                path.chmod(0o700)
            state_file = root / "state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "mode": mode,
                        "traffic": [{"revisionName": "fixture-old", "percent": 100}],
                        "mutations": [],
                        "queries": 0,
                        "smoke_calls": 0,
                    }
                ),
                encoding="utf-8",
            )
            environment = {
                "PATH": str(binary) + ":/usr/bin:/bin",
                "CLI_STATE": str(state_file),
                "PROJECT_ID": "fixture-project",
                "REGION": "fixture-region",
                "CLOUD_RUN_SERVICE": "fixture-service",
                "NEW_REVISION": "fixture-new",
                "PREVIOUS_REVISION": "fixture-old",
                "SERVICE_URL": "https://fixture.invalid",
                "SMOKE_TEST_PATH": "/admin/login/",
                "RUNNER_TEMP": str(root),
            }
            state = {
                name: {"outcome": "success", "outputs": {}}
                for name in (
                    "smoke",
                    "runtime-dependencies",
                    "dast-coverage",
                    "dast",
                    "zap-artifact",
                )
            }
            state["deployed"] = {
                "outcome": "success",
                "outputs": {
                    "revision_name": "fixture-new",
                    "service_url": "https://fixture.invalid",
                },
            }
            state["previous"] = {
                "outcome": "success",
                "outputs": {"revision": "fixture-old"},
            }
            for index, step in enumerate(STEPS):
                name = step.get("name", "")
                identifier = step.get("id", "")
                relevant = identifier in (
                    "promote",
                    "promotion-state",
                    "post-promote-smoke",
                )
                relevant |= name.startswith(
                    ("traffic 전환 후 실패", "검사·전환·보고서 실패")
                )
                if not relevant:
                    continue
                identifier = identifier or (
                    "rollback" if name.startswith("traffic 전환 후 실패") else "final"
                )
                if not condition(step.get("if", "True"), state):
                    state[identifier] = {"outcome": "skipped", "outputs": {}}
                    continue
                output = root / f"output-{index}"
                output.touch()
                environment["GITHUB_OUTPUT"] = str(output)
                result = subprocess.run(
                    ["bash", "-c", step["run"]],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                outputs = dict(
                    line.split("=", 1)
                    for line in output.read_text().splitlines()
                    if "=" in line
                )
                state[identifier] = {
                    "outcome": "success" if result.returncode == 0 else "failure",
                    "outputs": outputs,
                }
            observed = json.loads(state_file.read_text())
            return observed, state

    def test_normal_switch_is_checked_before_smoke(self):
        observed, steps = self.replay("normal")
        self.assertEqual(
            observed["traffic"], [{"revisionName": "fixture-new", "percent": 100}]
        )
        self.assertGreater(observed["queries"], 0)
        self.assertGreater(observed["smoke_calls"], 0)
        self.assertEqual(steps["final"]["outcome"], "skipped")

    def test_committed_switch_response_loss_still_gets_smoke(self):
        observed, steps = self.replay("response-loss")
        self.assertGreater(
            observed["smoke_calls"],
            0,
            "A committed switch must receive a smoke check despite a lost CLI response",
        )
        self.assertEqual(steps["final"]["outcome"], "skipped")

    def test_lost_response_and_failed_smoke_restore_previous_revision(self):
        observed, steps = self.replay("response-loss-smoke-failure")
        self.assertEqual(
            observed["traffic"], [{"revisionName": "fixture-old", "percent": 100}]
        )
        self.assertEqual(steps["rollback"]["outcome"], "success")
        self.assertEqual(steps["final"]["outcome"], "failure")

    def test_partial_owned_switch_is_rolled_back(self):
        observed, steps = self.replay("partial")
        self.assertEqual(
            observed["traffic"], [{"revisionName": "fixture-old", "percent": 100}]
        )
        self.assertEqual(steps["rollback"]["outcome"], "success")
        self.assertEqual(steps["final"]["outcome"], "failure")

    def test_unapplied_switch_fails_without_rewriting_previous_traffic(self):
        observed, steps = self.replay("not-applied")
        self.assertEqual(observed["mutations"], ["fixture-new=100"])
        self.assertEqual(observed["smoke_calls"], 0)
        self.assertEqual(steps["final"]["outcome"], "failure")

    def test_unavailable_observation_does_not_claim_success_or_rollback_blindly(self):
        observed, steps = self.replay("query-unavailable")
        self.assertEqual(observed["mutations"], ["fixture-new=100"])
        self.assertEqual(observed["smoke_calls"], 0)
        self.assertEqual(steps["final"]["outcome"], "failure")

    def test_another_operators_traffic_is_not_overwritten(self):
        observed, steps = self.replay("other-owner")
        self.assertEqual(observed["mutations"], ["fixture-new=100"])
        self.assertEqual(
            observed["traffic"], [{"revisionName": "someone-else", "percent": 100}]
        )
        self.assertEqual(steps["final"]["outcome"], "failure")

    def test_rollback_response_loss_is_verified_from_actual_traffic(self):
        observed, steps = self.replay("rollback-response-loss")
        self.assertEqual(
            observed["traffic"], [{"revisionName": "fixture-old", "percent": 100}]
        )
        self.assertEqual(steps["rollback"]["outcome"], "success")
        self.assertEqual(steps["final"]["outcome"], "failure")

    def test_failed_rollback_is_not_reported_as_confirmed_recovery(self):
        observed, steps = self.replay("rollback-rejected")
        self.assertEqual(
            observed["traffic"], [{"revisionName": "fixture-new", "percent": 100}]
        )
        self.assertEqual(steps["rollback"]["outcome"], "failure")
        self.assertEqual(steps["final"]["outcome"], "failure")

    def test_scheduler_credential_is_pinned_for_backend_only(self):
        inputs = DOCUMENT.get("on", DOCUMENT.get(True))["workflow_call"]["inputs"]
        self.assertTrue(inputs["scheduler_token_secret_version"]["required"])
        step = next(
            item
            for item in STEPS
            if item["name"] == "migration과 backend의 최소 환경·Secret 분리"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "environment"
            environment = self.valid_inputs()
            environment.update(RUNNER_TEMP=str(root), GITHUB_ENV=str(output))
            result = subprocess.run(
                ["bash", "-c", step["run"]],
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertIn(
                "SCHEDULER_API_TOKEN=scheduler-api-token-dev:1",
                lines["backend_secrets"],
            )
            self.assertNotIn("SCHEDULER", lines["migration_secrets"])

    def valid_inputs(self):
        values = {
            "PATH": "/usr/bin:/bin",
            "COMMIT_SHA": "a" * 40,
            "PROJECT_ID": "fixture-project",
            "REGION": "asia-northeast3",
            "ARTIFACT_REPOSITORY": "msg-ctf-dev",
            "IMAGE_NAME": "backend",
            "CLOUD_RUN_SERVICE": "msg-backend-dev",
            "MIGRATION_JOB": "msg-backend-migrate-dev",
            "VPC_NETWORK": "fixture-vpc",
            "VPC_SUBNET": "fixture-subnet",
            "DATA_NETWORK_TAG": "msg-backend-data-dev",
            "DEPLOYER_SERVICE_ACCOUNT": "github-backend-deployer-dev@fixture-project.iam.gserviceaccount.com",
            "BACKEND_RUNTIME_SERVICE_ACCOUNT": "backend-runtime@fixture-project.iam.gserviceaccount.com",
            "MIGRATION_RUNTIME_SERVICE_ACCOUNT": "backend-migration@fixture-project.iam.gserviceaccount.com",
            "POSTGRES_USER": "msg_app",
            "MIGRATION_POSTGRES_USER": "msg_migrator",
            "POSTGRES_DB": "msg_backend",
            "POSTGRES_HOST": "10.1.0.2",
            "POSTGRES_PORT": "5432",
            "SCHEDULER_BASE_URL": "https://scheduler.example.com",
            "DJANGO_ALLOWED_HOSTS": "backend.example.com",
            "SMOKE_TEST_PATH": "/admin/login/",
            "DAST_SEED_PATHS_JSON": '["/admin/login/","/api/v1/timer"]',
        }
        values["IMAGE_REF"] = (
            "asia-northeast3-docker.pkg.dev/fixture-project/msg-ctf-dev/backend@sha256:"
            + "b" * 64
        )
        for name, secret in (
            ("DJANGO", "django-secret-key-dev"),
            ("JWT", "jwt-secret-dev"),
            ("POSTGRES_PASSWORD", "postgres-password-dev"),
            ("MIGRATION_POSTGRES_PASSWORD", "migration-postgres-password-dev"),
            ("REDIS", "redis-url-dev"),
            ("KOTH", "koth-team-token-secret-dev"),
            ("SCHEDULER_TOKEN", "scheduler-api-token-dev"),
        ):
            values[name + "_SECRET_ID"] = secret
            values[name + "_SECRET_VERSION"] = "1"
        return values

    def test_scheduler_origin_requires_https_without_embedded_credentials(self):
        validation = next(step for step in STEPS if step["name"] == "배포 입력값 확인")
        cases = [
            ("https://scheduler.example.com", 0),
            ("http://scheduler.example.com", 1),
            ("https://user:password@scheduler.example.com", 1),
            ("https://scheduler.example.com?token=fixture", 1),
            ("https://scheduler.example.com/path", 1),
        ]
        for endpoint, expected in cases:
            with self.subTest(endpoint=endpoint):
                environment = self.valid_inputs()
                environment["SCHEDULER_BASE_URL"] = endpoint
                result = subprocess.run(
                    ["bash", "-c", validation["run"]],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(
                    result.returncode, expected, result.stdout + result.stderr
                )


if __name__ == "__main__":
    unittest.main()
