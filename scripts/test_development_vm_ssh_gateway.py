import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("development_vm_ssh_gateway.py")
SPEC = importlib.util.spec_from_file_location("development_vm_ssh_gateway", MODULE_PATH)
gateway = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gateway)


class GatewayCommandTests(unittest.TestCase):
    def test_accepts_exact_frontend_deploy_command(self):
        commit = "a" * 40
        pipeline = "b" * 40
        self.assertEqual(
            gateway.parse_command(f"deploy-frontend {commit} 12345-2 {pipeline}"),
            (commit, "12345-2", pipeline),
        )

    def test_rejects_shell_syntax_as_extra_arguments(self):
        commit = "a" * 40
        pipeline = "b" * 40
        with self.assertRaises(gateway.GatewayError):
            gateway.parse_command(f"deploy-frontend {commit} 12345-2 {pipeline}; id")

    def test_rejects_non_sha_commit(self):
        with self.assertRaises(gateway.GatewayError):
            gateway.parse_command(f"deploy-frontend main 12345-2 {'b' * 40}")

    def test_builds_fixed_repository_and_domain_command(self):
        config = {
            "deploy_script": "/usr/local/libexec/msg-dev/deploy-development-vm.sh",
            "bridge_helper": "/usr/local/libexec/msg-dev/backend_proxy_bridge.py",
            "repository": "MSG-CTF/front-team",
            "public_domain": "msgctf.kr",
        }
        command = gateway.build_deploy_command(config, "a" * 40, "12345-2")
        self.assertEqual(command[0], "/usr/bin/bash")
        self.assertEqual(command[2:5], ["frontend", "MSG-CTF/front-team", "a" * 40])
        self.assertEqual(command[5:7], ["msgctf.kr", "gh-12345-2"])


if __name__ == "__main__":
    unittest.main()
