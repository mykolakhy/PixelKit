from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QSettings
from pixelkit.presets import Preset, PresetStore, preset_name


class PresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "preferences.ini")
        self.settings = QSettings(self.path, QSettings.Format.IniFormat)
        self.store = PresetStore(self.settings)

    def test_presets_round_trip_in_a_new_settings_instance_without_paths(self):
        preset = Preset(width=640, height=480, keep_ratio=False, quality=91, strip_metadata=False, background="white", output_format="JPG")
        self.store.save({"Каталог / 2026": preset})
        reopened = PresetStore(QSettings(self.path, QSettings.Format.IniFormat))
        self.assertEqual(reopened.load(), {"Каталог / 2026": preset})
        self.assertNotIn("output_path", asdict(preset))

    def test_corrupt_preferences_fall_back_and_are_backed_up_on_save(self):
        for raw in ("not JSON", '{"version":2,"presets":{}}', '{"version":1,"presets":{"Bad":{"quality":500}}}'):
            with self.subTest(raw=raw):
                self.settings.setValue(self.store.KEY, raw)
                self.assertEqual(self.store.load(), {})
                self.assertTrue(self.store.load_error)
                self.store.save({"Recovered": Preset()})
                self.assertEqual(self.settings.value("presets/recovery_backup"), raw)
                self.assertEqual(self.store.load(), {"Recovered": Preset()})

    def test_dimensions_quality_flags_and_names_are_validated(self):
        for values in ({"width":0}, {"height":100001}, {"longest_side":True}, {"quality":9}, {"quality":101}, {"keep_ratio":1}, {"output_format":"EXE"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Preset(**values)
        for name in ("", " ", "For WEBSITE", "Custom settings", "bad\nname", "x" * 61):
            with self.subTest(name=name), self.assertRaises(ValueError):
                preset_name(name)
        self.assertEqual(preset_name("  Мій пресет  "), "Мій пресет")

    def test_unwritable_preferences_report_failure(self):
        with patch.object(self.settings, "isWritable", return_value=False), self.assertRaises(OSError):
            self.store.save({"My preset": Preset()})
        self.assertEqual(self.store.load(), {})


if __name__ == "__main__":
    unittest.main()
