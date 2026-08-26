#!/usr/bin/env python3
"""Verify component versions against the canonical release manifest."""

from __future__ import annotations

import argparse
import ast
import json
import re
import tomllib
from pathlib import Path
from typing import Any


SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
APPLE_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def literal_assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[object] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            if node.value is not None:
                values.append(ast.literal_eval(node.value))
    if len(values) != 1:
        raise ValueError(f"{path} must define exactly one literal {name}")
    return values[0]


def shell_assignment(path: Path, name: str) -> str:
    pattern = re.compile(rf'^{re.escape(name)}="([^"]+)"$')
    matches = [
        match.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := pattern.fullmatch(line)) is not None
    ]
    if len(matches) != 1:
        raise ValueError(f"{path} must define exactly one quoted {name}")
    return matches[0]


def powershell_assignment(path: Path, name: str) -> str:
    pattern = re.compile(rf'^\${re.escape(name)} = "([^"]+)"$')
    matches = [
        match.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := pattern.fullmatch(line)) is not None
    ]
    if len(matches) != 1:
        raise ValueError(f"{path} must define exactly one quoted ${name}")
    return matches[0]


def inno_define(path: Path, name: str) -> str:
    pattern = re.compile(rf'^\s*#define {re.escape(name)} "([^"]+)"$')
    matches = [
        match.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := pattern.fullmatch(line)) is not None
    ]
    if len(matches) != 1:
        raise ValueError(f"{path} must define exactly one quoted #define {name}")
    return matches[0]


def toml_project_version(path: Path) -> str:
    with path.open("rb") as stream:
        value = tomllib.load(stream)["project"]["version"]
    if not isinstance(value, str):
        raise ValueError(f"{path} project.version must be a string")
    return value


def expect(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label} is {actual!r}; expected {expected!r}")


def semver_core(version: str) -> tuple[int, int, int]:
    match = SEMVER_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid SemVer: {version}")
    return tuple(int(match.group(index)) for index in (1, 2, 3))


def component(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    value = manifest["components"][name]
    if not isinstance(value, dict):
        raise ValueError(f"manifest component {name} must be an object")
    return value


def verify_manifest(manifest: dict[str, Any]) -> None:
    expect(manifest.get("schema_version"), 1, "manifest schema_version")
    reference = manifest.get("reference")
    if not isinstance(reference, dict):
        raise ValueError("manifest reference must be an object")
    expect(reference.get("component"), "python_stable", "reference component")
    stable_version = reference.get("version")
    if not isinstance(stable_version, str) or SEMVER_PATTERN.fullmatch(stable_version) is None:
        raise ValueError("reference version must be SemVer")
    stable_core = semver_core(stable_version)
    expect(reference.get("stable_series"), f"{stable_core[0]}.{stable_core[1]}", "stable series")
    policy = manifest.get("policy")
    if not isinstance(policy, dict):
        raise ValueError("manifest policy must be an object")
    expect(policy.get("feature_generation_step"), "minor", "feature generation step")
    expect(
        policy.get("platform_identity_location"),
        "tag-and-artifact-name",
        "platform identity location",
    )

    tags: set[str] = set()
    for name in ("python_stable", "python_freemoving", "swift", "web"):
        entry = component(manifest, name)
        release_version = entry.get("release_version")
        if (
            not isinstance(release_version, str)
            or SEMVER_PATTERN.fullmatch(release_version) is None
        ):
            raise ValueError(f"{name} release_version must be SemVer")
        tag = entry.get("tag")
        if not isinstance(tag, str) or not tag:
            raise ValueError(f"{name} tag must be a non-empty string")
        if tag in tags:
            raise ValueError(f"duplicate component tag: {tag}")
        tags.add(tag)

    python_stable = component(manifest, "python_stable")
    expect(
        python_stable.get("release_version"),
        stable_version,
        "Python stable reference version",
    )
    expect(python_stable.get("channel"), "stable", "Python stable channel")
    expect(
        python_stable.get("feature_generation_offset"),
        0,
        "Python stable feature generation offset",
    )
    stable_marketing_version = python_stable.get("marketing_version")
    if (
        not isinstance(stable_marketing_version, str)
        or APPLE_VERSION_PATTERN.fullmatch(stable_marketing_version) is None
    ):
        raise ValueError("Python stable marketing_version must be three integers")
    expect(
        stable_marketing_version,
        python_stable["release_version"],
        "Python stable marketing version",
    )
    stable_release = python_stable["release_version"]
    stable_flavor = python_stable["artifact_flavor"]
    expect(
        python_stable.get("package_version"),
        stable_release,
        "Python stable package version",
    )
    expect(
        python_stable.get("tag"),
        f"python-v{stable_release}",
        "Python stable tag",
    )
    expect(
        python_stable.get("artifact"),
        f"RF_Map_Viewer-python-{stable_release}-{stable_flavor}-macos-arm64.zip",
        "Python stable artifact",
    )
    expect(
        python_stable.get("checksum"),
        f"SHA256SUMS-python-{stable_release}-{stable_flavor}.txt",
        "Python stable checksum",
    )
    expect(
        python_stable.get("windows_portable_artifact"),
        "RF_Map_Viewer-"
        f"python-{stable_release}-{stable_flavor}-windows-x64-portable.zip",
        "Python stable Windows portable artifact",
    )
    expect(
        python_stable.get("windows_installer_artifact"),
        "RF_Map_Viewer-"
        f"python-{stable_release}-{stable_flavor}-windows-x64-setup.exe",
        "Python stable Windows installer artifact",
    )
    expect(
        python_stable.get("windows_checksum"),
        f"SHA256SUMS-python-{stable_release}-{stable_flavor}-windows-x64.txt",
        "Python stable Windows checksum",
    )

    python_fm = component(manifest, "python_freemoving")
    expect(python_fm.get("channel"), "alpha", "Python FM channel")
    python_fm_core = semver_core(python_fm["release_version"])
    python_fm_offset = python_fm.get("feature_generation_offset")
    if not isinstance(python_fm_offset, int):
        raise ValueError("Python FM feature_generation_offset must be an integer")
    expect(
        python_fm_core[:2],
        (stable_core[0], stable_core[1] + python_fm_offset),
        "Python FM feature generation",
    )
    marketing_version = python_fm.get("marketing_version")
    if (
        not isinstance(marketing_version, str)
        or APPLE_VERSION_PATTERN.fullmatch(marketing_version) is None
    ):
        raise ValueError("Python FM marketing_version must be three integers")
    expect(
        python_fm.get("release_version"),
        f"{marketing_version}-{python_fm.get('prerelease')}",
        "Python FM release version",
    )
    python_release = python_fm["release_version"]
    python_flavor = python_fm["artifact_flavor"]
    expect(
        python_fm.get("tag"),
        f"python-v{python_release}",
        "Python FM tag",
    )
    expect(
        python_fm.get("artifact"),
        "Free_Moving_RF_Viewer-"
        f"python-{python_release}-{python_flavor}-macos-arm64.zip",
        "Python FM artifact",
    )
    expect(
        python_fm.get("checksum"),
        f"SHA256SUMS-python-{python_release}-{python_flavor}.txt",
        "Python FM checksum",
    )

    swift = component(manifest, "swift")
    expect(swift.get("channel"), "stable", "Swift channel")
    swift_offset = swift.get("feature_generation_offset")
    if not isinstance(swift_offset, int):
        raise ValueError("Swift feature_generation_offset must be an integer")
    expect(
        semver_core(swift["release_version"])[:2],
        (stable_core[0], stable_core[1] + swift_offset),
        "Swift feature generation",
    )
    swift_marketing_version = swift.get("marketing_version")
    if (
        not isinstance(swift_marketing_version, str)
        or APPLE_VERSION_PATTERN.fullmatch(swift_marketing_version) is None
    ):
        raise ValueError("Swift marketing_version must be three integers")
    expect(swift.get("tag"), f"swift-v{swift['release_version']}", "Swift tag")
    expect(
        swift.get("artifact"),
        f"RF_Map_Viewer-{swift['release_version']}-swift-macos-arm64.zip",
        "Swift artifact",
    )

    web = component(manifest, "web")
    expect(web.get("channel"), "stable", "Web channel")
    web_offset = web.get("feature_generation_offset")
    if not isinstance(web_offset, int):
        raise ValueError("Web feature_generation_offset must be an integer")
    expect(
        semver_core(web["release_version"])[:2],
        (stable_core[0], stable_core[1] + web_offset),
        "Web feature generation",
    )
    web_release = web["release_version"]
    web_flavor = web["artifact_flavor"]
    expect(web.get("tag"), f"web-v{web_release}", "Web tag")
    expect(
        web.get("artifact"),
        f"RF_Map_Viewer-{web_release}-{web_flavor}.tar.gz",
        "Web artifact",
    )
    expect(
        web.get("checksum"),
        f"SHA256SUMS-{web_flavor}-{web_release}.txt",
        "Web checksum",
    )


def verify_sources(root: Path, manifest: dict[str, Any]) -> None:
    python_stable = component(manifest, "python_stable")
    python_fm = component(manifest, "python_freemoving")
    swift = component(manifest, "swift")
    web = component(manifest, "web")

    expect(
        literal_assignment(root / "python/rfmapping_gui.py", "APP_VERSION"),
        python_stable["release_version"],
        "Python stable APP_VERSION",
    )
    expect(
        literal_assignment(root / "python/rfmapping_gui.py", "APP_EDITION"),
        python_stable["edition"],
        "Python stable APP_EDITION",
    )
    python_stable_env = root / "python/script/python_stable_macos_release.env"
    expect(
        shell_assignment(python_stable_env, "RF_MAPPING_APP_VERSION"),
        python_stable["marketing_version"],
        "Python stable macOS marketing version",
    )
    expect(
        shell_assignment(python_stable_env, "RF_MAPPING_PACKAGE_VERSION"),
        python_stable["package_version"],
        "Python stable package release version",
    )
    expect(
        shell_assignment(python_stable_env, "RF_MAPPING_APP_BUILD"),
        python_stable["build"],
        "Python stable macOS build",
    )
    expect(
        shell_assignment(python_stable_env, "RF_MAPPING_RELEASE_EDITION"),
        python_stable["edition"],
        "Python stable macOS release edition",
    )
    expect(
        shell_assignment(python_stable_env, "RF_MAPPING_RELEASE_FLAVOR"),
        python_stable["artifact_flavor"],
        "Python stable artifact flavor",
    )
    python_stable_windows = root / "python/script/build_python_stable_windows_app.ps1"
    for name, expected, label in (
        ("AppVersion", python_stable["release_version"], "Windows app version"),
        ("AppBuild", python_stable["build"], "Windows app build"),
        ("ReleaseEdition", python_stable["edition"], "Windows release edition"),
        (
            "ReleaseFlavor",
            python_stable["artifact_flavor"],
            "Windows artifact flavor",
        ),
        ("Architecture", "x64", "Windows release architecture"),
    ):
        expect(
            powershell_assignment(python_stable_windows, name),
            expected,
            f"Python stable {label}",
        )
    python_stable_inno = root / "python/packaging/windows/RFMapViewer.iss"
    expect(
        inno_define(python_stable_inno, "MyAppVersion"),
        python_stable["release_version"],
        "Python stable Inno Setup version",
    )
    expect(
        inno_define(python_stable_inno, "MyAppBuild"),
        python_stable["build"],
        "Python stable Inno Setup build",
    )
    expect(
        literal_assignment(root / "python/rfmapping_fm_gui.py", "APP_VERSION"),
        python_fm["marketing_version"],
        "Python FM APP_VERSION",
    )
    expect(
        literal_assignment(root / "python/rfmapping_fm_gui.py", "APP_PRERELEASE"),
        python_fm["prerelease"],
        "Python FM APP_PRERELEASE",
    )
    expect(
        literal_assignment(root / "python/rfmapping_fm_gui.py", "APP_EDITION"),
        python_fm["edition"],
        "Python FM APP_EDITION",
    )
    expect(
        toml_project_version(root / "python/pyproject.toml"),
        python_fm["package_version"],
        "Python FM package version",
    )
    python_env = root / "python/script/python_macos_release.env"
    expect(
        shell_assignment(python_env, "RF_MAPPING_APP_VERSION"),
        python_fm["marketing_version"],
        "Python macOS marketing version",
    )
    expect(
        shell_assignment(python_env, "RF_MAPPING_APP_PRERELEASE"),
        python_fm["prerelease"],
        "Python macOS prerelease",
    )
    expect(
        shell_assignment(python_env, "RF_MAPPING_PACKAGE_VERSION"),
        python_fm["package_version"],
        "Python package release version",
    )
    expect(
        shell_assignment(python_env, "RF_MAPPING_APP_BUILD"),
        python_fm["build"],
        "Python macOS build",
    )
    expect(
        shell_assignment(python_env, "RF_MAPPING_RELEASE_EDITION"),
        python_fm["edition"],
        "Python macOS release edition",
    )
    expect(
        shell_assignment(python_env, "RF_MAPPING_RELEASE_FLAVOR"),
        python_fm["artifact_flavor"],
        "Python artifact flavor",
    )

    swift_script = root / "swift/script/build_macos_app.sh"
    expect(
        shell_assignment(swift_script, "APP_VERSION"),
        swift["marketing_version"],
        "Swift marketing version",
    )
    expect(
        shell_assignment(swift_script, "APP_BUILD"),
        swift["build"],
        "Swift build",
    )

    expect(
        literal_assignment(root / "web/backend/rfmapping_web/app.py", "WEB_VERSION"),
        web["release_version"],
        "Web runtime version",
    )
    expect(
        toml_project_version(root / "web/pyproject.toml"),
        web["package_version"],
        "Web Python package version",
    )
    package = json.loads((root / "web/frontend/package.json").read_text(encoding="utf-8"))
    package_lock = json.loads(
        (root / "web/frontend/package-lock.json").read_text(encoding="utf-8")
    )
    expect(package.get("version"), web["package_version"], "Web npm version")
    expect(package_lock.get("version"), web["package_version"], "Web lock version")
    expect(
        package_lock["packages"][""].get("version"),
        web["package_version"],
        "Web lock root package version",
    )
    web_env = root / "web/release.env"
    expect(
        shell_assignment(web_env, "RF_MAPPING_WEB_VERSION"),
        web["release_version"],
        "Web release version",
    )
    expect(
        shell_assignment(web_env, "RF_MAPPING_WEB_RELEASE_FLAVOR"),
        web["artifact_flavor"],
        "Web artifact flavor",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tag")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    manifest_path = root / "release/versions.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("release/versions.json must contain an object")
    verify_manifest(manifest)
    verify_sources(root, manifest)

    if args.tag is not None:
        expected_tags = {
            component(manifest, name)["tag"]
            for name in ("python_stable", "python_freemoving", "swift", "web")
        }
        if args.tag not in expected_tags:
            expected = ", ".join(sorted(expected_tags))
            raise ValueError(f"tag {args.tag!r} is not canonical; expected one of {expected}")

    versions = ", ".join(
        f"{name}={component(manifest, name)['release_version']}"
        for name in ("python_stable", "python_freemoving", "swift", "web")
    )
    if args.github_output is not None:
        python_stable = component(manifest, "python_stable")
        python_fm = component(manifest, "python_freemoving")
        swift = component(manifest, "swift")
        web = component(manifest, "web")
        outputs = {
            "python_stable_release": python_stable["release_version"],
            "python_stable_tag": python_stable["tag"],
            "python_stable_artifact": python_stable["artifact"],
            "python_stable_checksum": python_stable["checksum"],
            "python_stable_windows_portable_artifact": python_stable[
                "windows_portable_artifact"
            ],
            "python_stable_windows_installer_artifact": python_stable[
                "windows_installer_artifact"
            ],
            "python_stable_windows_checksum": python_stable["windows_checksum"],
            "python_fm_release": python_fm["release_version"],
            "python_fm_tag": python_fm["tag"],
            "python_fm_artifact": python_fm["artifact"],
            "python_fm_checksum": python_fm["checksum"],
            "swift_release": swift["release_version"],
            "swift_tag": swift["tag"],
            "swift_artifact": swift["artifact"],
            "web_release": web["release_version"],
            "web_tag": web["tag"],
            "web_artifact": web["artifact"],
            "web_checksum": web["checksum"],
        }
        with args.github_output.open("a", encoding="utf-8") as stream:
            for key, value in outputs.items():
                stream.write(f"{key}={value}\n")
    print(f"component versions verified: {versions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
