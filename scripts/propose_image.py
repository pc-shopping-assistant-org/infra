#!/usr/bin/env python3
"""Update one production image pin after its release image has been published."""

import re
import sys
from pathlib import Path

from release_images import read_pins


COMPONENT_KEYS = {
    "backend": "BACKEND_IMAGE_TAG",
    "ai": "AI_IMAGE_TAG",
    "web": "WEB_IMAGE_TAG",
}
VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-rc\.[1-9]\d*)?")


def update_pin(path: Path, component: str, release_branch: str) -> tuple[bool, str]:
    key = COMPONENT_KEYS.get(component)
    version = release_branch.removeprefix("release/v")
    if key is None or not release_branch.startswith("release/v") or not VERSION.fullmatch(version):
        raise ValueError("Expected component backend/ai/web and release/vX.Y.Z branch")
    pins = read_pins(path)
    if pins[key] == version:
        return False, version
    path.write_text("\n".join(
        f"{key}={version}" if line.startswith(f"{key}=") else line
        for line in path.read_text().splitlines()
    ) + "\n")
    read_pins(path)
    return True, version


if __name__ == "__main__":
    try:
        changed, version = update_pin(Path("release/images.lock"), sys.argv[1], sys.argv[2])
        with Path(sys.argv[3]).open("a") as output:
            output.write(f"changed={str(changed).lower()}\nversion={version}\n")
    except (IndexError, OSError, ValueError) as error:
        sys.exit(str(error))
