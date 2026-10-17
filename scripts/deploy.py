"""Validate app settings before explicitly pulling/starting the GHCR profile."""

import argparse
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [str(ROOT / "compose.sh"), "--profile", "apps"]


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
    try:
        mapping = json.loads(ai.get("AI_HARDWARE_CATALOG_MAPPING") or "null")
    except json.JSONDecodeError:
        mapping = None
    if not isinstance(mapping, dict) or not mapping.get("categories"):
        errors.append("Set AI_HARDWARE_CATALOG_MAPPING with real category UUIDs")
    provider = ai["AI_PROVIDER"]
    provider_keys = {"openai": "AI_OPENAI_API_KEY", "gemini": "AI_GEMINI_API_KEY"}
    if provider in provider_keys:
        if not ai.get(provider_keys[provider]):
            errors.append(f"Set {provider_keys[provider]}")
    elif provider == "qwen":
        if not ai.get("AI_QWEN_BASE_URL") or "model-server" in ai["AI_QWEN_BASE_URL"]:
            errors.append("Set AI_QWEN_BASE_URL to a reachable model endpoint")
    else:
        errors.append("AI_PROVIDER must be openai, gemini or qwen")
    return errors


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
    errors = deployment_errors(json.loads(result.stdout)["services"])
    if errors:
        raise SystemExit("\n".join(errors))
    print("Application deployment configuration OK (no secrets printed)")
    if not args.check:
        subprocess.run([*COMPOSE, "pull"], cwd=ROOT, check=True)
        subprocess.run([*COMPOSE, "up", "-d"], cwd=ROOT, check=True)
        print("Containers started; verify health, then register the catalog connector")


if __name__ == "__main__":
    main()
