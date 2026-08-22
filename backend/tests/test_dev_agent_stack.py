from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts/dev_agent_stack.py"
SPEC = importlib.util.spec_from_file_location("dev_agent_stack", SCRIPT_PATH)
assert SPEC and SPEC.loader
dev_agent_stack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev_agent_stack)


class DevAgentStackTest(unittest.TestCase):
    def test_parse_project_id_uses_bootstrap_assignment(self) -> None:
        output = "ready: Quant Parameter Strategy\nNEXT_PUBLIC_AGENTOPS_PROJECT_ID=project-123\n"
        self.assertEqual(dev_agent_stack.parse_project_id(output), "project-123")

    def test_parse_project_id_rejects_missing_assignment(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "did not return"):
            dev_agent_stack.parse_project_id("ready: Quant Parameter Strategy\n")

    def test_find_coding_agent_repo_accepts_explicit_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = root / "coding_agent"
            script = checkout / "scripts/bootstrap_quant_integration.py"
            script.parent.mkdir(parents=True)
            script.touch()
            self.assertEqual(
                dev_agent_stack.find_coding_agent_repo(root / "quant", checkout),
                checkout.resolve(),
            )

    def test_owned_stack_requires_safe_marker_and_live_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "stack.json"
            state_path.write_text(
                json.dumps(
                    {
                        "controllerPid": os.getpid(),
                        "processPids": [os.getpid(), os.getpid(), os.getpid()],
                        "quantRepo": str(root),
                        "paperSchedulerEnabled": False,
                        "paperOrderSubmissionEnabled": False,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(dev_agent_stack, "endpoint_ready", return_value=True):
                self.assertTrue(dev_agent_stack.owned_stack_is_ready(state_path, root))
                self.assertFalse(dev_agent_stack.owned_stack_is_ready(state_path, root / "other"))

    def test_owned_stack_rejects_marker_with_dead_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "stack.json"
            state_path.write_text(
                json.dumps(
                    {
                        "controllerPid": 999_999_999,
                        "processPids": [os.getpid(), os.getpid(), os.getpid()],
                        "quantRepo": str(root),
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(dev_agent_stack.owned_stack_is_ready(state_path, root))


if __name__ == "__main__":
    unittest.main()
