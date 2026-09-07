#!/usr/bin/env python3
"""Inspect Git objects without importing or executing application code."""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys


SYNTHETIC_FIXTURE = "python/tests/fixtures/release_smoke_rf.json"
INPUT_SUFFIXES = {".rfmap", ".tc", ".probe", ".mat", ".h5", ".hdf5", ".nwb", ".csv", ".ipynb"}
PACKAGE_SUFFIXES = {".zip", ".dmg", ".pkg", ".exe", ".p12", ".pfx", ".pem", ".key"}
SECRETS = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|AKIA[0-9A-Z]{16}")
PRIVATE_HOME = re.compile(rb"/(?:Users|home)/(?!developer(?:/|\b)|tester(?:/|\b)|rfmapping(?:/|\b))[^\s\"'/]+")
EMAIL = re.compile(r"<([^<>\s]+@[^<>\s]+)>")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def reason(path: str, data: bytes) -> str | None:
    parts = PurePosixPath(path)
    if any(part in {"data", "dist", ".idea", ".app"} or part.endswith(".app") for part in parts.parts):
        return "experimental data or generated distribution directory"
    if parts.suffix.lower() in INPUT_SUFFIXES | PACKAGE_SUFFIXES:
        return "experimental input, notebook, credential file, or built package"
    if parts.name.startswith(".env") and not parts.name.endswith(".example"):
        return "private environment file"
    if path.startswith(".codex/environments/") or parts.name == "file_names.json":
        return "local environment or recording manifest"
    if SECRETS.search(data):
        return "credential pattern"
    if PRIVATE_HOME.search(data):
        return "personal home-directory path"
    if parts.suffix.lower() == ".json" and (b'"unitsSpikeCounts"' in data or b'"occupancyTimeSec"' in data):
        if path != SYNTHETIC_FIXTURE or len(data) > 16_384 or b"synthetic release smoke test" not in data:
            return "RF result data; only the small, marked synthetic fixture is permitted"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--history", metavar="REF")
    args = parser.parse_args()
    failures = []
    blobs = []
    if args.staged:
        names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\0")
        for raw in filter(None, names):
            path = raw.decode("utf-8", "surrogateescape")
            blobs.append((git("rev-parse", f":{path}").decode().strip(), path))
    else:
        # Resolve first: arbitrary ref text must never become a Git option.
        object_id = git("rev-parse", "--verify", "--end-of-options", args.history).decode().strip()
        while git("cat-file", "-t", object_id).strip() == b"tag":
            tag = git("cat-file", "tag", object_id).decode()
            tagger = next((line for line in tag.splitlines() if line.startswith("tagger ")), "")
            for email in EMAIL.findall(tagger):
                if not (email.endswith("@users.noreply.github.com") or email == "noreply@github.com"):
                    failures.append("tag identity: use a GitHub noreply email address")
            object_id = tag.splitlines()[0].removeprefix("object ")
        ref = git("rev-parse", "--verify", "--end-of-options", f"{args.history}^{{commit}}").decode().strip()
        for line in git("rev-list", "--objects", ref).decode("utf-8", "surrogateescape").splitlines():
            oid, _, path = line.partition(" ")
            if path:
                blobs.append((oid, path))
        identities = git("log", "--format=%an <%ae>%n%cn <%ce>", ref).decode()
        for email in set(EMAIL.findall(identities)):
            if not (email.endswith("@users.noreply.github.com") or email == "noreply@github.com"):
                failures.append("commit identity: use a GitHub noreply email address")
    reader = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert reader.stdin is not None and reader.stdout is not None
    try:
        for oid, path in blobs:
            reader.stdin.write((oid + "\n").encode()); reader.stdin.flush()
            header = reader.stdout.readline().split()
            content = reader.stdout.read(int(header[2])); reader.stdout.read(1)
            if header[1] != b"blob":
                continue
            problem = reason(path, content)
            if problem:
                failures.append(f"{path}: {problem}")
    finally:
        reader.stdin.close()
        reader.wait()
    for failure in sorted(set(failures)):
        print(f"Privacy check failed: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"Repository privacy check passed ({len(blobs)} objects inspected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
