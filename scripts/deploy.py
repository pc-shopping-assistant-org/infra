"""Validate app settings before explicitly pulling/starting the GHCR profile."""

import argparse
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [str(ROOT / "compose.sh"), "--profile", "apps"]
DEPLOY_WAIT_SECONDS = 300


def deployment_errors(services):
    errors = []
    identity = services["identity-service"]["environment"]
    for key in ("JWT_PRIVATE_KEY", "JWT_PUBLIC_KEY"):
        if not identity.get(key):
            errors.append(f"Set {key}; deployed identity must not generate temporary keys")
    ai = services["ai-service"]["environment"]
    url = urlsplit(ai.get("AI_DATABASE_URL") or "")
    if url.scheme != "postgresql+psycopg" or url.hostname != "postgres" or url.path != "/ai_db":
        errors.append("Set AI_DATABASE_URL to postgresql+psycopg://...@postgres:5432/ai_db")
    if not (ai.get("AI_MODELS_CONFIG_PATH") or "").strip():
        errors.append("Set AI_MODELS_CONFIG_PATH to the YAML model configuration path")
    has_provider_credential = any(
        (ai.get(key) or "").strip()
        for key in ("AI_GEMINI_API_KEY", "AI_OPENAI_API_KEY", "AI_OPENAI_BASE_URL", "AI_QWEN_BASE_URL")
    )
    if not has_provider_credential:
        errors.append(
            "Set at least one model provider credential or endpoint "
            "(AI_GEMINI_API_KEY, AI_OPENAI_API_KEY, AI_OPENAI_BASE_URL, or AI_QWEN_BASE_URL)"
        )
    if (ai.get("AI_QWEN_BASE_URL") or "").strip() and "model-server" in ai["AI_QWEN_BASE_URL"]:
        errors.append("Set AI_QWEN_BASE_URL to a reachable model endpoint")
    return errors


def bootstrap():
    subprocess.run([*COMPOSE, "pull"], cwd=ROOT, check=True)
    subprocess.run([str(ROOT / "scripts" / "seed-prod.sh")], cwd=ROOT, check=True)
    subprocess.run([*COMPOSE, "up", "-d", "--wait", "--wait-timeout", str(DEPLOY_WAIT_SECONDS)],
                   cwd=ROOT, check=True)
    print("Containers started; verify health, then register the catalog connector")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate only; no pull/start")
    args = parser.parse_args()
    result = subprocess.run(
        [*COMPOSE, "config", "--format", "json"], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise SystemExit("Compose configuration failed; fill infra/.env.docker then run ./compose.sh config --quiet")
    services = json.loads(result.stdout)["services"]
    errors = deployment_errors(services)
    if errors:
        raise SystemExit("\n".join(errors))
    print("Application deployment configuration OK (no secrets printed)")
    if not args.check:
        bootstrap()


if __name__ == "__main__":
    main()
