from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QElapsedTimer, QEvent, QPoint, QSettings, QTimer, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QPushButton

from pixelkit.app import ONBOARDING_SETTINGS_KEY, ImageMagickStudio, PixelKitApplication, main
from pixelkit.onboarding import OnboardingPage
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

    def assert_eventually(self, condition, message):
        timer = QElapsedTimer()
        timer.start()
        while not condition() and timer.elapsed() < 2000:
            QTest.qWait(10)
        self.assertTrue(condition(), message)

    def show_page(self, *, window=None, replay=False):
        window = window or self.window
        window.show()
        window.activateWindow()
        self.app.processEvents()
        if replay:
            window.getting_started_action.trigger()
        else:
            window._maybe_show_onboarding()
        self.app.processEvents()
        page = window._onboarding_page
        self.assertIsInstance(page, OnboardingPage)
        self.assertIs(window.content_stack.currentWidget(), page)
        self.assertFalse(window.workspace_page.isVisible())
        self.assertFalse(window.workspace_page.isEnabled())
        self.assertTrue(page.isVisible())
        self.assertFalse(page.isWindow())
        self.assertIsNone(self.app.activeModalWidget())
        return page

    def run_page(self, callback, *, window=None, replay=False):
        window = window or self.window
        page = self.show_page(window=window, replay=replay)
        finished = []
        page.finished.connect(lambda: finished.append(None))
        with self.no_processing_or_choosers():
            callback(page)
            self.app.processEvents()
        self.assertIsNone(window._onboarding_page)
        self.assertIs(window.content_stack.currentWidget(), window.workspace_page)
        self.assertTrue(window.workspace_page.isEnabled())
        self.assertEqual(window.content_stack.count(), 1)
        return finished

    @contextmanager
    def no_processing_or_choosers(self):
        with ExitStack() as stack:
            checks = []
            for method, result in (
                ("getOpenFileName", ("", "")),
                ("getOpenFileNames", ([], "")),
                ("getExistingDirectory", ""),
                ("getSaveFileName", ("", "")),
                ("exec", 0),
            ):
                checks.append(stack.enter_context(patch(f"pixelkit.app.QFileDialog.{method}", return_value=result)))
            for target in ("pixelkit.app.BatchWorker", "pixelkit.video_panel.VideoWorker", "pixelkit.app.run_magick", "pixelkit.video.subprocess.Popen"):
                checks.append(stack.enter_context(patch(target)))
            yield
            for check in checks:
                check.assert_not_called()

    @staticmethod
    def skip(page):
        QTest.mouseClick(page.skip_button, Qt.MouseButton.LeftButton)

    def complete(self, page):
        self.assertEqual(page.page_stack.currentIndex(), 0)
        QTest.mouseClick(page.next_button, Qt.MouseButton.LeftButton)
        self.assertEqual(page.page_stack.currentIndex(), 1)
        self.assertEqual(page.next_button.text(), "Get started")
        QTest.mouseClick(page.next_button, Qt.MouseButton.LeftButton)

    def test_get_started_enters_empty_workspace_and_persists_without_file_side_effects(self):
        self.assertEqual(self.run_page(self.complete), [None])
        self.assertTrue(self.dismissed())
        self.assertTrue(self.window._onboarding_seen_session)
        self.assertEqual(self.window.sources, [])
        self.assertEqual(self.window.video_panel.sources, [])
        self.assertIsNone(self.window.worker)
        self.assertIsNone(self.window.video_panel.worker)
        self.assertIs(self.app.focusWidget(), self.window.source_list)
        self.assertEqual(set(self.root.iterdir()), {self.settings_path})
        reopened = self.make_window()
        reopened.show()
        reopened._maybe_show_onboarding()
        self.assertIsNone(reopened._onboarding_page)
        self.assertTrue(self.dismissed(reopened))

    def test_completion_buttons_and_keys_preserve_existing_work_in_both_modes(self):
        images = [self.source(f"existing-{index}.png") for index in range(2)]
        videos = [self.source(f"existing-{index}.mov") for index in range(2)]
        source_state = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in images + videos}
        actions = ("button", "return", "enter", "get_started_shortcut", "skip", "escape", "skip_shortcut")
        for mode in (0, 1):
            for action in actions:
                with self.subTest(mode=mode, action=action):
                    settings_path = self.root / f"entry-{mode}-{action}.ini"
                    store = PresetStore(QSettings(str(settings_path), QSettings.Format.IniFormat))
                    store.save({"Saved image": Preset(quality=63, output_format="WEBP")})
                    VideoPresetStore(store.settings).save({"Saved video": VideoSettings(preset="small", max_height=720, audio="remove")})
                    store.settings.setValue("unrelated/preference", "keep me")
                    store.settings.sync()
                    saved_preferences = {key: store.settings.value(key) for key in store.settings.allKeys()}
                    window = self.make_window(store)
                    panel = window.video_panel
                    window._set_sources(images)
                    panel.set_sources(videos)
                    window.source_list.setCurrentRow(1)
                    panel.source_list.setCurrentRow(1)
                    image_output = self.root / f"image-output-{mode}-{action}"
                    video_output = self.root / f"video-output-{mode}-{action}"
                    window.output_edit.setText(str(image_output))
                    window.default_output = False
                    panel.output_edit.setText(str(video_output))
                    window.width_edit.setText("640")
                    window.quality_slider.setValue(71)
                    panel.preset_combo.setCurrentIndex(panel.preset_combo.findData("small"))
                    panel.resolution_combo.setCurrentIndex(panel.resolution_combo.findData(720))
                    panel.audio_combo.setCurrentIndex(panel.audio_combo.findData("remove"))
                    panel.target_size_check.setChecked(True)
                    panel.target_size_edit.setText("8.5")
                    current_settings = window._current_preset(), panel._current_settings()
                    reports = BatchReport((), self.root), BatchReport((), self.root)
                    window.last_report, panel.last_report = reports
                    panel.report_button.show()
                    panel._update_state()
                    window.mode_buttons[mode].click()
                    window.resize(1300, 850)
                    window.show()
                    window.activateWindow()
                    self.app.processEvents()
                    geometry, title = window.geometry(), window.windowTitle()
                    existing_files = set(self.root.iterdir())

                    def finish(page):
                        self.assertEqual(window.geometry(), geometry)
                        if action == "skip":
                            self.skip(page)
                        elif action == "escape":
                            page.next_button.setFocus()
                            QTest.keyClick(page.next_button, Qt.Key.Key_Escape)
                        elif action == "skip_shortcut":
                            QTest.keySequence(page.next_button, QKeySequence("Alt+S"))
                            self.assert_eventually(lambda: window._onboarding_page is None, "Alt+S did not restore the workspace")
                        else:
                            QTest.mouseClick(page.next_button, Qt.MouseButton.LeftButton)
                            self.assertEqual(page.next_button.text(), "Get started")
                            if action == "button":
                                QTest.mouseClick(page.next_button, Qt.MouseButton.LeftButton)
                            elif action == "get_started_shortcut":
                                QTest.keySequence(page.next_button, QKeySequence("Alt+G"))
                                self.assert_eventually(lambda: window._onboarding_page is None, "Alt+G did not restore the workspace")
                            else:
                                key = Qt.Key.Key_Return if action == "return" else Qt.Key.Key_Enter
                                QTest.keyClick(page.next_button, key)

                    self.assertEqual(self.run_page(finish, window=window, replay=True), [None])
                    self.assertEqual(window.sources, images)
                    self.assertEqual(panel.sources, videos)
                    self.assertEqual((window.source_list.currentRow(), panel.source_list.currentRow()), (1, 1))
                    self.assertEqual(window.media_stack.currentIndex(), mode)
                    self.assertTrue(window.mode_buttons[mode].isChecked())
                    self.assertIs(self.app.focusWidget(), panel.source_list if mode else window.source_list)
                    self.assertEqual((window.output_edit.text(), panel.output_edit.text()), (str(image_output), str(video_output)))
                    self.assertFalse(window.default_output)
                    self.assertEqual((window._current_preset(), panel._current_settings()), current_settings)
                    self.assertIs(window.last_report, reports[0])
                    self.assertIs(panel.last_report, reports[1])
                    self.assertTrue(window.report_button.isEnabled())
                    self.assertFalse(panel.report_button.isHidden())
                    self.assertTrue(panel.report_button.isEnabled())
                    self.assertTrue(window.process_button.isEnabled())
                    self.assertTrue(panel.process_button.isEnabled())
                    self.assertIsNone(window.worker)
                    self.assertIsNone(panel.worker)
                    self.assertFalse(window.processing)
                    self.assertFalse(panel.processing)
                    self.assertEqual(window.geometry(), geometry)
                    self.assertEqual(window.windowTitle(), title)
                    self.assertEqual(set(self.root.iterdir()), existing_files)
                    self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in images + videos}, source_state)
                    self.assertTrue(self.dismissed(window))
                    persisted = QSettings(str(settings_path), QSettings.Format.IniFormat)
                    persisted.sync()
                    self.assertTrue(persisted.value(ONBOARDING_SETTINGS_KEY, False, type=bool))
                    self.assertEqual({key: persisted.value(key) for key in persisted.allKeys() if key != ONBOARDING_SETTINGS_KEY}, saved_preferences)
                    window.close()

    def test_skip_persists_across_windows_and_preserves_image_and_video_presets(self):
        image = Preset(quality=63, output_format="WEBP")
        video = VideoSettings(preset="small", max_height=720, audio="remove")
        self.store.save({"My image preset": image})
        VideoPresetStore(self.store.settings).save({"My video preset": video})
        raw_image = self.store.settings.value(PresetStore.KEY)
        raw_video = self.store.settings.value(VideoPresetStore.KEY)
        self.store.settings.setValue("unrelated/preference", "keep me")
        self.assertEqual(self.run_page(self.skip), [None])
        self.assertTrue(self.dismissed())
        self.assertTrue(self.window._onboarding_seen_session)
        self.assertEqual(self.store.settings.value(PresetStore.KEY), raw_image)
        self.assertEqual(self.store.settings.value(VideoPresetStore.KEY), raw_video)
        self.assertEqual(self.store.settings.value("unrelated/preference"), "keep me")
        reopened = self.make_window()
        reopened.show()
        reopened._maybe_show_onboarding()
        self.assertIsNone(reopened._onboarding_page)
        self.assertEqual(reopened.custom_presets, {"My image preset": image})
        self.assertEqual(reopened.video_panel.custom_presets, {"My video preset": video})

    def test_escape_from_page_or_focused_button_is_a_saved_dismissal(self):
        for index, callback in enumerate((lambda page: QTest.keyClick(page, Qt.Key.Key_Escape), lambda page: QTest.keyClick(page.next_button, Qt.Key.Key_Escape))):
            with self.subTest(index=index):
                store = PresetStore(QSettings(str(self.root / f"dismissal-{index}.ini"), QSettings.Format.IniFormat))
                window = self.make_window(store)
                with patch("pixelkit.app.QFileDialog.getOpenFileName") as chooser:
                    finished = self.run_page(callback, window=window)
                self.assertEqual(finished, [None])
                chooser.assert_not_called()
                self.assertTrue(self.dismissed(window))

    def test_help_replays_after_dismissal_and_restarts_on_first_page(self):
        self.run_page(self.skip)

        def navigate_and_skip(page):
            self.assertEqual(page.page_stack.currentIndex(), 0)
            QTest.mouseClick(page.next_button, Qt.MouseButton.LeftButton)
            QTest.mouseClick(page.back_button, Qt.MouseButton.LeftButton)
            self.assertEqual(page.page_stack.currentIndex(), 0)
            self.skip(page)

        self.run_page(navigate_and_skip, replay=True)
        self.window._maybe_show_onboarding()
        self.assertIsNone(self.window._onboarding_page)
        self.assertTrue(self.dismissed())

    def test_guide_fills_resized_central_area_without_another_window_or_title_change(self):
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        title = self.window.windowTitle()
        geometry = self.window.geometry()
        visible_windows = {widget for widget in self.app.topLevelWidgets() if widget.isVisible()}
        page = self.show_page()
        self.assertIs(self.window.centralWidget(), self.window.content_stack)
        self.assertEqual(self.window.windowTitle(), title)
        self.assertEqual(self.window.geometry(), geometry)
        self.assertEqual({widget for widget in self.app.topLevelWidgets() if widget.isVisible()}, visible_windows)
        for width, height in ((1040, 620), (1300, 850)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertEqual(page.geometry(), self.window.content_stack.contentsRect())
                self.assertEqual(page.mapTo(self.window, QPoint(0, 0)), self.window.content_stack.pos())
                self.assertEqual(self.window.windowTitle(), title)
                for button in (page.next_button, page.skip_button):
                    self.assertFalse(button.visibleRegion().isEmpty())
                    self.assertTrue(page.rect().contains(button.mapTo(page, QPoint(0, 0))))
                    self.assertTrue(page.rect().contains(button.mapTo(page, QPoint(button.width() - 1, button.height() - 1))))
        geometry = self.window.geometry()
        self.skip(page)
        self.app.processEvents()
        self.assertIsNone(self.window._onboarding_page)
        self.assertIs(self.window.content_stack.currentWidget(), self.window.workspace_page)
        self.assertEqual(self.window.workspace_page.geometry(), self.window.content_stack.contentsRect())
        self.assertEqual(self.window.geometry(), geometry)
        self.assertEqual(self.window.windowTitle(), title)

    def test_repeated_help_and_startup_callbacks_keep_one_active_guide(self):
        page = self.show_page()
        QTest.mouseClick(page.next_button, Qt.MouseButton.LeftButton)
        for _ in range(3):
            self.window._maybe_show_onboarding()
            self.window.getting_started_action.trigger()
            self.app.processEvents()
            self.assertIs(self.window._onboarding_page, page)
            self.assertEqual(self.window.content_stack.count(), 2)
            self.assertEqual(page.page_stack.currentIndex(), 1)
            self.assertFalse(self.dismissed())
        self.skip(page)
        self.app.processEvents()
        self.assertIsNone(self.window._onboarding_page)
        self.assertEqual(self.window.content_stack.count(), 1)

    def test_old_guide_signals_cannot_finish_a_new_help_replay(self):
        old_page = self.show_page()
        self.window.open_files([self.source("Finder interrupt.png")])
        self.window._show_onboarding()
        new_page = self.window._onboarding_page
        self.assertIsNotNone(new_page)
        self.assertIsNot(new_page, old_page)
        with patch("pixelkit.app.QFileDialog.getOpenFileName") as chooser:
            old_page.finished.emit()
            old_page.finished.emit()
        chooser.assert_not_called()
        self.assertIs(self.window._onboarding_page, new_page)
        self.assertIs(self.window.content_stack.currentWidget(), new_page)
        self.assertEqual(self.window.content_stack.count(), 2)
        self.assertFalse(self.dismissed())
        self.skip(new_page)
        self.app.processEvents()
        self.assertIsNone(self.window._onboarding_page)
        self.assertTrue(self.dismissed())

    def test_hidden_workspace_open_and_processing_actions_cannot_run_during_guide(self):
        with patch("pixelkit.app.sys.platform", "darwin"):
            window = self.make_window()
        window._set_sources([self.source("ready.png")])
        window.video_panel.set_sources([self.source("ready.mov")])
        page = self.show_page(window=window, replay=True)
        self.assertFalse(window.open_action.isEnabled())
        self.assertFalse(window.save_action.isEnabled())
        with patch.object(window, "_start_processing") as process_images, patch.object(window.video_panel, "start_processing") as process_video, patch.object(window, "_choose_many") as open_images, patch.object(window.video_panel, "choose_many") as open_video:
            for mode in (0, 1):
                window.media_stack.setCurrentIndex(mode)
                window._open_current_mode()
                window._process_current_mode()
                window.open_action.trigger()
                window.save_action.trigger()
                QTest.keySequence(page.next_button, QKeySequence(QKeySequence.StandardKey.Open))
                QTest.keySequence(page.next_button, QKeySequence(QKeySequence.StandardKey.Save))
                self.app.processEvents()
                self.assertIs(window._onboarding_page, page)
            process_images.assert_not_called()
            process_video.assert_not_called()
            open_images.assert_not_called()
            open_video.assert_not_called()
        self.assertIsNone(window.worker)
        self.assertIsNone(window.video_panel.worker)
        self.skip(page)
        self.app.processEvents()
        self.assertTrue(window.open_action.isEnabled())
        self.assertTrue(window.save_action.isEnabled())

    def test_hidden_workspace_buttons_are_disabled_and_restore_original_states(self):
        image, video = self.source("image ready.png"), self.source("video ready.mov")
        self.window._set_sources([image])
        panel = self.window.video_panel
        panel.set_sources([video])
        self.window.width_edit.setEnabled(False)
        controls = (self.window.process_button, panel.process_button, self.window.width_edit, panel.target_size_edit, self.window.report_button)
        original_enabled = tuple(control.isEnabled() for control in controls)
        self.assertEqual(original_enabled, (True, True, False, False, False))
        image_add_buttons = [button for button in self.window.source_card.findChildren(QPushButton) if button.text() in ("One file", "Add images…", "Folder")]
        self.assertEqual(len(image_add_buttons), 3)
        buttons = [self.window.process_button, panel.process_button, *image_add_buttons, panel.add_button, panel.folder_button]
        page = self.show_page(replay=True)
        with patch("pixelkit.app.BatchWorker") as image_worker, patch("pixelkit.video_panel.VideoWorker") as video_worker, patch("pixelkit.app.QFileDialog.getOpenFileName") as choose_one, patch("pixelkit.app.QFileDialog.getOpenFileNames") as choose_many, patch("pixelkit.app.QFileDialog.getExistingDirectory") as choose_folder:
            for button in buttons:
                self.assertFalse(button.isEnabled())
                button.click()
            self.app.processEvents()
            image_worker.assert_not_called()
            video_worker.assert_not_called()
            choose_one.assert_not_called()
            choose_many.assert_not_called()
            choose_folder.assert_not_called()
        self.assertEqual(self.window.sources, [image])
        self.assertEqual(panel.sources, [video])
        self.assertIsNone(self.window.worker)
        self.assertIsNone(panel.worker)
        self.skip(page)
        self.app.processEvents()
        self.assertTrue(self.window.workspace_page.isEnabled())
        self.assertEqual(tuple(control.isEnabled() for control in controls), original_enabled)
        self.assertTrue(all(button.isEnabled() for button in buttons))

    def test_existing_startup_queue_defers_without_marking_dismissal(self):
        for suffix in (".png", ".mov"):
            with self.subTest(suffix=suffix):
                window = self.make_window()
                window.open_files([self.source(f"startup{suffix}")])
                window._maybe_show_onboarding()
                self.assertIsNone(window._onboarding_page)
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
            self.app.event(event)
            QTimer.singleShot(0, self.window._maybe_show_onboarding)
            self.app.processEvents()
            self.assertIsNone(self.window._onboarding_page)
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
            # Reusing the test application must not replace its native Qt style
            # beneath existing windows. Fresh-process bundle checks cover that.
            with (
                patch("pixelkit.app.PixelKitApplication", return_value=self.app),
                patch("pixelkit.app.ImageMagickStudio", return_value=startup),
                patch("pixelkit.app.sys.platform", "linux"),
                patch("pixelkit.app.sys.argv", ["pixelkit", str(cli)]),
                patch("pixelkit.app.QTimer.singleShot", side_effect=lambda delay, callback: callbacks.append((delay, callback))),
                patch.object(self.app, "setStyle") as set_style,
                patch.object(self.app, "exec", return_value=0),
                patch("pixelkit.app.sys.exit") as exit_app,
            ):
                main()
            set_style.assert_called_once_with("Fusion")
            exit_app.assert_called_once_with(0)
            self.assertEqual(callbacks, [(0, self.app.dispatch_open_files), (0, startup._maybe_show_onboarding)])
            for _, callback in callbacks:
                callback()
            self.assertIsNone(startup._onboarding_page)
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
                    self.window._maybe_show_onboarding()
                    self.window.getting_started_action.trigger()
                    self.assertIsNone(self.window._onboarding_page)
                    self.assertFalse(self.dismissed())
                    owner.processing = False
                    owner.worker = None

    def test_incoming_supported_finder_files_interrupt_without_marking_completed(self):
        for index, suffix in enumerate((".png", ".MOV")):
            with self.subTest(suffix=suffix):
                source = self.source(f"incoming{suffix}")
                window = self.make_window(PresetStore(QSettings(str(self.root / f"incoming-{index}.ini"), QSettings.Format.IniFormat)))

                def incoming(page):
                    window.open_files([source, self.root / "missing.png"])
                    self.assertIsNone(window._onboarding_page)
                    self.assertIs(window.content_stack.currentWidget(), window.workspace_page)

                with patch("pixelkit.app.QFileDialog.getOpenFileName") as chooser:
                    self.run_page(incoming, window=window)
                is_video = suffix.lower() in VIDEO_SUFFIXES
                self.assertEqual(window.sources, [] if is_video else [source])
                self.assertEqual(window.video_panel.sources, [source] if is_video else [])
                self.assertEqual(window.media_stack.currentIndex(), int(is_video))
                self.assertFalse(self.dismissed(window))
                self.assertFalse(window._onboarding_seen_session)
                chooser.assert_not_called()
                window._maybe_show_onboarding()
                self.assertIsNone(window._onboarding_page)

    def test_invalid_and_mixed_finder_input_does_not_interrupt_page(self):
        image, video, unsupported = self.source("incoming.png"), self.source("incoming.mov"), self.source("notes.txt")

        def incoming(page):
            for paths in ([unsupported, self.root / "missing.png"], [image, video]):
                self.window.open_files(paths)
                self.assertIs(self.window._onboarding_page, page)
                self.assertTrue(page.isVisible())
                self.assertFalse(self.window._onboarding_seen_session)
                self.assertEqual(self.window.sources, [])
                self.assertEqual(self.window.video_panel.sources, [])
            self.skip(page)

        self.run_page(incoming)
        self.assertTrue(self.dismissed())
        self.message.assert_called_once()

    def test_parent_close_interrupts_without_persisting_or_opening_chooser(self):
        with patch("pixelkit.app.QFileDialog.getOpenFileName") as chooser:
            self.run_page(lambda page: self.window.close())
        self.assertFalse(self.dismissed())
        self.assertFalse(self.window._onboarding_seen_session)
        self.assertFalse(self.window.isVisible())
        chooser.assert_not_called()

    def test_modal_startup_warning_defers_and_retries_after_warning_closes(self):
        self.window.show()
        warning = QDialog(self.window)
        warning.setModal(True)
        warning.show()
        self.app.processEvents()
        try:
            self.window._maybe_show_onboarding()
            self.assertIsNone(self.window._onboarding_page)
            self.assertFalse(self.dismissed())
            QTimer.singleShot(10, warning.reject)
            QTest.qWait(220)
            page = self.window._onboarding_page
            self.assertIsInstance(page, OnboardingPage)
            self.assertIs(self.window.content_stack.currentWidget(), page)
            self.skip(page)
            self.app.processEvents()
        finally:
            warning.close()
        self.assertIsNone(self.window._onboarding_page)
        self.assertTrue(self.dismissed())

    def test_unwritable_preferences_dismiss_for_session_without_blocking_user(self):
        blocker = self.source("not-a-folder")
        settings = QSettings(str(blocker / "preferences.ini"), QSettings.Format.IniFormat)
        window = self.make_window(PresetStore(settings))
        self.run_page(self.skip, window=window)
        self.assertTrue(window._onboarding_seen_session)
        self.assertIn("could not be saved", window.status_label.full_text)
        self.assertEqual(blocker.read_bytes(), b"original media fixture")
        window._maybe_show_onboarding()
        self.assertIsNone(window._onboarding_page)
        self.message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
