import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy.py"
spec = importlib.util.spec_from_file_location("deploy", SCRIPT)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

VALID_SERVICES = {
    "identity-service": {"environment": {
        "JWT_PRIVATE_KEY": "private-test-key", "JWT_PUBLIC_KEY": "public-test-key",
    }},
    "ai-service": {"environment": {
        "AI_DATABASE_URL": "postgresql+psycopg://postgres:test@postgres:5432/ai_db",
        "AI_PROVIDER": "openai", "AI_OPENAI_API_KEY": "test-key",
    }},
}


class DeploymentPreflight(unittest.TestCase):
    def test_bootstrap_does_not_require_category_mapping(self):
        services = copy.deepcopy(VALID_SERVICES)
        services["ai-service"]["environment"]["AI_HARDWARE_CATALOG_MAPPING"] = ""
        self.assertEqual(deploy.deployment_errors(services), [])

    def test_accepts_explicit_application_configuration(self):
        self.assertEqual(deploy.deployment_errors(VALID_SERVICES), [])

    def test_missing_signing_and_provider_keys_are_reported_without_secrets(self):
        services = copy.deepcopy(VALID_SERVICES)
        services["identity-service"]["environment"]["JWT_PRIVATE_KEY"] = ""
        services["ai-service"]["environment"].update({
            "AI_HARDWARE_CATALOG_MAPPING": "{}", "AI_OPENAI_API_KEY": "",
        })
        errors = deploy.deployment_errors(services)
        self.assertEqual(len(errors), 2)
        self.assertNotIn("public-test-key", " ".join(errors))

    def test_localhost_database_url_is_rejected_for_container_deployment(self):
        services = copy.deepcopy(VALID_SERVICES)
        services["ai-service"]["environment"]["AI_DATABASE_URL"] = "postgresql+psycopg://postgres:secret@localhost:5433/ai_db"
        self.assertEqual(len(deploy.deployment_errors(services)), 1)
        self.assertNotIn("secret", " ".join(deploy.deployment_errors(services)))

    def test_qwen_requires_a_real_endpoint_without_requiring_an_openai_key(self):
        services = copy.deepcopy(VALID_SERVICES)
        ai = services["ai-service"]["environment"]
        ai.update({"AI_PROVIDER": "qwen", "AI_OPENAI_API_KEY": "", "AI_QWEN_BASE_URL": "http://model-server:8001/v1"})
        self.assertEqual(len(deploy.deployment_errors(services)), 1)
        ai["AI_QWEN_BASE_URL"] = "http://gpu.internal:8001/v1"
        self.assertEqual(deploy.deployment_errors(services), [])


class DeploymentBootstrap(unittest.TestCase):
    def test_bootstrap_seeds_before_starting_apps_and_preserves_global_env(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env.docker"
            original = "POSTGRES_PASSWORD=test-secret\nAI_HARDWARE_CATALOG_MAPPING=\n"
            env.write_text(original)
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(deploy, "ROOT", Path(directory)), patch.object(deploy.subprocess, "run", side_effect=run):
                deploy.bootstrap()
            self.assertIn("pull", calls[0])
            self.assertEqual(calls[1], [str(Path(directory) / "scripts" / "seed-prod.sh")])
            self.assertIn("up", calls[2])
            self.assertEqual(env.read_text(), original)

    def test_failed_seed_prevents_application_start(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[0].endswith("seed-prod.sh"):
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)

        with patch.object(deploy.subprocess, "run", side_effect=run), self.assertRaises(subprocess.CalledProcessError):
            deploy.bootstrap()
        self.assertFalse(any("up" in command for command in calls))


if __name__ == "__main__":
    unittest.main()
