from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from arcgis_pro_mcp.private_state import (
    MAX_PRIVATE_STATE_BYTES,
    private_file_is_trusted,
    read_private_json,
    remove_private_json_if,
    write_private_json,
)


class PrivateStateTests(unittest.TestCase):
    def test_private_state_round_trip_and_identity_bound_removal(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=workspace) as root:
            path = Path(root) / "private" / "state.json"
            payload = {"session_id": "session-a", "token": "secret", "port": 17865}
            write_private_json(path, payload, temp_tag="session-a")
            self.assertTrue(private_file_is_trusted(path))
            self.assertEqual(read_private_json(path), payload)

            remove_private_json_if(path, "session_id", "different")
            self.assertTrue(path.exists())
            remove_private_json_if(path, "session_id", "session-a")
            self.assertFalse(path.exists())

    def test_untrusted_and_oversized_files_fail_closed(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=workspace) as root:
            path = Path(root) / "state.json"
            path.write_bytes(b"x" * (MAX_PRIVATE_STATE_BYTES + 1))
            self.assertEqual(read_private_json(path), {})


if __name__ == "__main__":
    unittest.main()
