import json
from pathlib import Path
import platform
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.bluetooth_identity import PROFILES, apply_identity, load_backup, restore_identity, validate_values


class MemoryRegistry:
    def __init__(self, values):
        self.values = dict(values)
        self.fail_next = False

    def read(self):
        return dict(self.values)

    def write(self, values):
        if self.fail_next:
            self.fail_next = False
            self.values["COD Major"] = values["COD Major"]
            raise OSError("simulated second DWORD failure")
        self.values = dict(values)


class BluetoothIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.backup = Path(self.directory.name) / "identity.json"
        self.original = {"COD Major": None, "COD Type": None}
        self.registry = MemoryRegistry(self.original)

    def test_apply_and_restore_preserve_absent_values(self):
        apply_identity(self.registry, self.backup, "keyboard")
        self.assertEqual(self.registry.read(), {"COD Major": 5, "COD Type": 16})
        restore_identity(self.registry, self.backup)
        self.assertEqual(self.registry.read(), self.original)

    def test_switching_profile_keeps_first_backup(self):
        apply_identity(self.registry, self.backup, "keyboard")
        original_backup = self.backup.read_bytes()
        apply_identity(self.registry, self.backup, "remote")
        self.assertEqual(self.backup.read_bytes(), original_backup)
        self.assertEqual(self.registry.read(), PROFILES["remote"])
        restore_identity(self.registry, self.backup)
        restore_identity(self.registry, self.backup)
        self.assertEqual(self.registry.read(), self.original)

    def test_existing_dwords_restored_exactly(self):
        self.registry.values = {"COD Major": 1, "COD Type": 3}
        apply_identity(self.registry, self.backup, "keyboard")
        restore_identity(self.registry, self.backup)
        self.assertEqual(self.registry.read(), {"COD Major": 1, "COD Type": 3})

    def test_partial_write_failure_rolls_back(self):
        self.registry.fail_next = True
        with self.assertRaises(OSError):
            apply_identity(self.registry, self.backup, "keyboard")
        self.assertEqual(self.registry.read(), self.original)
        self.assertEqual(load_backup(self.backup), self.original)

    def test_external_changes_are_not_overwritten(self):
        apply_identity(self.registry, self.backup, "keyboard")
        external = {"COD Major": 4, "COD Type": 0}
        self.registry.values = dict(external)
        for operation in (lambda: apply_identity(self.registry, self.backup, "remote"),
                          lambda: restore_identity(self.registry, self.backup)):
            with self.assertRaisesRegex(RuntimeError, "outside this tool"):
                operation()
            self.assertEqual(self.registry.read(), external)

    def test_malformed_or_foreign_backup_rejected(self):
        self.backup.write_text("[]")
        with self.assertRaises(ValueError):
            restore_identity(self.registry, self.backup)
        self.backup.write_text(json.dumps({"version": 1, "computer": "other-host", "original": self.original}))
        with self.assertRaises(ValueError):
            restore_identity(self.registry, self.backup)
        self.backup.write_text(json.dumps({"version": 1, "computer": platform.node(),
                                          "original": {"unrelated": 1}}))
        with self.assertRaises(ValueError):
            apply_identity(self.registry, self.backup, "keyboard")
        self.assertEqual(self.registry.read(), self.original)

    def test_backup_only_accepts_the_two_expected_dwords(self):
        for values in ({"COD Major": True, "COD Type": 0}, {"COD Major": -1, "COD Type": 0},
                       {"COD Major": 5, "COD Type": 2**32}, {**self.original, "other": 1}):
            with self.assertRaises(ValueError):
                validate_values(values)


if __name__ == "__main__":
    unittest.main()
