"""Explicit, repeatable connector setup after catalog migrations; never print config."""

import json
import os
from pathlib import Path
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


CONNECTOR_NAME = "catalog-connector"
SETUP_ATTEMPTS = 30
RETRY_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 10


def register():
    base_url = os.environ["CONNECT_URL"].rstrip("/")
    config = json.loads(Path("/config/catalog-connector.json").read_text())
    config["database.user"] = os.environ["POSTGRES_USER"]
    config["database.password"] = os.environ["POSTGRES_PASSWORD"]
    request = Request(
        f"{base_url}/connectors/{CONNECTOR_NAME}/config",
        data=json.dumps(config).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    for attempt in range(SETUP_ATTEMPTS):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS):
                break
        except URLError:
            if attempt == SETUP_ATTEMPTS - 1:
                raise SystemExit("Connector registration failed; inspect Kafka Connect logs") from None
            time.sleep(RETRY_SECONDS)
    for _ in range(SETUP_ATTEMPTS):
        with urlopen(f"{base_url}/connectors/{CONNECTOR_NAME}/status", timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = json.load(response)
        states = [status["connector"]["state"], *(task["state"] for task in status["tasks"])]
        if "FAILED" in states:
            raise SystemExit("Connector/task failed; inspect Kafka Connect logs")
        if status["tasks"] and all(state == "RUNNING" for state in states):
            print("catalog-connector and tasks RUNNING")
            return
        time.sleep(RETRY_SECONDS)
    raise SystemExit("Connector did not reach RUNNING before setup timeout")


if __name__ == "__main__":
    register()
