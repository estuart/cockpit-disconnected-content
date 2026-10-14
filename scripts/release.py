#!/usr/bin/env python3
"""Create the release source archive, including prebuilt frontend assets only."""
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
version = "0.4.0"
output = root / "build" / f"cockpit-disconnected-content-{version}.tar.gz"
output.parent.mkdir(exist_ok=True)
with tarfile.open(output, "w:gz") as archive:
    for name in ["backend", "dist", "packaging", "Makefile", "README.md", "docs",
                 "src", "scripts", "tests", "package.json", "package-lock.json", "tsconfig.json"]:
        path = root / name
        if not path.exists():
            raise SystemExit(f"Missing {name}; build frontend assets first")
        def clean(info):
            if "__pycache__" in Path(info.name).parts:
                return None
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            return info
        archive.add(path, arcname=f"cockpit-disconnected-content-{version}/{name}", filter=clean)
print(output)
