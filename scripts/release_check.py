"""Exact tag/version/source safety check for a release event."""
import os
import pathlib
import re
import sys
import tomllib

root = pathlib.Path(__file__).resolve().parents[1]
project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
version = project["version"]
expected = "v" + version
tag = os.environ.get("RELEASE_TAG")
if tag != expected:
    sys.exit(f"FAIL: tag {tag!r} does not match project version {expected!r}")
if not re.fullmatch(r"0\.1\.0", version):
    sys.exit("FAIL: unexpected version")
required = ["README.md", "LICENSE", "SECURITY.md", "VALIDATION.md", "INTEGRATION-CONTRACT.md"]
for name in required:
    if not (root / name).is_file():
        sys.exit(f"FAIL: missing {name}")
print("Release file/tag check passed:", expected)
