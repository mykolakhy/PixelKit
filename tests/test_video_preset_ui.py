from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings

from pixelkit.app import ImageMagickStudio, PixelKitApplication
from pixelkit.presets import Preset, PresetStore
from pixelkit.video import VideoSettings


class VideoPresetUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.settings_path = self.root / "presets.ini"
        self.windows = []
        self.addCleanup(self.close_windows)
        message_patch = patch("pixelkit.app.ImageMagickStudio._show_message")
        self.message = message_patch.start()
        self.addCleanup(message_patch.stop)
        self.window = self.make_window()
        self.panel = self.window.video_panel

    def make_window(self):
        settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        with patch("pixelkit.app.find_magick", return_value="magick"), patch("pixelkit.video_panel.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video_panel.find_ffprobe", return_value="ffprobe"):
            window = ImageMagickStudio(PresetStore(settings))
        self.windows.append(window)
        return window

    def close_windows(self):
        for window in self.windows:
            for worker in (window.worker, window.video_panel.worker):
                if isinstance(worker, Mock):
                    worker.isRunning.return_value = False
            window.video_panel.processing = False
            window.processing = False
            window.close()

    def video(self, name):
        source = self.root / name
        source.write_bytes(b"video fixture")
        return source

    def configure(self, settings, panel=None):
        panel = panel or self.panel
        panel.preset_combo.setCurrentIndex(panel.preset_combo.findData(settings.preset))
        panel.resolution_combo.setCurrentIndex(panel.resolution_combo.findData(settings.max_height))
        panel.audio_combo.setCurrentIndex(panel.audio_combo.findData(settings.audio))
        panel.target_size_check.setChecked(settings.target_bytes is not None)
        if settings.target_bytes is not None:
            panel.target_size_edit.setText(f"{settings.target_bytes / 1_000_000:g}")

    def save(self, name, panel=None):
        panel = panel or self.panel
        with patch.object(panel, "_ask_preset_name", return_value=name):
            panel._save_preset()

    def seed(self, presets):
        self.panel.video_preset_store.save(presets)
        self.panel.custom_presets = dict(presets)
        self.panel._refresh_presets()

    def select(self, name, panel=None):
        panel = panel or self.panel
        index = panel.saved_preset_combo.findData(name)
        self.assertGreaterEqual(index, 0, f"Missing video preset: {name}")
        panel.saved_preset_combo.setCurrentIndex(index)

    def test_save_and_reopen_restore_every_setting_with_exact_decimal_size(self):
        expected = VideoSettings("small", 720, "remove", 1_001_000)
        self.configure(expected)
        self.save("Для поширення")
        self.assertEqual(self.panel.custom_presets, {"Для поширення": expected})
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Для поширення")

        reopened = self.make_window().video_panel
        self.assertEqual(reopened.custom_presets, {"Для поширення": expected})
        self.select("Для поширення", reopened)
        self.assertEqual(reopened._current_settings(), expected)
        self.assertEqual(reopened.saved_preset_combo.currentData(), "Для поширення")
        self.assertTrue(reopened.target_size_check.isChecked())
        self.assertTrue(reopened.target_size_edit.isEnabled())

    def test_image_and_video_presets_with_same_name_remain_independent(self):
        image = Preset(quality=90, output_format="PNG")
        self.window.preset_store.save({"Для поширення": image})
        video = VideoSettings("high", 1080, "keep", 25_000_000)
        self.configure(video)
        self.save("Для поширення")
        reopened = self.make_window()
        self.assertEqual(reopened.custom_presets, {"Для поширення": image})
        self.assertEqual(reopened.video_panel.custom_presets, {"Для поширення": video})

    def test_apply_changes_settings_without_touching_queue_output_or_starting_processing(self):
        expected = VideoSettings("small", 720, "remove", 500_000)
        self.seed({"Short clips": expected})
        source = self.video("clip.mov")
        self.panel.set_sources([source])
        output = str(self.root / "chosen.mp4")
        self.panel.output_edit.setText(output)
        with patch("pixelkit.video_panel.VideoWorker") as worker:
            self.select("Short clips")
        self.assertEqual(self.panel._current_settings(), expected)
        self.assertEqual(self.panel.sources, [source.resolve()])
        self.assertEqual(self.panel.output_edit.text(), output)
        self.assertFalse(self.panel.processing)
        worker.assert_not_called()

    def test_batch_worker_receives_selected_settings_for_all_videos(self):
        expected = VideoSettings("high", 1080, "keep", 2_500_000)
        self.seed({"Deliverables": expected})
        files = [self.video("first.mov"), self.video("second.mp4")]
        self.panel.set_sources(files)
        self.panel.output_edit.setText(str(self.root / "exports"))
        self.select("Deliverables")
        worker = Mock()
        worker.isRunning.return_value = False
        with patch("pixelkit.video_panel.VideoWorker", return_value=worker) as construct:
            self.panel.start_processing()
        jobs, _, settings = construct.call_args.args[:3]
        self.assertEqual([source for source, _ in jobs], [path.resolve() for path in files])
        self.assertEqual(settings, expected)
        worker.start.assert_called_once()

    def test_manual_changes_leave_named_selection_without_modifying_saved_settings(self):
        expected = VideoSettings("small", 720, "remove", 2_500_000)
        self.seed({"Saved": expected})
        edits = (
            lambda: self.panel.preset_combo.setCurrentIndex(self.panel.preset_combo.findData("high")),
            lambda: self.panel.resolution_combo.setCurrentIndex(self.panel.resolution_combo.findData(1080)),
            lambda: self.panel.audio_combo.setCurrentIndex(self.panel.audio_combo.findData("keep")),
            lambda: self.panel.target_size_edit.setText("1.001"),
            lambda: self.panel.target_size_check.setChecked(False),
        )
        for edit in edits:
            with self.subTest(edit=edit):
                self.select("Saved")
                edit()
                self.assertIsNone(self.panel.saved_preset_combo.currentData())
                self.assertEqual(self.panel.custom_presets["Saved"], expected)

    def test_inactive_target_entry_does_not_make_an_unlimited_preset_dirty(self):
        expected = VideoSettings("high", 1080, "keep")
        self.seed({"No cap": expected})
        self.select("No cap")
        self.panel.target_size_edit.clear()
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "No cap")
        self.assertEqual(self.panel._current_settings(), expected)
        self.panel.target_size_check.setChecked(True)
        self.assertIsNone(self.panel.saved_preset_combo.currentData())

    def test_switching_to_unlimited_preset_clears_enabled_limit_and_accepts_unfinished_text(self):
        limited = VideoSettings("small", 720, "remove", 500_000)
        unlimited = VideoSettings("high", 1080, "keep")
        self.seed({"Limited": limited, "Unlimited": unlimited})
        self.select("Limited")
        self.panel.target_size_edit.clear()
        self.select("Unlimited")
        self.assertEqual(self.panel._current_settings(), unlimited)
        self.assertFalse(self.panel.target_size_check.isChecked())
        self.assertFalse(self.panel.target_size_edit.isEnabled())
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Unlimited")

    def test_cancel_save_and_decline_casefold_overwrite_preserve_saved_data(self):
        original = VideoSettings("small", 720, "remove", 500_000)
        self.seed({"Delivery": original})
        self.select("Delivery")
        with patch.object(self.panel, "_ask_preset_name", return_value=None), patch.object(self.panel.video_preset_store, "save") as save:
            self.panel._save_preset()
        save.assert_not_called()
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Delivery")

        self.configure(VideoSettings("high", 1080, "keep"))
        with patch.object(self.panel, "_ask_preset_name", return_value="DELIVERY"), patch.object(self.panel, "_confirm", return_value=False) as confirm:
            self.panel._save_preset()
        confirm.assert_called_once()
        self.assertEqual(self.panel.custom_presets, {"Delivery": original})
        reopened = self.make_window().video_panel
        self.assertEqual(reopened.custom_presets, {"Delivery": original})

    def test_confirmed_casefold_overwrite_replaces_one_preset_and_survives_restart(self):
        self.seed({"Delivery": VideoSettings("small", 720, "remove", 500_000)})
        expected = VideoSettings("high", 1080, "keep", 1_001_000)
        self.configure(expected)
        with patch.object(self.panel, "_ask_preset_name", return_value="DELIVERY"), patch.object(self.panel, "_confirm", return_value=True) as confirm:
            self.panel._save_preset()
        confirm.assert_called_once()
        self.assertEqual(len(self.panel.custom_presets), 1)
        name, actual = next(iter(self.panel.custom_presets.items()))
        self.assertEqual(name.casefold(), "delivery")
        self.assertEqual(actual, expected)
        self.assertEqual(self.panel.saved_preset_combo.currentData(), name)
        self.assertEqual(self.make_window().video_panel.custom_presets, self.panel.custom_presets)

    def test_invalid_active_limit_cannot_be_saved_but_unlimited_can(self):
        self.panel.target_size_check.setChecked(True)
        self.panel.target_size_edit.clear()
        with patch.object(self.panel, "_ask_preset_name", return_value="Bad limit"), patch.object(self.panel.video_preset_store, "save") as save:
            self.panel._save_preset()
        save.assert_not_called()
        self.assertEqual(self.panel.custom_presets, {})
        self.message.assert_called()
        self.panel.target_size_check.setChecked(False)
        self.save("No limit")
        self.assertIsNone(self.panel.custom_presets["No limit"].target_bytes)

    def test_rename_keeps_settings_and_selection_and_persists_new_name(self):
        expected = VideoSettings("small", 720, "remove", 500_000)
        self.seed({"Old name": expected})
        self.select("Old name")
        with patch.object(self.panel, "_ask_preset_name", return_value="Нова назва"):
            self.panel._rename_preset()
        self.assertEqual(self.panel.custom_presets, {"Нова назва": expected})
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Нова назва")
        self.assertEqual(self.panel._current_settings(), expected)
        self.assertEqual(self.make_window().video_panel.custom_presets, {"Нова назва": expected})

    def test_rename_cancel_and_identical_name_do_not_write_or_ask_to_replace(self):
        expected = VideoSettings("small", 720, "remove", 500_000)
        self.seed({"Saved": expected})
        self.select("Saved")
        for answer in (None, "Saved"):
            with self.subTest(answer=answer), patch.object(self.panel, "_ask_preset_name", return_value=answer), patch.object(self.panel.video_preset_store, "save") as save, patch.object(self.panel, "_confirm") as confirm:
                self.panel._rename_preset()
                save.assert_not_called()
                confirm.assert_not_called()
        self.assertEqual(self.panel.custom_presets, {"Saved": expected})
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Saved")

    def test_rename_collision_requires_confirmation_and_preserves_source_settings(self):
        source = VideoSettings("small", 720, "remove", 500_000)
        target = VideoSettings("high", 1080, "keep")
        presets = {"Source": source, "Target": target}
        self.seed(presets)
        self.select("Source")
        with patch.object(self.panel, "_ask_preset_name", return_value="TARGET"), patch.object(self.panel, "_confirm", return_value=False) as confirm:
            self.panel._rename_preset()
        confirm.assert_called_once()
        self.assertEqual(self.panel.custom_presets, presets)
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Source")
        with patch.object(self.panel, "_ask_preset_name", return_value="TARGET"), patch.object(self.panel, "_confirm", return_value=True) as confirm:
            self.panel._rename_preset()
        confirm.assert_called_once()
        self.assertEqual(self.panel.custom_presets, {"TARGET": source})
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "TARGET")
        self.assertEqual(self.panel._current_settings(), source)
        self.assertEqual(self.make_window().video_panel.custom_presets, {"TARGET": source})

    def test_delete_requires_confirmation_and_keeps_current_settings(self):
        expected = VideoSettings("small", 720, "remove", 500_000)
        self.seed({"Saved": expected})
        self.select("Saved")
        with patch.object(self.panel, "_confirm", return_value=False):
            self.panel._delete_preset()
        self.assertEqual(self.panel.custom_presets, {"Saved": expected})
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Saved")
        with patch.object(self.panel, "_confirm", return_value=True):
            self.panel._delete_preset()
        self.assertEqual(self.panel.custom_presets, {})
        self.assertIsNone(self.panel.saved_preset_combo.currentData())
        self.assertEqual(self.panel._current_settings(), expected)
        self.assertEqual(self.make_window().video_panel.custom_presets, {})

    def test_failed_save_keeps_existing_data_and_current_selection(self):
        expected = VideoSettings("small", 720, "remove", 500_000)
        self.seed({"Saved": expected})
        self.select("Saved")
        with patch.object(self.panel, "_ask_preset_name", return_value="Another"), patch.object(self.panel.video_preset_store, "save", side_effect=OSError("Read only")):
            self.panel._save_preset()
        self.assertEqual(self.panel.custom_presets, {"Saved": expected})
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Saved")
        self.assertEqual(self.panel._current_settings(), expected)
        self.assertIn("Read only", self.message.call_args.args[2])

    def test_failed_rename_and_delete_keep_existing_data_and_selection(self):
        expected = VideoSettings("small", 720, "remove", 500_000)
        self.seed({"Saved": expected})
        self.select("Saved")
        with patch.object(self.panel, "_ask_preset_name", return_value="New name"), patch.object(self.panel.video_preset_store, "save", side_effect=OSError("Read only")):
            self.panel._rename_preset()
        self.assertEqual(self.panel.custom_presets, {"Saved": expected})
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Saved")
        with patch.object(self.panel, "_confirm", return_value=True), patch.object(self.panel.video_preset_store, "save", side_effect=OSError("Read only")):
            self.panel._delete_preset()
        self.assertEqual(self.panel.custom_presets, {"Saved": expected})
        self.assertEqual(self.panel.saved_preset_combo.currentData(), "Saved")
        self.assertEqual(self.panel._current_settings(), expected)
        self.assertIn("Read only", self.message.call_args.args[2])

    def test_preset_controls_and_direct_mutations_are_locked_during_processing(self):
        expected = VideoSettings("small", 720, "remove", 500_000)
        self.seed({"Saved": expected})
        self.select("Saved")
        self.panel._set_busy(True)
        for control in (self.panel.saved_preset_combo, self.panel.save_preset_button, self.panel.rename_preset_button, self.panel.delete_preset_button):
            self.assertFalse(control.isEnabled())
        with patch.object(self.panel, "_ask_preset_name") as ask, patch.object(self.panel, "_confirm") as confirm, patch.object(self.panel.video_preset_store, "save") as save:
            self.panel._save_preset()
            self.panel._rename_preset()
            self.panel._delete_preset()
        ask.assert_not_called()
        confirm.assert_not_called()
        save.assert_not_called()
        self.assertEqual(self.panel.custom_presets, {"Saved": expected})
        self.panel._set_busy(False)
        for control in (self.panel.saved_preset_combo, self.panel.save_preset_button, self.panel.rename_preset_button, self.panel.delete_preset_button):
            self.assertTrue(control.isEnabled())

    def test_manual_selection_disables_rename_delete_and_their_methods_are_noops(self):
        self.assertIsNone(self.panel.saved_preset_combo.currentData())
        self.assertTrue(self.panel.save_preset_button.isEnabled())
        self.assertFalse(self.panel.rename_preset_button.isEnabled())
        self.assertFalse(self.panel.delete_preset_button.isEnabled())
        with patch.object(self.panel, "_ask_preset_name") as ask, patch.object(self.panel, "_confirm") as confirm, patch.object(self.panel.video_preset_store, "save") as save:
            self.panel._rename_preset()
            self.panel._delete_preset()
        ask.assert_not_called()
        confirm.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
