from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QLocale, QSettings
from PyQt6.QtWidgets import QMessageBox, QScrollArea
from pixelkit.app import BatchWorker, ImageMagickStudio, PixelKitApplication
from pixelkit.runtime import find_magick, run_magick
from pixelkit.presets import BUILTIN_PRESETS, PresetStore
from pixelkit.report import BatchReport, FileResult


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

    def test_image_completion_surfaces_all_failed_and_mixed_results(self):
        good = FileResult(self.root / "good.png", self.root / "good.webp", 100, 40)
        bad = FileResult(self.root / "bad.png", self.root / "bad.webp", 100, None, "Could not convert this image")
        cases = (
            ((bad,), "Failed: 0 / 1 files processed successfully · 1 failed"),
            ((good, bad), "Completed: 1 / 2 files processed successfully · 1 failed"),
        )
        for files, expected in cases:
            with self.subTest(expected=expected), patch("pixelkit.app.ReportDialog") as dialog:
                report = BatchReport(files, self.root)
                self.window._processing_finished(report)
                self.assertEqual(self.window.status_label.full_text, expected)
                self.assertIs(self.window.last_report, report)
                dialog.assert_called_once_with(report, self.window)

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

    def test_invalid_active_dimensions_block_processing_before_creating_output(self):
        source = self.root / "source.png"
        source.touch()
        self.window._set_sources([source])
        destination = self.root / "not created" / "output.png"
        self.window.output_edit.setText(str(destination))
        for mode, edit, values in (
            (0, self.window.width_edit, ("0", "100001", "invalid")),
            (0, self.window.height_edit, ("0", "100001")),
            (1, self.window.long_side_edit, ("0", "100001")),
        ):
            self.window.resize_mode.setCurrentIndex(mode)
            for value in values:
                with self.subTest(mode=mode, value=value):
                    edit.setText(value)
                    with patch("pixelkit.app.BatchWorker") as worker:
                        self.window._start_processing()
                    worker.assert_not_called()
                    self.assertTrue(edit.property("invalid"))
                    self.assertTrue(edit.accessibleDescription())
                    self.assertFalse(self.window.field_errors[edit].isHidden())
                    self.assertFalse(destination.parent.exists())
                    self.assertFalse(self.window.processing)
            edit.clear()
            self.assertFalse(edit.property("invalid"))

    def test_empty_original_dimensions_and_invalid_inactive_fields_are_allowed(self):
        self.window.width_edit.setText("0")
        self.window.height_edit.setText("invalid")
        self.window.resize_mode.setCurrentIndex(1)
        self.window.long_side_edit.clear()
        self.window.target_size_edit.clear()
        self.assertTrue(self.window._validate_processing_fields())
        self.window.resize_mode.setCurrentIndex(0)
        self.window.width_edit.clear()
        self.window.height_edit.clear()
        self.window.long_side_edit.setText("0")
        self.assertTrue(self.window._validate_processing_fields())

    def test_manual_extension_follows_selected_format_and_keeps_name_and_folder(self):
        source = self.root / "image.png"
        source.touch()
        self.window._set_sources([source])
        self.window.output_edit.setText(str(self.root / "chosen name.png"))
        self.window.output_edit.textEdited.emit(self.window.output_edit.text())
        self.window.format_combo.setCurrentText("JPG")
        self.assertEqual(self.window.output_edit.text(), str(self.root / "chosen name.jpg"))
        self.window.output_edit.setText(str(self.root / "chosen name.jpeg"))
        self.window._normalize_output_extension()
        self.assertEqual(self.window.output_edit.text(), str(self.root / "chosen name.jpeg"))
        self.window.format_combo.setCurrentText("Automatic")
        self.assertEqual(self.window.output_edit.text(), str(self.root / "chosen name.png"))

    def test_launch_normalizes_a_later_manual_extension_and_encodes_the_selected_format(self):
        magick = find_magick()
        if not magick:
            self.skipTest("ImageMagick is needed for actual format verification")
        self.window.magick = magick
        source = self.root / "input.png"
        run_magick([magick, "-size", "120x80", "xc:#7452eb", str(source)], check=True)
        original = source.read_bytes()
        self.window._set_sources([source])
        self.window.format_combo.setCurrentText("JPG")
        requested = self.root / "mismatched.png"
        self.window.output_edit.setText(str(requested))
        self.window.default_output = False
        with patch.object(BatchWorker, "start", BatchWorker.run), patch("pixelkit.app.ReportDialog"):
            self.window._start_processing()
        output = requested.with_suffix(".jpg")
        result = run_magick([magick, "identify", "-format", "%m %wx%h", str(output)], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, "JPEG 120x80")
        self.assertFalse(requested.exists())
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.window.last_report.files[0].output, output)

    def test_output_path_errors_keep_inputs_settings_and_do_not_start_worker(self):
        sources = [self.root / "one.png", self.root / "two.png"]
        for source in sources:
            source.touch()
        blocker = self.root / "existing file"
        blocker.write_bytes(b"keep")
        for selected, output in ((sources, blocker), (sources[:1], blocker / "out.png"), (sources[:1], self.root)):
            with self.subTest(output=output):
                self.window._set_sources(selected)
                self.window.width_edit.setText("640")
                self.window.output_edit.setText(str(output))
                with patch.object(self.window, "_show_message") as message, patch("pixelkit.app.BatchWorker") as worker:
                    self.window._start_processing()
                worker.assert_not_called()
                self.assertEqual(message.call_args.args[1], "Could not prepare output")
                self.assertEqual(self.window.sources, [source.resolve() for source in selected])
                self.assertEqual(self.window.width_edit.text(), "640")
                self.assertEqual(blocker.read_bytes(), b"keep")
                self.assertFalse(self.window.processing)

    def test_last_image_report_can_be_reopened_and_is_unavailable_while_busy(self):
        report = BatchReport((), self.root)
        with patch("pixelkit.app.ReportDialog") as dialog:
            self.window._processing_finished(report)
            self.window.report_button.click()
            self.assertEqual(dialog.call_count, 2)
        self.window._set_processing_state(True)
        self.assertFalse(self.window.report_button.isEnabled())
        with patch("pixelkit.app.ReportDialog") as dialog:
            self.window._show_last_report()
            dialog.assert_not_called()
        self.window._set_processing_state(False)
        self.assertTrue(self.window.report_button.isEnabled())

    def test_image_drop_appends_unique_files_and_keeps_custom_batch_folder(self):
        sources = [self.root / name for name in ("one.png", "two.png", "three.png")]
        for source in sources:
            source.touch()
        self.window._set_sources(sources[:1])
        output = self.root / "custom" / "one.jpg"
        self.window.output_edit.setText(str(output))
        self.window.default_output = False
        self.window.source_list.files_dropped.emit(sources[:2])
        self.assertEqual(self.window.sources, [source.resolve() for source in sources[:2]])
        self.assertEqual(self.window.output_edit.text(), str(output.parent))
        self.assertFalse(self.window.default_output)
        self.window.source_list.files_dropped.emit(sources[1:])
        self.assertEqual(self.window.sources, [source.resolve() for source in sources])
        self.assertEqual(self.window.output_edit.text(), str(output.parent))

    def test_primary_image_action_remains_visible_at_minimum_window_size(self):
        self.window.resize(1040, 620)
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.window.process_button.visibleRegion().isEmpty())
        self.assertEqual(self.window.resize_mode.accessibleName(), "Resize mode")

    def test_image_settings_fit_default_and_large_windows_after_resizing_and_queue_changes(self):
        files = [self.root / f"image {index:02d}.png" for index in range(60)]
        for file in files:
            file.touch()
        self.window.show()
        page = self.window.media_stack.widget(0)
        columns = page.findChildren(QScrollArea)
        for sources in ([], files[:1], files, []):
            self.window._set_sources(sources)
            for size in ((1040, 620), (1100, 780), (1440, 900), (1440, 1000), (1100, 780)):
                self.window.resize(*size)
                for mode in (0, 1):
                    self.window.resize_mode.setCurrentIndex(mode)
                    self.window.media_stack.setCurrentIndex(1)
                    self.window.media_stack.setCurrentIndex(0)
                    for _ in range(4):
                        self.app.processEvents()
                    with self.subTest(files=len(sources), size=size, mode=mode):
                        self.assertFalse(self.window.process_button.visibleRegion().isEmpty())
                        for first, second in ((self.window.source_card, self.window.quality_card), (self.window.resize_card, self.window.output_card)):
                            self.assertEqual(
                                (first.mapTo(page, first.rect().topLeft()).y(), first.mapTo(page, first.rect().bottomLeft()).y()),
                                (second.mapTo(page, second.rect().topLeft()).y(), second.mapTo(page, second.rect().bottomLeft()).y()),
                                "Paired cards must have matching top and bottom edges",
                            )
                        if size[1] >= 780:
                            for column in columns:
                                self.assertEqual(column.verticalScrollBar().maximum(), 0)
                            self.assertEqual(self.window.resize_card.visibleRegion().boundingRect(), self.window.resize_card.rect())
                        if sources == files:
                            self.assertGreater(self.window.source_list.verticalScrollBar().maximum(), 0)

    def test_grouped_numbers_are_rejected_in_dimensions_and_file_size(self):
        previous_locale = QLocale()
        try:
            for locale_name in ("en_US", "en_PL"):
                QLocale.setDefault(QLocale(locale_name))
                for edit, maximum in ((self.window.width_edit, 100000), (self.window.height_edit, 100000), (self.window.long_side_edit, 100000), (self.window.target_size_edit, 1000000)):
                    with self.subTest(locale=locale_name, field=edit.accessibleName()):
                        edit.setValidator(self.window._integer_validator(1, maximum))
                        edit.setText(f"1{edit.validator().locale().groupSeparator()}000")
                        self.assertFalse(edit.hasAcceptableInput())
                        edit.setText("1000")
                        self.assertTrue(edit.hasAcceptableInput())
        finally:
            QLocale.setDefault(previous_locale)

    def test_locale_digits_and_bidi_signs_launch_with_ascii_dimensions_and_size_limit(self):
        source = self.root / "original.png"
        source.write_bytes(b"original")
        self.window._set_sources([source])
        self.window.format_combo.setCurrentText("WEBP")
        self.window.target_size_check.setChecked(True)
        try:
            for locale_name in ("ar_EG", "fa_IR", "he_IL", "en_US"):
                locale = QLocale(locale_name)
                locale.setNumberOptions(locale.numberOptions() | QLocale.NumberOption.RejectGroupSeparator)
                for edit, value in ((self.window.width_edit, 123), (self.window.height_edit, 45), (self.window.long_side_edit, 99), (self.window.target_size_edit, 500)):
                    edit.validator().setLocale(locale)
                    edit.setText(locale.positiveSign() + locale.toString(value))
                    self.assertTrue(edit.hasAcceptableInput())
                for mode, geometry in ((0, "123x45"), (1, "99x99")):
                    with self.subTest(locale=locale_name, mode=mode):
                        self.window.resize_mode.setCurrentIndex(mode)
                        self.window.output_edit.setText(str(self.root / f"{locale_name}-{mode}.webp"))
                        self.assertTrue(self.window._validate_processing_fields())
                        with patch("pixelkit.app.BatchWorker") as worker, patch.object(self.window, "_show_message") as message:
                            self.window._start_processing()
                        worker.assert_called_once()
                        jobs, _, target_bytes = worker.call_args.args
                        command = jobs[0][0]
                        self.assertEqual(command[command.index("-resize") + 1], geometry)
                        self.assertEqual(target_bytes, 500 * 1024)
                        worker.return_value.start.assert_called_once()
                        message.assert_not_called()
                        self.assertEqual(source.read_bytes(), b"original")
                        self.window.worker = None
                        self.window._set_processing_state(False)
        finally:
            self.window.worker = None
            self.window._set_processing_state(False)

    def test_locale_numbers_save_and_reload_as_normalized_preset_values(self):
        locale = QLocale("ar_EG")
        locale.setNumberOptions(locale.numberOptions() | QLocale.NumberOption.RejectGroupSeparator)
        self.window.format_combo.setCurrentText("WEBP")
        self.window.target_size_check.setChecked(True)
        for edit, value in ((self.window.width_edit, 123), (self.window.height_edit, 45), (self.window.target_size_edit, 500)):
            edit.validator().setLocale(locale)
            edit.setText(locale.positiveSign() + locale.toString(value))
        with patch.object(self.window, "_ask_preset_name", return_value="Native digits"), patch.object(self.window, "_show_message") as message:
            self.window._save_preset()
        message.assert_not_called()
        preset = self.preset_store.load()["Native digits"]
        self.assertEqual((preset.width, preset.height, preset.target_kib), (123, 45, 500))

    def test_source_hard_link_alias_is_rejected_before_overwrite_confirmation(self):
        source = self.root / "original.png"
        source.write_bytes(b"original")
        alias = self.root / "alias.png"
        os.link(source, alias)
        self.window._set_sources([source])
        self.window.output_edit.setText(str(alias))
        with patch.object(self.window, "_show_message") as message, patch.object(self.window, "_confirm") as confirm:
            self.window._start_processing()
        self.assertEqual(message.call_args.args[1], "Unsafe overwrite")
        confirm.assert_not_called()
        self.assertIsNone(self.window.worker)
        self.assertEqual(source.read_bytes(), b"original")

    def test_tilde_output_folder_is_preserved_and_rejected_as_a_single_file_destination(self):
        source = self.root / "source.png"
        source.touch()
        self.window._set_sources([source])
        folder = self.root / "chosen folder"
        folder.mkdir()
        # A disposable path reached through ~; no real home files are changed.
        relative = os.path.relpath(folder, Path.home())
        typed = str(Path("~") / relative)
        self.window.output_edit.setText(typed)
        self.window._normalize_output_extension()
        self.assertEqual(self.window.output_edit.text(), typed)
        with patch.object(self.window, "_show_message") as message, patch("pixelkit.app.BatchWorker") as worker:
            self.window._start_processing()
        worker.assert_not_called()
        self.assertEqual(message.call_args.args[1], "Could not prepare output")
        self.assertTrue(folder.is_dir())

    def test_unknown_tilde_user_is_a_recoverable_output_error(self):
        source = self.root / "source.png"
        source.touch()
        self.window._set_sources([source])
        typed = "~pixelkit_nonexistent_qa_user/out.png"
        self.window.output_edit.setText(typed)
        self.window._normalize_output_extension()
        self.assertEqual(self.window.output_edit.text(), typed)
        with patch.object(self.window, "_show_message") as message, patch("pixelkit.app.BatchWorker") as worker:
            self.window._start_processing()
        worker.assert_not_called()
        self.assertEqual(message.call_args.args[1], "Could not prepare output")
        self.assertFalse(self.window.processing)

    def test_batch_outputs_with_case_aliases_get_distinct_names(self):
        sources = [self.root / "one" / "Photo.png", self.root / "two" / "photo.png"]
        for source in sources:
            source.parent.mkdir()
            source.touch()
        self.window._set_sources(sources)
        worker = Mock()
        worker.isRunning.return_value = False
        with patch("pixelkit.app.BatchWorker", return_value=worker) as constructor:
            self.window._start_processing()
        jobs = constructor.call_args.args[0]
        names = [output.name.casefold() for _command, output in jobs]
        self.assertEqual(len(set(names)), 2)
        self.assertFalse(any(source.stat().st_size for source in sources))


if __name__ == "__main__":
    unittest.main()
