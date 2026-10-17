"""Offline deployment contracts; Docker Compose is required, no daemon is used."""

import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BACKEND_SERVICES = {
    "discovery-server", "api-gateway", "identity-service", "catalog-service",
    "order-service", "payment-service", "promotion-service", "search-service",
    "media-service", "sync-service",
}
TEST_ENV = {
    "POSTGRES_PASSWORD": "compose-test-only",
    "REDIS_PASSWORD": "compose-test-only",
    "RABBITMQ_PASSWORD": "compose-test-only",
    "KAFKA_PASSWORD": "compose-test-only",
    "ELASTIC_PASSWORD": "compose-test-only",
    "GRAFANA_ADMIN_PASSWORD": "compose-test-only",
    "BACKEND_IMAGE_TAG": "sha-test",
    "AI_IMAGE_TAG": "sha-test",
    "WEB_IMAGE_TAG": "sha-test",
    "JWT_PRIVATE_KEY": "test-private-key",
    "JWT_PUBLIC_KEY": "test-public-key",
    "AI_DATABASE_URL": "postgresql+psycopg://postgres:test@postgres:5432/ai_db",
    "AI_HARDWARE_CATALOG_MAPPING": '{"version":"catalog-hardware-v1"}',
    "VNPAY_RETURN_URL": "https://gearpc.example.test/vi/payment/vnpay-return",
}


def render(profile=None, overrides=None):
    # Do not inherit developer secrets or read an ignored .env during validation.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_CONFIG") if key in os.environ}
    env.update(TEST_ENV)
    env.update(overrides or {})
    command = ["docker", "compose", "--env-file", "/dev/null", "-f", str(ROOT / "compose.yaml")]
    if profile:
        command.extend(["--profile", profile])
    return subprocess.run(
        [*command, "config", "--format", "json"],
        env=env, capture_output=True, text=True, check=False,
    )


class ComposeContracts(unittest.TestCase):
    def test_repository_has_one_compose_entrypoint(self):
        self.assertEqual(sorted(ROOT.rglob("compose.yaml")), [ROOT / "compose.yaml"])

    def config(self, profile=None):
        result = render(profile)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_server_contains_dependencies_not_applications(self):
        config = self.config()
        self.assertEqual(set(config["services"]), {
            "postgres", "redis", "rabbitmq", "kafka", "elasticsearch", "kafka-connect",
            "lgtm",
        })
        for service in self.config("*")["services"].values():
            self.assertNotIn("build", service)
            for port in service.get("ports", []):
                self.assertEqual(port["host_ip"], "127.0.0.1")
            for volume in service.get("volumes", []):
                if volume["type"] == "bind":
                    self.assertTrue(Path(volume["source"]).exists(), volume["source"])

    def test_infra_only_does_not_require_application_secrets(self):
        result = render(overrides={
            "JWT_PRIVATE_KEY": "", "JWT_PUBLIC_KEY": "", "AI_DATABASE_URL": "",
            "AI_HARDWARE_CATALOG_MAPPING": "", "AI_OPENAI_API_KEY": "",
        })
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_connector_is_an_explicit_setup_step_without_checked_in_passwords(self):
        config = self.config("setup")
        self.assertIn("register-connector", config["services"])
        connector = json.loads((ROOT / "config/debezium/catalog-connector.json").read_text())
        self.assertNotIn("database.password", connector)
        self.assertEqual(len(connector["table.include.list"].split(",")), 5)

    def test_catalog_seed_is_an_explicit_database_only_setup_job(self):
        seed = self.config("setup")["services"]["catalog-seed"]
        self.assertEqual(seed["command"], ["--seed-prod"])
        self.assertEqual(set(seed["depends_on"]), {"postgres"})
        self.assertEqual(seed["restart"], "no")
        self.assertTrue(seed["healthcheck"]["disable"])
        self.assertNotIn("JWT_PRIVATE_KEY", seed["environment"])

    def test_credentials_are_required_and_authentication_is_enabled(self):
        config = self.config()["services"]
        self.assertEqual(config["elasticsearch"]["environment"]["xpack.security.enabled"], "true")
        self.assertIn("--requirepass", " ".join(config["redis"]["command"]))
        kafka = config["kafka"]["environment"]
        self.assertIn("EXTERNAL:SASL_PLAINTEXT", kafka["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"])
        for key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "KAFKA_PASSWORD", "ELASTIC_PASSWORD"):
            with self.subTest(key=key):
                self.assertNotEqual(render(overrides={key: ""}).returncode, 0)

    def test_kafka_advertises_reachable_external_address_and_keeps_data(self):
        config = self.config()
        env = config["services"]["kafka"]["environment"]
        self.assertIn("EXTERNAL://127.0.0.1:29092", env["KAFKA_ADVERTISED_LISTENERS"])
        self.assertEqual(env["KAFKA_LOG_DIRS"], "/var/lib/kafka/data")
        remote = json.loads(render(overrides={"KAFKA_ADVERTISED_HOST": "kafka.example.test"}).stdout)
        self.assertIn("EXTERNAL://kafka.example.test:29092", remote["services"]["kafka"]["environment"]["KAFKA_ADVERTISED_LISTENERS"])

    def test_apps_profile_adds_separate_ghcr_images_on_the_same_network(self):
        config = self.config("apps")
        server = self.config()
        apps = BACKEND_SERVICES | {"ai-service", "ai-migrate", "web-frontend"}
        self.assertEqual(set(config["services"]) - set(server["services"]), apps)
        self.assertEqual(config["networks"], server["networks"])
        for name in apps:
            service = config["services"][name]
            self.assertNotIn("build", service)
            self.assertTrue(service["image"].startswith("ghcr.io/pc-shopping-assistant-org/"))
            self.assertTrue(service["image"].endswith(":sha-test"))
            for port in service.get("ports", []):
                self.assertIn(name, {"api-gateway", "web-frontend"})
                self.assertEqual(port["host_ip"], "127.0.0.1")

    def test_application_endpoints_use_container_dns_not_localhost(self):
        services = self.config("apps")["services"]
        for name in BACKEND_SERVICES - {"discovery-server"}:
            env = services[name]["environment"]
            self.assertEqual(env["EUREKA_CLIENT_SERVICEURL_DEFAULTZONE"], "http://discovery-server:8761/eureka/")
            self.assertEqual(env["JWT_JWKS_URI"], "http://identity-service:8081/.well-known/jwks.json")
        self.assertEqual(services["sync-service"]["environment"]["SPRING_KAFKA_BOOTSTRAP_SERVERS"], "kafka:9092")
        self.assertEqual(services["web-frontend"]["environment"]["BACKEND_API_URL"], "http://api-gateway:8080")
        self.assertEqual(services["ai-service"]["environment"]["AI_BACKEND_API_URL"], "http://catalog-service:8082")

    def test_identity_has_explicit_signing_keys_and_ai_waits_for_migration(self):
        services = self.config("apps")["services"]
        self.assertEqual(services["identity-service"]["environment"]["JWT_ALLOW_DEV_KEY_GENERATION"], "false")
        self.assertEqual(services["ai-service"]["depends_on"]["ai-migrate"]["condition"], "service_completed_successfully")
        self.assertIn("alembic upgrade head", " ".join(services["ai-migrate"]["command"]))
        self.assertEqual(services["ai-migrate"]["depends_on"]["postgres"]["condition"], "service_healthy")

    def test_frontend_only_receives_its_allowlisted_runtime_configuration(self):
        services = self.config("apps")["services"]
        self.assertEqual(set(services["web-frontend"]["environment"]), {
            "BACKEND_API_URL", "AI_API_URL", "GOOGLE_CLIENT_ID",
        })
        self.assertEqual(services["identity-service"]["environment"]["JWT_PRIVATE_KEY"], "test-private-key")


if __name__ == "__main__":
    unittest.main()
