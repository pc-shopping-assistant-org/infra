import copy
import importlib.util
from pathlib import Path
import unittest


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
        "AI_HARDWARE_CATALOG_MAPPING": '{"categories":{"catalog-uuid":"CPU"}}',
        "AI_PROVIDER": "openai", "AI_OPENAI_API_KEY": "test-key",
    }},
}


class DeploymentPreflight(unittest.TestCase):
    def test_accepts_explicit_application_configuration(self):
        self.assertEqual(deploy.deployment_errors(VALID_SERVICES), [])

    def test_missing_signing_keys_provider_key_and_mapping_are_reported_without_secrets(self):
        services = copy.deepcopy(VALID_SERVICES)
        services["identity-service"]["environment"]["JWT_PRIVATE_KEY"] = ""
        services["ai-service"]["environment"].update({
            "AI_HARDWARE_CATALOG_MAPPING": "{}", "AI_OPENAI_API_KEY": "",
        })
        errors = deploy.deployment_errors(services)
        self.assertEqual(len(errors), 3)
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


if __name__ == "__main__":
    unittest.main()
