from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QSettings

from pixelkit.presets import Preset, PresetStore
from pixelkit.video import VideoSettings
from pixelkit.video_presets import VideoPresetStore, video_preset_description, video_preset_name


class VideoPresetStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "preferences.ini")
        self.settings = QSettings(self.path, QSettings.Format.IniFormat)
        self.store = VideoPresetStore(self.settings)

    def write_payload(self, presets, version=1):
        self.settings.setValue(self.store.KEY, json.dumps({"version": version, "presets": presets}))

    def test_video_presets_round_trip_after_reopening_without_paths(self):
        expected = {
            "Для поширення": VideoSettings("small", 720, "compress", 25_000_000),
            "Кліп без звуку 🎬": VideoSettings("high", 1080, "remove", 1_001_000),
            "Оригінальна роздільність": VideoSettings("balanced", 0, "keep"),
        }
        self.store.save(expected)
        reopened = VideoPresetStore(QSettings(self.path, QSettings.Format.IniFormat))
        self.assertEqual(reopened.load(), expected)
        self.assertFalse(reopened.load_error)
        for values in json.loads(self.settings.value(self.store.KEY))["presets"].values():
            self.assertEqual(set(values), {"preset", "max_height", "audio", "target_bytes"})

    def test_video_presets_are_isolated_from_image_presets_and_backups(self):
        images = PresetStore(self.settings)
        images.save({"Shared name": Preset(quality=80, output_format="WEBP")})
        image_raw = self.settings.value(images.KEY)
        self.settings.setValue("presets/recovery_backup", "old image preferences")
        self.store.save({"Shared name": VideoSettings("small", 720, "remove")})
        self.assertEqual(self.settings.value(images.KEY), image_raw)
        self.assertEqual(self.settings.value("presets/recovery_backup"), "old image preferences")
        images.save({"New image preset": Preset()})
        self.assertEqual(self.store.load(), {"Shared name": VideoSettings("small", 720, "remove")})

    def test_names_trim_and_reject_controls_reserved_names_and_duplicates(self):
        self.assertEqual(video_preset_name("  Мій пресет 🎬  "), "Мій пресет 🎬")
        self.assertEqual(video_preset_name("x" * 60), "x" * 60)
        for name in (None, 123, "", "  ", "x" * 61, " CUSTOM SETTINGS ", "bad\nname", "\tname\t", "bad\x7fname", "bad\x85name"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                video_preset_name(name)
        self.store.save({"  Для поширення  ": VideoSettings()})
        self.assertEqual(set(self.store.load()), {"Для поширення"})
        for presets in ({"Share": VideoSettings(), " SHARE ": VideoSettings()},
                        {"Straße": VideoSettings(), "STRASSE": VideoSettings()}):
            with self.subTest(presets=presets), self.assertRaises(ValueError):
                self.store.save(presets)
        self.assertEqual(set(self.store.load()), {"Для поширення"})

    def test_targets_match_decimal_ui_range_and_precision(self):
        for target in (1000, 1_001_000, 25_000_000, 1_000_000_000_000):
            with self.subTest(target=target):
                self.store.save({"Limit": VideoSettings(target_bytes=target)})
                self.assertEqual(self.store.load()["Limit"].target_bytes, target)
        for target in (1, 999, 1001, 1_000_000_001_000):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.store.save({"Invalid limit": VideoSettings(target_bytes=target)})
        self.assertEqual(self.store.load()["Limit"].target_bytes, 1_000_000_000_000)

    def test_corruption_is_reported_and_backed_up_on_recovery(self):
        corrupt_values = (
            "not JSON", "null", "[]", "0", "false",
            '{"version":2,"presets":{}}', '{"version":true,"presets":{}}',
            '{"version":1,"presets":[]}', '{"version":1,"presets":{},"extra":1}',
            '{"version":1,"presets":{"Bad":{"preset":"unknown"}}}',
            '{"version":1,"presets":{"Bad":{"preset":1}}}',
            '{"version":1,"presets":{"Bad":{"max_height":true}}}',
            '{"version":1,"presets":{"Bad":{"max_height":720.0}}}',
            '{"version":1,"presets":{"Bad":{"max_height":2160}}}',
            '{"version":1,"presets":{"Bad":{"audio":"unknown"}}}',
            '{"version":1,"presets":{"Bad":{"audio":1}}}',
            '{"version":1,"presets":{"Bad":{"target_bytes":true}}}',
            '{"version":1,"presets":{"Bad":{"target_bytes":1000.0}}}',
            '{"version":1,"presets":{"Bad":{"target_bytes":1001}}}',
            '{"version":1,"presets":{"Bad":{"target_bytes":1000000001000}}}',
            '{"version":1,"presets":{"Bad":{"output_path":"/tmp/file.mp4"}}}',
            '{"version":1,"presets":{"Bad":null}}',
            '{"version":1,"presets":{"Name":{},"name":{}}}',
            '{"version":1,"presets":{"Name":{},"Name":{}}}',
            '{"version":1,"presets":{"Custom settings":{}}}',
            '{"version":1,"presets":{"Bad":{"audio":"keep","audio":"remove"}}}',
        )
        for raw in corrupt_values:
            with self.subTest(raw=raw):
                self.settings.setValue(self.store.KEY, raw)
                self.assertEqual(self.store.load(), {})
                self.assertTrue(self.store.load_error)
                self.store.save({"Recovered": VideoSettings()})
                self.assertEqual(self.settings.value(self.store.BACKUP_KEY), raw)
                self.assertEqual(self.store.load(), {"Recovered": VideoSettings()})
                self.assertFalse(self.store.load_error)

    def test_deeply_nested_corrupt_json_recovers_without_startup_failure(self):
        raw = "[" * 10000 + "0" + "]" * 10000
        self.settings.setValue(self.store.KEY, raw)
        self.assertEqual(self.store.load(), {})
        self.assertTrue(self.store.load_error)
        self.assertEqual(self.settings.value(self.store.KEY), raw)
        recovered = {"Recovered": VideoSettings("small", 720, "remove", 25_000_000)}
        self.store.save(recovered)
        reopened = VideoPresetStore(QSettings(self.path, QSettings.Format.IniFormat))
        self.assertEqual(reopened.load(), recovered)
        self.assertFalse(reopened.load_error)
        self.assertEqual(reopened.settings.value(self.store.BACKUP_KEY), raw)

    def test_empty_missing_optional_fields_and_non_text_storage(self):
        self.assertEqual(self.store.load(), {})
        self.assertFalse(self.store.load_error)
        self.write_payload({"Defaults": {}})
        self.assertEqual(self.store.load(), {"Defaults": VideoSettings()})
        for value in (False, 17, ["invalid"]):
            with self.subTest(value=value):
                self.settings.setValue(self.store.KEY, value)
                self.assertEqual(self.store.load(), {})
                self.assertTrue(self.store.load_error)
        self.settings.remove(self.store.KEY)
        self.assertEqual(self.store.load(), {})
        self.assertFalse(self.store.load_error)

    def test_invalid_save_does_not_touch_preferences_or_corruption_backup(self):
        self.settings.setValue(self.store.KEY, "broken")
        self.settings.setValue(self.store.BACKUP_KEY, "earlier backup")
        self.store.load()
        for presets in (None, [], {"Name": {}}, {"Custom settings": VideoSettings()},
                        {"Name": VideoSettings(), "name": VideoSettings()}):
            with self.subTest(presets=presets), self.assertRaises(ValueError):
                self.store.save(presets)
            self.assertEqual(self.settings.value(self.store.KEY), "broken")
            self.assertEqual(self.settings.value(self.store.BACKUP_KEY), "earlier backup")
            self.assertTrue(self.store.load_error)

    def test_unwritable_preferences_report_failure_without_changes(self):
        self.store.save({"Existing": VideoSettings()})
        original = self.settings.value(self.store.KEY)
        with patch.object(self.settings, "isWritable", return_value=False), self.assertRaises(OSError) as failure:
            self.store.save({"New": VideoSettings("small", 720, "remove")})
        self.assertNotIn("Restart", str(failure.exception))
        self.assertEqual(self.settings.value(self.store.KEY), original)

    def test_failed_sync_restores_cached_presets_and_backup(self):
        self.store.save({"Existing": VideoSettings()})
        original = self.settings.value(self.store.KEY)
        self.settings.setValue(self.store.BACKUP_KEY, "existing backup")
        self.store.load_error = True
        with patch.object(self.settings, "status", return_value=QSettings.Status.AccessError), self.assertRaisesRegex(OSError, "Restart PixelKit and try again"):
            self.store.save({"New": VideoSettings("small", 720, "remove")})
        self.assertEqual(self.settings.value(self.store.KEY), original)
        self.assertEqual(self.settings.value(self.store.BACKUP_KEY), "existing backup")
        self.assertTrue(self.store.load_error)

    def test_failed_initial_sync_removes_new_cached_keys(self):
        with patch.object(self.settings, "status", return_value=QSettings.Status.AccessError), self.assertRaises(OSError):
            self.store.save({"New": VideoSettings()})
        self.assertFalse(self.settings.contains(self.store.KEY))
        self.assertFalse(self.settings.contains(self.store.BACKUP_KEY))

    def test_descriptions_show_quality_resolution_audio_and_exact_limit(self):
        self.assertEqual(video_preset_description(VideoSettings()), "Balanced • Original resolution • Compress audio")
        self.assertEqual(video_preset_description(VideoSettings("high", 1080, "keep", 25_000_000)),
                         "High quality • 1080p • Keep audio • ≤ 25 MB")
        self.assertEqual(video_preset_description(VideoSettings("small", 720, "remove", 1_001_000)),
                         "Smallest file • 720p • No audio • ≤ 1.001 MB")
        self.assertTrue(video_preset_description(VideoSettings(target_bytes=1000)).endswith("≤ 0.001 MB"))
        self.assertTrue(video_preset_description(VideoSettings(target_bytes=1_000_000_000_000)).endswith("≤ 1000000 MB"))


if __name__ == "__main__":
    unittest.main()
