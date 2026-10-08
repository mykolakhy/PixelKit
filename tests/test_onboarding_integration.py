from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QSettings, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

from pixelkit.app import ONBOARDING_SETTINGS_KEY, SUPPORTED_SUFFIXES, ImageMagickStudio, PixelKitApplication, main
from pixelkit.onboarding import OnboardingDialog
from pixelkit.presets import Preset, PresetStore
from pixelkit.report import BatchReport
from pixelkit.video import VIDEO_SUFFIXES, VideoSettings
from pixelkit.video_presets import VideoPresetStore


class OnboardingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.settings_path = self.root / "preferences.ini"
        self.store = PresetStore(QSettings(str(self.settings_path), QSettings.Format.IniFormat))
        self.windows = []
        self.addCleanup(self.close_windows)
        message_patch = patch("pixelkit.app.ImageMagickStudio._show_message")
        self.message = message_patch.start()
        self.addCleanup(message_patch.stop)
        self.window = self.make_window(self.store)

    def make_window(self, store=None):
        if store is None:
            store = PresetStore(QSettings(str(self.settings_path), QSettings.Format.IniFormat))
        with patch("pixelkit.app.find_magick", return_value="magick"), patch("pixelkit.video_panel.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video_panel.find_ffprobe", return_value="ffprobe"):
            window = ImageMagickStudio(store)
        self.windows.append(window)
        return window

    def close_windows(self):
        for window in self.windows:
            for owner in (window, window.video_panel):
                owner.processing = False
                if isinstance(owner.worker, Mock):
                    owner.worker.isRunning.return_value = False
            window.close()
        self.app.processEvents()

    def source(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original media fixture")
        return path

    def dismissed(self, window=None):
        window = window or self.window
        return window.preset_store.settings.value(ONBOARDING_SETTINGS_KEY, False, type=bool)

    def run_dialog(self, callback, *, window=None, replay=False):
        """Drive the real modal loop; keep assertion failures outside Qt callbacks."""
        window = window or self.window
        window.show()
        window.activateWindow()
        self.app.processEvents()
        errors, dialogs = [], []

        def interact():
            dialog = window._onboarding_dialog
            if dialog is None:
                errors.append(AssertionError("Getting started did not open"))
                return
            dialogs.append(dialog)
            try:
                callback(dialog)
            except BaseException as error:
                errors.append(error)
                dialog.reject()

        watchdog = QTimer(window)
        watchdog.setSingleShot(True)

        def reject_unfinished():
            dialog = window._onboarding_dialog
            if dialog is not None:
                errors.append(AssertionError("Getting started did not finish"))
                dialog.reject()

        watchdog.timeout.connect(reject_unfinished)
        watchdog.start(2000)
        QTimer.singleShot(0, interact)
        try:
            if replay:
                window.getting_started_action.trigger()
            else:
                window._maybe_show_onboarding()
        finally:
            watchdog.stop()
        self.assertTrue(dialogs, "The dialog was suppressed unexpectedly")
        if errors:
            raise errors[0]
        self.assertIsNone(window._onboarding_dialog)
        return dialogs[0]

    @staticmethod
    def skip(dialog):
        QTest.mouseClick(dialog.skip_button, Qt.MouseButton.LeftButton)

    def complete(self, dialog):
        self.assertEqual(dialog.page_stack.currentIndex(), 0)
        QTest.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.page_stack.currentIndex(), 1)
        QTest.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)

    def test_skip_persists_across_windows_and_preserves_image_and_video_presets(self):
        image = Preset(quality=63, output_format="WEBP")
        video = VideoSettings(preset="small", max_height=720, audio="remove")
        self.store.save({"My image preset": image})
        VideoPresetStore(self.store.settings).save({"My video preset": video})
        raw_image = self.store.settings.value(PresetStore.KEY)
        raw_video = self.store.settings.value(VideoPresetStore.KEY)
        self.store.settings.setValue("unrelated/preference", "keep me")
        dialog = self.run_dialog(self.skip)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertTrue(self.dismissed())
        self.assertTrue(self.window._onboarding_seen_session)
        self.assertEqual(self.store.settings.value(PresetStore.KEY), raw_image)
        self.assertEqual(self.store.settings.value(VideoPresetStore.KEY), raw_video)
        self.assertEqual(self.store.settings.value("unrelated/preference"), "keep me")
        reopened = self.make_window()
        reopened.show()
        with patch.object(OnboardingDialog, "exec") as execute:
            reopened._maybe_show_onboarding()
        execute.assert_not_called()
        self.assertEqual(reopened.custom_presets, {"My image preset": image})
        self.assertEqual(reopened.video_panel.custom_presets, {"My video preset": video})

    def test_escape_and_title_bar_close_are_saved_dismissals(self):
        for index, callback in enumerate((lambda dialog: QTest.keyClick(dialog, Qt.Key.Key_Escape), lambda dialog: dialog.close())):
            with self.subTest(index=index):
                store = PresetStore(QSettings(str(self.root / f"dismissal-{index}.ini"), QSettings.Format.IniFormat))
                window = self.make_window(store)
                with patch("pixelkit.app.QFileDialog.getOpenFileName") as chooser:
                    dialog = self.run_dialog(callback, window=window)
                self.assertFalse(dialog.add_file_requested)
                chooser.assert_not_called()
                self.assertTrue(self.dismissed(window))

    def test_help_replays_after_dismissal_and_restarts_on_first_page(self):
        self.run_dialog(self.skip)

        def navigate_and_skip(dialog):
            self.assertEqual(dialog.page_stack.currentIndex(), 0)
            QTest.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
            QTest.mouseClick(dialog.back_button, Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.page_stack.currentIndex(), 0)
            self.skip(dialog)

        self.run_dialog(navigate_and_skip, replay=True)
        with patch.object(OnboardingDialog, "exec") as execute:
            self.window._maybe_show_onboarding()
        execute.assert_not_called()
        self.assertTrue(self.dismissed())

    def test_cancelled_final_chooser_keeps_empty_queues_and_dismissal(self):
        with patch("pixelkit.app.QFileDialog.getOpenFileName", return_value=("", "")) as chooser, patch("pixelkit.app.BatchWorker") as image_worker, patch("pixelkit.video_panel.VideoWorker") as video_worker:
            dialog = self.run_dialog(self.complete)
        chooser.assert_called_once()
        self.assertTrue(dialog.add_file_requested)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(self.dismissed())
        self.assertEqual(self.window.sources, [])
        self.assertEqual(self.window.video_panel.sources, [])
        image_worker.assert_not_called()
        video_worker.assert_not_called()
        self.assertFalse((self.root / "optimized").exists())

    def test_final_image_and_video_choices_route_without_processing_or_media_writes(self):
        for index, suffix in enumerate((".HEIF", ".MOV")):
            with self.subTest(suffix=suffix):
                source = self.source(f"first file{suffix}")
                before = (source.read_bytes(), source.stat().st_mtime_ns)
                window = self.make_window(PresetStore(QSettings(str(self.root / f"routing-{index}.ini"), QSettings.Format.IniFormat)))

                def choose(*args):
                    self.assertIsNone(window._onboarding_dialog)
                    self.assertIsNone(self.app.activeModalWidget())
                    for supported in SUPPORTED_SUFFIXES | VIDEO_SUFFIXES:
                        self.assertIn(f"*{supported}", args[3])
                    return str(source), ""

                with patch("pixelkit.app.QFileDialog.getOpenFileName", side_effect=choose), patch("pixelkit.app.BatchWorker") as image_worker, patch("pixelkit.video_panel.VideoWorker") as video_worker:
                    self.run_dialog(self.complete, window=window)
                self.app.processEvents()
                is_video = suffix.lower() in VIDEO_SUFFIXES
                self.assertEqual(window.sources, [] if is_video else [source])
                self.assertEqual(window.video_panel.sources, [source] if is_video else [])
                self.assertEqual(window.media_stack.currentIndex(), int(is_video))
                active_list = window.video_panel.source_list if is_video else window.source_list
                self.assertIs(self.app.focusWidget(), active_list)
                self.assertIsNone(window.worker)
                self.assertIsNone(window.video_panel.worker)
                image_worker.assert_not_called()
                video_worker.assert_not_called()
                self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)
                self.assertFalse(Path(window.video_panel.output_edit.text() if is_video else window.output_edit.text()).exists())

    def test_replay_appends_selected_media_and_preserves_queues_settings_and_reports(self):
        images = [self.source(f"image-{i}.png") for i in range(3)]
        videos = [self.source(f"video-{i}.mov") for i in range(3)]
        self.window._set_sources(images[:2])
        panel = self.window.video_panel
        panel.set_sources(videos[:2])
        image_output, video_output = self.root / "image-output", self.root / "video-output"
        self.window.output_edit.setText(str(image_output))
        self.window.default_output = False
        panel.output_edit.setText(str(video_output))
        self.window.width_edit.setText("640")
        self.window.quality_slider.setValue(71)
        panel.preset_combo.setCurrentIndex(panel.preset_combo.findData("small"))
        panel.resolution_combo.setCurrentIndex(panel.resolution_combo.findData(720))
        panel.audio_combo.setCurrentIndex(panel.audio_combo.findData("remove"))
        image_settings, video_settings = self.window._current_preset(), panel._current_settings()
        image_report, video_report = BatchReport((), self.root), BatchReport((), self.root)
        self.window.last_report, panel.last_report = image_report, video_report
        self.store.settings.setValue(ONBOARDING_SETTINGS_KEY, True)
        with patch("pixelkit.app.QFileDialog.getOpenFileName", side_effect=[(str(images[2]), ""), (str(videos[2]), "")]), patch("pixelkit.app.BatchWorker") as image_worker, patch("pixelkit.video_panel.VideoWorker") as video_worker:
            self.run_dialog(self.complete, replay=True)
            self.run_dialog(self.complete, replay=True)
        self.assertEqual(self.window.sources, images)
        self.assertEqual(panel.sources, videos)
        self.assertEqual(self.window.output_edit.text(), str(image_output))
        self.assertEqual(panel.output_edit.text(), str(video_output))
        self.assertEqual(self.window._current_preset(), image_settings)
        self.assertEqual(panel._current_settings(), video_settings)
        self.assertIs(self.window.last_report, image_report)
        self.assertIs(panel.last_report, video_report)
        image_worker.assert_not_called()
        video_worker.assert_not_called()
        self.assertFalse(image_output.exists())
        self.assertFalse(video_output.exists())

    def test_replay_skip_and_cancel_leave_existing_state_and_mode_unchanged(self):
        image, video = self.source("existing.png"), self.source("existing.mov")
        self.window._set_sources([image])
        self.window.video_panel.set_sources([video])
        self.window.mode_buttons[1].click()
        self.window.quality_slider.setValue(59)
        self.window.video_panel.audio_combo.setCurrentIndex(self.window.video_panel.audio_combo.findData("remove"))
        outputs = self.window.output_edit.text(), self.window.video_panel.output_edit.text()
        settings = self.window._current_preset(), self.window.video_panel._current_settings()
        reports = BatchReport((), self.root), BatchReport((), self.root)
        self.window.last_report, self.window.video_panel.last_report = reports
        self.run_dialog(self.skip, replay=True)
        with patch("pixelkit.app.QFileDialog.getOpenFileName", return_value=("", "")):
            self.run_dialog(self.complete, replay=True)
        self.assertEqual(self.window.sources, [image])
        self.assertEqual(self.window.video_panel.sources, [video])
        self.assertEqual(self.window.media_stack.currentIndex(), 1)
        self.assertEqual((self.window.output_edit.text(), self.window.video_panel.output_edit.text()), outputs)
        self.assertEqual((self.window._current_preset(), self.window.video_panel._current_settings()), settings)
        self.assertIs(self.window.last_report, reports[0])
        self.assertIs(self.window.video_panel.last_report, reports[1])

    def test_existing_startup_queue_defers_without_marking_dismissal(self):
        for suffix in (".png", ".mov"):
            with self.subTest(suffix=suffix):
                window = self.make_window()
                window.open_files([self.source(f"startup{suffix}")])
                with patch.object(OnboardingDialog, "exec") as execute:
                    window._maybe_show_onboarding()
                execute.assert_not_called()
                self.assertFalse(self.dismissed(window))
                self.assertFalse(window._onboarding_seen_session)

    def test_startup_finder_event_dispatches_before_onboarding_timer(self):
        if not isinstance(self.app, PixelKitApplication):
            self.skipTest("The shared Qt application was created without Finder event support")
        source = self.source("Finder image.png")
        self.window.show()
        event = Mock()
        event.type.return_value = QEvent.Type.FileOpen
        event.file.return_value = str(source)
        self.app.files_opened.connect(self.window.open_files)
        try:
            with patch.object(OnboardingDialog, "exec") as execute:
                self.app.event(event)
                QTimer.singleShot(0, self.window._maybe_show_onboarding)
                self.app.processEvents()
            execute.assert_not_called()
            self.assertEqual(self.window.sources, [source])
            self.assertFalse(self.dismissed())
        finally:
            self.app.files_opened.disconnect(self.window.open_files)
            self.app.pending_files.clear()

    def test_main_dispatches_cli_and_pending_finder_files_before_onboarding(self):
        if not isinstance(self.app, PixelKitApplication):
            self.skipTest("The shared Qt application was created without Finder event support")
        finder, cli = self.source("Finder.png"), self.source("CLI.png")
        startup = self.make_window()
        callbacks = []
        previous_pending = list(self.app.pending_files)
        self.app.pending_files[:] = [finder]
        try:
            with patch("pixelkit.app.PixelKitApplication", return_value=self.app), patch("pixelkit.app.ImageMagickStudio", return_value=startup), patch("pixelkit.app.sys.platform", "linux"), patch("pixelkit.app.sys.argv", ["pixelkit", str(cli)]), patch("pixelkit.app.QTimer.singleShot", side_effect=lambda delay, callback: callbacks.append((delay, callback))), patch.object(self.app, "exec", return_value=0), patch("pixelkit.app.sys.exit") as exit_app:
                main()
            exit_app.assert_called_once_with(0)
            self.assertEqual(callbacks, [(0, self.app.dispatch_open_files), (0, startup._maybe_show_onboarding)])
            with patch.object(OnboardingDialog, "exec") as execute:
                for _, callback in callbacks:
                    callback()
            execute.assert_not_called()
            self.assertEqual(startup.sources, [finder, cli])
            self.assertFalse(self.dismissed(startup))
        finally:
            self.app.files_opened.disconnect(startup.open_files)
            self.app.pending_files[:] = previous_pending

    def test_busy_images_videos_and_running_workers_block_first_run_and_replay(self):
        self.window.show()
        for owner in (self.window, self.window.video_panel):
            for state in ("processing", "worker"):
                with self.subTest(owner=type(owner).__name__, state=state):
                    if state == "processing":
                        owner.processing = True
                    else:
                        owner.worker = Mock()
                        owner.worker.isRunning.return_value = True
                    with patch.object(OnboardingDialog, "exec") as execute:
                        self.window._maybe_show_onboarding()
                        self.window.getting_started_action.trigger()
                    execute.assert_not_called()
                    self.assertFalse(self.dismissed())
                    owner.processing = False
                    owner.worker = None

    def test_processing_started_during_chooser_rejects_selected_file(self):
        source = self.source("late.png")
        for owner in (self.window, self.window.video_panel):
            for state in ("processing", "worker"):
                with self.subTest(owner=type(owner).__name__, state=state):
                    def choose(*args):
                        if state == "processing":
                            owner.processing = True
                        else:
                            owner.worker = Mock()
                            owner.worker.isRunning.return_value = True
                        return str(source), ""

                    with patch("pixelkit.app.QFileDialog.getOpenFileName", side_effect=choose):
                        self.window._choose_onboarding_file()
                    self.assertEqual(self.window.sources, [])
                    self.assertEqual(self.window.video_panel.sources, [])
                    owner.processing = False
                    owner.worker = None

    def test_finder_files_arriving_during_chooser_are_kept_when_selection_is_added(self):
        incoming, selected = self.source("Finder during chooser.png"), self.source("selected.png")

        def choose(*args):
            self.window.open_files([incoming])
            return str(selected), ""

        with patch("pixelkit.app.QFileDialog.getOpenFileName", side_effect=choose), patch("pixelkit.app.BatchWorker") as image_worker, patch("pixelkit.video_panel.VideoWorker") as video_worker:
            self.run_dialog(self.complete)
        self.assertEqual(self.window.sources, [incoming, selected])
        self.assertEqual(self.window.video_panel.sources, [])
        self.assertTrue(self.dismissed())
        image_worker.assert_not_called()
        video_worker.assert_not_called()

    def test_missing_unsupported_and_directory_choices_do_not_change_queue(self):
        original = self.source("original.png")
        self.window._set_sources([original])
        before_output = self.window.output_edit.text()
        unsupported = self.source("notes.txt")
        folder = self.root / "folder.png"
        folder.mkdir()
        vanished = self.source("vanished.mov")
        for selected in (self.root / "missing.png", unsupported, folder, vanished):
            with self.subTest(selected=selected):
                def choose(*args):
                    if selected == vanished:
                        vanished.unlink()
                    return str(selected), ""

                with patch("pixelkit.app.QFileDialog.getOpenFileName", side_effect=choose):
                    self.window._choose_onboarding_file()
                self.assertEqual(self.window.sources, [original])
                self.assertEqual(self.window.video_panel.sources, [])
                self.assertEqual(self.window.output_edit.text(), before_output)
                self.assertFalse(self.dismissed())

    def test_incoming_supported_finder_files_interrupt_without_marking_completed(self):
        for index, suffix in enumerate((".png", ".MOV")):
            with self.subTest(suffix=suffix):
                source = self.source(f"incoming{suffix}")
                window = self.make_window(PresetStore(QSettings(str(self.root / f"incoming-{index}.ini"), QSettings.Format.IniFormat)))

                def incoming(dialog):
                    window.open_files([source, self.root / "missing.png"])
                    self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

                with patch("pixelkit.app.QFileDialog.getOpenFileName") as chooser:
                    self.run_dialog(incoming, window=window)
                is_video = suffix.lower() in VIDEO_SUFFIXES
                self.assertEqual(window.sources, [] if is_video else [source])
                self.assertEqual(window.video_panel.sources, [source] if is_video else [])
                self.assertEqual(window.media_stack.currentIndex(), int(is_video))
                self.assertFalse(self.dismissed(window))
                self.assertFalse(window._onboarding_seen_session)
                chooser.assert_not_called()
                with patch.object(OnboardingDialog, "exec") as execute:
                    window._maybe_show_onboarding()
                execute.assert_not_called()

    def test_invalid_and_mixed_finder_input_does_not_interrupt_dialog(self):
        image, video, unsupported = self.source("incoming.png"), self.source("incoming.mov"), self.source("notes.txt")

        def incoming(dialog):
            for paths in ([unsupported, self.root / "missing.png"], [image, video]):
                self.window.open_files(paths)
                self.assertIs(self.window._onboarding_dialog, dialog)
                self.assertTrue(dialog.isVisible())
                self.assertFalse(self.window._onboarding_interrupted)
                self.assertEqual(self.window.sources, [])
                self.assertEqual(self.window.video_panel.sources, [])
            self.skip(dialog)

        self.run_dialog(incoming)
        self.assertTrue(self.dismissed())
        self.message.assert_called_once()

    def test_parent_close_interrupts_without_persisting_or_opening_chooser(self):
        with patch("pixelkit.app.QFileDialog.getOpenFileName") as chooser:
            self.run_dialog(lambda dialog: self.window.close())
        self.assertFalse(self.dismissed())
        self.assertFalse(self.window._onboarding_seen_session)
        self.assertFalse(self.window.isVisible())
        chooser.assert_not_called()

    def test_parent_close_during_chooser_discards_selected_file(self):
        source = self.source("late close.png")
        self.window.show()

        def choose(*args):
            self.window.close()
            return str(source), ""

        with patch("pixelkit.app.QFileDialog.getOpenFileName", side_effect=choose):
            self.window._choose_onboarding_file()
        self.assertEqual(self.window.sources, [])
        self.assertEqual(self.window.video_panel.sources, [])

    def test_modal_startup_warning_defers_and_retries_after_warning_closes(self):
        self.window.show()
        warning = QDialog(self.window)
        warning.setModal(True)
        warning.show()
        self.app.processEvents()
        errors, opened = [], []
        watchdog = QTimer(self.window)
        watchdog.setSingleShot(True)

        def dismiss_onboarding():
            dialog = self.window._onboarding_dialog
            if dialog is None:
                errors.append(AssertionError("Deferred getting started did not open"))
            else:
                opened.append(dialog)
                try:
                    self.skip(dialog)
                except BaseException as error:
                    errors.append(error)
                    dialog.reject()

        def reject_unfinished():
            dialog = self.window._onboarding_dialog
            if dialog is not None:
                errors.append(AssertionError("Deferred getting started did not finish"))
                dialog.reject()

        watchdog.timeout.connect(reject_unfinished)
        watchdog.start(2000)

        try:
            with patch.object(OnboardingDialog, "exec") as execute:
                self.window._maybe_show_onboarding()
            execute.assert_not_called()
            self.assertFalse(self.dismissed())
            QTimer.singleShot(10, warning.reject)
            QTimer.singleShot(200, dismiss_onboarding)
            QTest.qWait(300)
        finally:
            watchdog.stop()
            warning.close()
        if errors:
            raise errors[0]
        self.assertEqual(len(opened), 1)
        self.assertTrue(self.dismissed())

    def test_unwritable_preferences_dismiss_for_session_without_blocking_user(self):
        blocker = self.source("not-a-folder")
        settings = QSettings(str(blocker / "preferences.ini"), QSettings.Format.IniFormat)
        window = self.make_window(PresetStore(settings))
        self.run_dialog(self.skip, window=window)
        self.assertTrue(window._onboarding_seen_session)
        self.assertIn("could not be saved", window.status_label.full_text)
        self.assertEqual(blocker.read_bytes(), b"original media fixture")
        with patch.object(OnboardingDialog, "exec") as execute:
            window._maybe_show_onboarding()
        execute.assert_not_called()
        self.message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
