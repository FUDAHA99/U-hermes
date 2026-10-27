"""Run offline workbench JS interaction contracts with portable Node or PATH Node."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

HERE = Path(__file__).resolve().parent
PORTABLE = HERE.parent.parent


class RecruitUITests(unittest.TestCase):
    def test_workbench_interactions(self):
        candidates = [PORTABLE / "runtime/node/node.exe"]
        candidates.extend((PORTABLE / "runtime").glob("node-*/bin/node"))
        candidates.extend((PORTABLE / "runtime").glob("node-*/node.exe"))
        node = next((str(path) for path in candidates if path.is_file()), None) or shutil.which("node")
        if not node:
            if os.environ.get("CI"):
                self.fail("Node is required in CI for recruitment workbench contracts")
            self.skipTest("Node unavailable; JS interaction contracts not run")
        completed = subprocess.run([node, str(HERE / "recruit_ui_contract.js")],
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("8 interaction contracts passed", completed.stdout)
        print(completed.stdout, end="")


if __name__ == "__main__":
    unittest.main()
