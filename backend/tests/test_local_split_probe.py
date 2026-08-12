import stat
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from local_split_probe import (
    LocalSplitProbeError,
    ProbeOptions,
    _route_evidence,
    _validate_options,
    _write_private_json,
)


class LocalSplitProbeTests(unittest.TestCase):
    def test_default_options_define_two_nonempty_opt_ranges(self) -> None:
        options = ProbeOptions()

        total_layers = _validate_options(options)

        self.assertEqual(total_layers, 12)
        self.assertEqual(
            [(0, options.split_layer), (options.split_layer, total_layers)],
            [(0, 6), (6, 12)],
        )

    def test_split_layer_must_be_inside_model(self) -> None:
        for split_layer in (0, 12, 13):
            with self.subTest(split_layer=split_layer):
                with self.assertRaisesRegex(LocalSplitProbeError, "split_layer"):
                    _validate_options(ProbeOptions(split_layer=split_layer))

    def test_route_evidence_keeps_only_acceptance_fields(self) -> None:
        route = _route_evidence(
            [
                {
                    "peer_id": "head",
                    "rpc_uid": "prefix.0.6",
                    "layer_start": 0,
                    "layer_end": 6,
                    "model_name": "facebook/opt-125m",
                    "maddrs": ["/ip4/127.0.0.1/tcp/1234"],
                }
            ]
        )

        self.assertEqual(route[0]["peer_id"], "head")
        self.assertNotIn("maddrs", route[0])

    def test_evidence_file_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "split.json"

            _write_private_json(output, {"ok": True})

            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
