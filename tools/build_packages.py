#!/usr/bin/env python3
"""Build installable zips for the bundled extensions and themes and write packages/index.json.

    python tools/build_packages.py

Each entry in the index has the package name, kind, version, the core version it needs, the zip's
file name and its SHA-256. The admin screen Site > Packages installs from this index and refuses a
zip whose hash does not match.
"""
import hashlib
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "packages")
SOURCES = [("extension", os.path.join(ROOT, "contrib", "extensions"), "extension.json"),
           ("theme", os.path.join(ROOT, "themes"), "theme.json")]
SKIP_THEMES = {"default"}  # ships with the core


def build():
	os.makedirs(OUT, exist_ok=True)
	index = {"format": 1, "packages": []}
	for kind, base, manifest_name in SOURCES:
		if not os.path.isdir(base):
			continue
		for slug in sorted(os.listdir(base)):
			folder = os.path.join(base, slug)
			manifest_path = os.path.join(folder, manifest_name)
			if not os.path.isfile(manifest_path) or (kind == "theme" and slug in SKIP_THEMES):
				continue
			meta = json.load(open(manifest_path, encoding="utf-8"))
			version = str(meta.get("version", "0"))
			if not re.match(r"^[A-Za-z0-9._-]+$", version):
				sys.exit(f"{slug}: bad version {version!r}")
			filename = f"{slug}-{version}.zip"
			path = os.path.join(OUT, filename)
			with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
				for root, dirs, files in os.walk(folder):
					dirs[:] = sorted(d for d in dirs if d != "__pycache__")
					for name in sorted(files):
						if name.endswith(".pyc"):
							continue
						full = os.path.join(root, name)
						info = zipfile.ZipInfo(os.path.join(slug, os.path.relpath(full, folder)).replace(os.sep, "/"), (2020, 1, 1, 0, 0, 0))
						info.compress_type = zipfile.ZIP_DEFLATED
						zf.writestr(info, open(full, "rb").read())
			digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
			index["packages"].append({
				"kind": kind, "slug": slug, "name": meta.get("name", slug), "version": version,
				"description": meta.get("description", ""), "author": meta.get("author", ""),
				"requires_core": meta.get("requires_core", ""), "file": filename, "sha256": digest,
			})
	with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as fh:
		json.dump(index, fh, indent=2)
		fh.write("\n")
	print(f"Wrote {len(index['packages'])} package(s) to {OUT}")


if __name__ == "__main__":
	build()
