import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CHECKER = Path(__file__).resolve().parents[1] / "check_repository_privacy.py"


class RepositoryPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Privacy test")
        self.git("config", "user.email", "test@users.noreply.github.com")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
        self.git("add", "--", name)

    def check(self, *args):
        return subprocess.run([sys.executable, str(CHECKER), *args], cwd=self.root, capture_output=True, text=True)

    def test_small_explicit_synthetic_fixture_is_allowed(self):
        self.write("python/tests/fixtures/release_smoke_rf.json", json.dumps({
            "fixture": "synthetic release smoke test", "unitsSpikeCounts": [[1]],
        }))
        self.assertEqual(self.check("--staged").returncode, 0)

    def test_renaming_rf_result_does_not_bypass_the_check(self):
        self.write("notes.json", json.dumps({"unitsSpikeCounts": [[1]]}))
        result = self.check("--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("RF result data", result.stderr)

    def test_deleted_data_remains_blocked_in_outgoing_history(self):
        self.write("data/results.json", json.dumps({"unitsSpikeCounts": [[1]]}))
        self.git("commit", "-qm", "add input")
        self.git("rm", "data/results.json")
        self.git("commit", "-qm", "remove input")
        self.assertEqual(self.check("--staged").returncode, 0)
        self.assertEqual(self.check("--history", "HEAD").returncode, 1)

    def test_environment_credentials_and_personal_paths_are_blocked(self):
        cases = {
            ".env.local": "SETTING=private\n",
            "client.py": "credential = " + repr("ghp_" + "0" * 36),
            "README.md": "/" + "home/private_person/recordings",
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                self.write(name, content)
                self.assertEqual(self.check("--staged").returncode, 1)
                self.git("rm", "--cached", name)

    def test_private_commit_email_is_blocked(self):
        self.git("config", "user.email", "person@example.com")
        self.write("README.md", "Source only\n")
        self.git("commit", "-qm", "source")
        result = self.check("--history", "HEAD")
        self.assertEqual(result.returncode, 1)
        self.assertIn("noreply", result.stderr)


if __name__ == "__main__":
    unittest.main()
