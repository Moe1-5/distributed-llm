import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from api.env_loader import load_project_env


class EnvironmentLoaderTests(unittest.TestCase):
    def test_managed_xdg_environment_precedes_checkout_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            managed = Path(tmp) / "distribllm" / "backend.env"
            managed.parent.mkdir(parents=True)
            managed.write_text(
                "DISTRIBLLM_INCENTIVES_MODE=shadow\nDISTRIBLLM_TEST_VALUE=managed\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": tmp, "DISTRIBLLM_TEST_VALUE": "existing"},
                clear=True,
            ):
                loaded = load_project_env()

                self.assertEqual(loaded, managed)
                self.assertEqual(os.environ["DISTRIBLLM_INCENTIVES_MODE"], "shadow")
                self.assertEqual(os.environ["DISTRIBLLM_TEST_VALUE"], "existing")

    def test_explicit_environment_file_supports_managed_operator_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "participant.env"
            explicit.write_text(
                "DISTRIBLLM_NETWORK_MODE='relay'\n# ignored\nINVALID_LINE\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"DISTRIBLLM_ENV_FILE": str(explicit)},
                clear=True,
            ):
                loaded = load_project_env()

                self.assertEqual(loaded, explicit)
                self.assertEqual(os.environ["DISTRIBLLM_NETWORK_MODE"], "relay")


if __name__ == "__main__":
    unittest.main()
