#!/usr/bin/env python3
"""Validate the tracked release.toml and emit image environment variables."""

import re
import sys
import tomllib
from pathlib import Path


KEYS = {"BACKEND_IMAGE_TAG", "AI_IMAGE_TAG", "WEB_IMAGE_TAG"}
TAG = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-rc\.[1-9]\d*)?|sha-[0-9a-f]{40}")


def read_pins(path: Path) -> dict[str, str]:
    if path.suffix == ".toml":
        raw = tomllib.loads(path.read_text())
        values = raw.get("images", {})
        pins = {
            "BACKEND_IMAGE_TAG": values.get("backend"),
            "AI_IMAGE_TAG": values.get("ai"),
            "WEB_IMAGE_TAG": values.get("web"),
        }
        if any(value is not None and not isinstance(value, str) for value in pins.values()):
            raise ValueError(f"Image tags must be strings in {path}")
        pins = {key: value for key, value in pins.items() if value is not None}
    else:
        pins = {}
        lines = path.read_text().splitlines()
        for line_number, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator != "=" or key not in KEYS or key in pins or not TAG.fullmatch(value):
                raise ValueError(f"Invalid image pin at {path}:{line_number}")
            pins[key] = value
    if pins.keys() != KEYS or any(not TAG.fullmatch(value) for value in pins.values()):
        raise ValueError(f"Missing image pins: {', '.join(sorted(KEYS - pins.keys()))}")
    return pins


if __name__ == "__main__":
    try:
        pins = read_pins(Path(sys.argv[1] if len(sys.argv) == 2 else "release.toml"))
    except (OSError, ValueError) as error:
        sys.exit(str(error))
    for key in sorted(KEYS):
        print(f"{key}={pins[key]}")
