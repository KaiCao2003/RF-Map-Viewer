from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from verify_versions import verify_manifest, verify_sources


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "release/versions.json").read_text(encoding="utf-8"))


class ComponentVersionTests(unittest.TestCase):
    def test_manifest_and_all_source_declarations_match(self) -> None:
        verify_manifest(MANIFEST)
        verify_sources(ROOT, MANIFEST)

    def test_platform_letter_is_rejected_as_a_fourth_version_component(self) -> None:
        invalid = copy.deepcopy(MANIFEST)
        invalid["components"]["swift"]["release_version"] = "1.9.0.s"
        with self.assertRaisesRegex(ValueError, "must be SemVer"):
            verify_manifest(invalid)

    def test_one_feature_generation_behind_maps_to_1_8_series(self) -> None:
        candidate = copy.deepcopy(MANIFEST)
        swift = candidate["components"]["swift"]
        swift["release_version"] = "1.8.4"
        swift["marketing_version"] = "1.8.4"
        swift["feature_generation_offset"] = -1
        swift["artifact"] = "RF_Map_Viewer-1.8.4-swift-macos-arm64.zip"
        swift["tag"] = "swift-v1.8.4"
        verify_manifest(candidate)

    def test_only_exact_component_tags_are_accepted(self) -> None:
        verifier = ROOT / "release/verify_versions.py"
        for tag in (
            "python-v1.9.5",
            "python-v1.10.0-alpha.3",
            "swift-v1.9.5",
            "web-v1.9.5",
        ):
            subprocess.run(
                [sys.executable, str(verifier), "--tag", tag],
                check=True,
                capture_output=True,
                text=True,
            )
        rejected = subprocess.run(
            [sys.executable, str(verifier), "--tag", "python-v1.10.0.2"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("is not canonical", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
