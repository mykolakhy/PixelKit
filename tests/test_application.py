from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QSettings
from PyQt6.QtWidgets import QMessageBox
from pixelkit.app import BatchWorker, ImageMagickStudio, PixelKitApplication
from pixelkit.runtime import find_magick, run_magick
from pixelkit.presets import BUILTIN_PRESETS, PresetStore
from pixelkit.report import BatchReport


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.preset_store = PresetStore(QSettings(str(self.root / "presets.ini"), QSettings.Format.IniFormat))
        with patch("pixelkit.app.find_magick", return_value="magick"):
            self.window = ImageMagickStudio(self.preset_store)
        self.addCleanup(self.window.close)

    def test_finder_open_events_are_queued_and_grouped(self):
        files = [self.root / "first.png", self.root / "second image.jpg"]
        for file in files:
            file.touch()
        opened = []
        self.app.files_opened.connect(opened.append)
        try:
            for file in files:
                # QFileOpenEvent is constructed by Qt and has no Python
                # constructor; supply its public interface here.
                event = Mock()
                event.type.return_value = QEvent.Type.FileOpen
                event.file.return_value = str(file)
                self.app.event(event)
            self.app.dispatch_open_files()
            self.assertEqual(opened, [files])
        finally:
            self.app.files_opened.disconnect(opened.append)

    def test_open_with_filters_files_and_does_not_replace_running_batch(self):
        image = self.root / "image.png"
        image.touch()
        other = self.root / "notes.txt"
        other.touch()
        self.window.open_files([image, other, self.root / "missing.jpg"])
        self.assertEqual(self.window.sources, [image.resolve()])
        with patch.object(self.window, "worker") as worker, patch.object(self.window, "_show_message") as message:
            worker.isRunning.return_value = True
            self.window.open_files([image])
            message.assert_called_once()

    def test_batch_processing_uses_spaces_and_preserves_aspect_ratio(self):
        magick = find_magick()
        if not magick:
            self.skipTest("ImageMagick 7 is needed for the integration check")
        self.window.magick = magick
        source = self.root / "input with spaces.png"
        run_magick([magick, "-size", "120x80", "xc:#7c5cff", str(source)], check=True)
        self.window._set_sources([source])
        self.window.resize_mode.setCurrentIndex(1)
        self.window.long_side_edit.setText("60")
        results = []
        jobs = []
        for extension in ("jpg", "png", "webp", "avif"):
            target = self.root / f"output with spaces.{extension}"
            jobs.append((self.window._build_command(source, target), target))
        worker = BatchWorker(jobs, self.root)
        worker.finished.connect(results.append)
        worker.run()
        self.assertEqual(len(results[0].successful), 4)
        self.assertEqual(len(results[0].files), 4)
        self.assertEqual(results[0].before, source.stat().st_size * 4)
        self.assertEqual(results[0].after, sum(target.stat().st_size for _, target in jobs))
        for _, target in jobs:
            result = run_magick([magick, "identify", "-format", "%wx%h", str(target)], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, "60x40")

    def test_source_image_cannot_be_overwritten(self):
        source = self.root / "original.png"
        source.touch()
        self.window._set_sources([source])
        self.window.output_edit.setText(str(source))
        with patch.object(self.window, "_show_message") as message:
            self.window._start_processing()
            self.assertEqual(message.call_args.args[0], QMessageBox.Icon.Critical)
            self.assertEqual(message.call_args.args[1], "Unsafe overwrite")
        self.assertIsNone(self.window.worker)

    def test_save_shortcut_cannot_start_a_second_running_batch(self):
        with patch.object(self.window, "worker") as worker, patch.object(self.window, "_show_message") as message:
            worker.isRunning.return_value = True
            self.window._start_processing()
            self.assertIs(self.window.worker, worker)
            message.assert_not_called()

    def test_inputs_are_locked_during_processing_and_restored_afterward(self):
        source = self.root / "image.png"
        source.touch()
        self.window._set_sources([source])
        self.assertTrue(self.window.process_button.isEnabled())
        self.window._set_processing_state(True)
        for widget in (self.window.source_list, self.window.width_edit, self.window.quality_slider, self.window.output_edit):
            self.assertFalse(widget.isEnabled())
        self.assertFalse(self.window.clear_button.isEnabled())
        self.assertFalse(self.window.process_button.isEnabled())
        self.window._set_processing_state(False)
        self.assertTrue(self.window.source_list.isEnabled())
        self.assertTrue(self.window.process_button.isEnabled())
        self.window._clear_sources()
        self.assertFalse(self.window.process_button.isEnabled())
        self.assertFalse(self.window.clear_button.isEnabled())

    def test_a_manually_edited_destination_survives_format_changes(self):
        source = self.root / "image.png"
        source.touch()
        self.window._set_sources([source])
        destination = str(self.root / "chosen-output.jpg")
        self.window.output_edit.setText(destination)
        self.window.output_edit.textEdited.emit(destination)
        self.window.format_combo.setCurrentText("JPG")
        self.assertEqual(self.window.output_edit.text(), destination)

    def test_cancel_button_requests_stop_and_restores_controls_after_report(self):
        self.window.worker = BatchWorker([], self.root)
        self.window._set_processing_state(True)
        self.assertFalse(self.window.cancel_button.isHidden())
        self.window.cancel_button.click()
        self.assertTrue(self.window.worker.cancel_event.is_set())
        self.assertFalse(self.window.cancel_button.isEnabled())
        self.assertFalse(self.window.width_edit.isEnabled())
        report = BatchReport((), self.root, cancelled=True)
        with patch("pixelkit.app.ReportDialog") as dialog:
            self.window._processing_finished(report)
            dialog.assert_called_once_with(report, self.window)
        self.assertTrue(self.window.cancel_button.isHidden())
        self.assertTrue(self.window.width_edit.isEnabled())
        self.assertIn("Cancelled", self.window.status_label.full_text)

    def test_builtin_preset_applies_to_all_batch_commands(self):
        files = [self.root / "first.png", self.root / "second.jpg"]
        for file in files:
            file.touch()
        self.window._set_sources(files)
        folder = str(self.root / "chosen folder")
        self.window.output_edit.setText(folder)
        self.window.output_edit.textEdited.emit(folder)
        self.window.preset_combo.setCurrentIndex(self.window.preset_combo.findData("builtin:For website"))
        self.assertEqual(self.window._current_preset(), BUILTIN_PRESETS["For website"])
        self.assertEqual(self.window.sources, [file.resolve() for file in files])
        self.assertEqual(self.window.output_edit.text(), folder)
        for source in files:
            self.assertEqual(self.window._selected_extension(source), "webp")
            command = self.window._build_command(source, self.root / f"{source.stem}.webp")
            self.assertIn("1920x1920", command)
            self.assertIn("-strip", command)
            self.assertEqual(command[command.index("-quality") + 1], "80")

    def test_preset_updates_single_file_extension_without_changing_folder_or_name(self):
        source = self.root / "source.png"
        source.touch()
        self.window._set_sources([source])
        self.window.output_edit.setText(str(self.root / "chosen name.png"))
        self.window.output_edit.textEdited.emit(self.window.output_edit.text())
        self.window.preset_combo.setCurrentIndex(self.window.preset_combo.findData("builtin:For email"))
        self.assertEqual(self.window.output_edit.text(), str(self.root / "chosen name.jpg"))

    def test_manual_edits_leave_named_preset_and_processing_locks_presets(self):
        self.window.preset_combo.setCurrentIndex(self.window.preset_combo.findData("builtin:For website"))
        self.window.quality_slider.setValue(77)
        self.assertIsNone(self.window.preset_combo.currentData())
        self.window._set_processing_state(True)
        self.assertFalse(self.window.preset_combo.isEnabled())
        self.assertFalse(self.window.save_preset_button.isEnabled())
        self.window._set_processing_state(False)
        self.assertTrue(self.window.preset_combo.isEnabled())

    def test_save_reload_replace_and_delete_custom_preset(self):
        self.window.width_edit.setText("640")
        self.window.height_edit.setText("480")
        self.window.keep_ratio.setChecked(False)
        self.window.strip_metadata.setChecked(False)
        self.window.background_edit.setText("#112233")
        self.window.format_combo.setCurrentText("JPG")
        expected = self.window._current_preset()
        with patch.object(self.window, "_ask_preset_name", return_value="Фото товарів"):
            self.window._save_preset()
        store = PresetStore(QSettings(str(self.root / "presets.ini"), QSettings.Format.IniFormat))
        with patch("pixelkit.app.find_magick", return_value="magick"):
            reopened = ImageMagickStudio(store)
        self.addCleanup(reopened.close)
        reopened.preset_combo.setCurrentIndex(reopened.preset_combo.findData("saved:Фото товарів"))
        self.assertEqual(reopened._current_preset(), expected)
        with patch.object(reopened, "_ask_preset_name", return_value="Фото товарів"), patch.object(reopened, "_confirm", return_value=False):
            reopened._save_preset()
        self.assertEqual(store.load()["Фото товарів"], expected)
        reopened.quality_slider.setValue(63)
        with patch.object(reopened, "_ask_preset_name", return_value="Фото товарів"), patch.object(reopened, "_confirm", return_value=True):
            reopened._save_preset()
        self.assertEqual(store.load()["Фото товарів"].quality, 63)
        with patch.object(reopened, "_confirm", return_value=False):
            reopened._delete_preset()
        self.assertIn("Фото товарів", store.load())
        with patch.object(reopened, "_confirm", return_value=True):
            reopened._delete_preset()
        self.assertEqual(store.load(), {})
        self.assertEqual(reopened.quality_slider.value(), 63)

    def test_failed_save_does_not_add_a_preset_and_cancel_is_a_noop(self):
        with patch.object(self.window, "_ask_preset_name", return_value=None), patch.object(self.preset_store, "save") as save:
            self.window._save_preset()
            save.assert_not_called()
        with patch.object(self.window, "_ask_preset_name", return_value="My preset"), patch.object(self.preset_store, "save", side_effect=OSError("Read only")), patch.object(self.window, "_show_message") as message:
            self.window._save_preset()
            message.assert_called_once()
        self.assertEqual(self.window.custom_presets, {})

    def test_invalid_preset_dimensions_are_not_saved(self):
        self.window.width_edit.setText("0")
        with patch.object(self.window, "_show_message") as message, patch.object(self.window, "_ask_preset_name") as ask:
            self.window._save_preset()
            message.assert_called_once()
            ask.assert_not_called()

    def test_target_size_rejects_empty_limit_and_unsupported_output_format(self):
        source = self.root / "input.png"
        source.touch()
        self.window._set_sources([source])
        self.window.target_size_check.setChecked(True)
        self.window.target_size_edit.clear()
        with patch.object(self.window, "_show_message") as message:
            self.window._start_processing()
            self.assertEqual(message.call_args.args[1], "Check file-size limit")
        self.window.target_size_edit.setText("500")
        with patch.object(self.window, "_show_message") as message:
            self.window._start_processing()
            self.assertEqual(message.call_args.args[1], "Choose a supported format")
        self.assertIsNone(self.window.worker)

    def test_target_limit_is_part_of_the_preset_and_locked_while_processing(self):
        self.window.target_size_check.setChecked(True)
        self.window.target_size_edit.setText("750")
        self.assertEqual(self.window._current_preset().target_kib, 750)
        self.window._set_processing_state(True)
        self.assertFalse(self.window.target_size_edit.isEnabled())
        self.window._set_processing_state(False)
        self.assertTrue(self.window.target_size_edit.isEnabled())


if __name__ == "__main__":
    unittest.main()
