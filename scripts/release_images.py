#!/usr/bin/env python3
"""Validate the server's .release.env and emit its three image tags."""

import re
import sys
from pathlib import Path


KEYS = {"BACKEND_IMAGE_TAG", "AI_IMAGE_TAG", "WEB_IMAGE_TAG"}
TAG = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-rc\.[1-9]\d*)?|sha-[0-9a-f]{40}")


def read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or key not in KEYS or key in pins or not TAG.fullmatch(value):
            raise ValueError(f"Invalid image pin at {path}:{line_number}")
        pins[key] = value
    if pins.keys() != KEYS:
        raise ValueError(f"Missing image pins: {', '.join(sorted(KEYS - pins.keys()))}")
    return pins


if __name__ == "__main__":
    try:
        pins = read_pins(Path(sys.argv[1] if len(sys.argv) == 2 else ".release.env"))
    except (OSError, ValueError) as error:
        sys.exit(str(error))
    for key in sorted(KEYS):
        print(f"{key}={pins[key]}")
