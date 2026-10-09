from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QCoreApplication, QMimeData, QPointF, QSettings, QUrl, Qt
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QDialog, QLabel

from pixelkit.app import ImageMagickStudio, PixelKitApplication
from pixelkit.presets import PresetStore


class DragDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = PixelKitApplication.instance() or PixelKitApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        settings = QSettings(str(self.root / "preferences.ini"), QSettings.Format.IniFormat)
        settings.setValue("onboarding/dismissed", True)
        with patch("pixelkit.app.find_magick", return_value="magick"), patch(
            "pixelkit.video_panel.find_ffmpeg", return_value="ffmpeg"
        ), patch("pixelkit.video_panel.find_ffprobe", return_value="ffprobe"):
            self.window = ImageMagickStudio(PresetStore(settings))
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.worker = None
        self.window.video_panel.worker = None
        self.window._set_processing_state(False)
        self.window.video_panel._set_busy(False)
        self.window.close()
        self.app.processEvents()

    def file(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"Original contents of {name}".encode())
        return path

    def select_panel(self, index):
        self.window.mode_buttons[index].click()
        self.app.processEvents()
        return self.window if index == 0 else self.window.video_panel

    def mime(self, paths=(), text=None):
        mime = QMimeData()
        if paths:
            mime.setUrls([QUrl.fromLocalFile(str(path)) if isinstance(path, Path) else QUrl(path) for path in paths])
        if text is not None:
            mime.setText(text)
        return mime

    def route_drop(self, widget, mime, *, supported=True, drop_accepted=True,
                   actions=Qt.DropAction.CopyAction, modifiers=Qt.KeyboardModifier.NoModifier,
                   during_drop=None):
        """Exercise Qt's viewport routing and require acceptance before dropping.

        Calling dropEvent or emitting files_dropped bypasses the inherited
        dragMoveEvent that previously rejected external files.
        """
        target = widget.viewport() if hasattr(widget, "viewport") else widget
        center = target.rect().center()
        entry = QDragEnterEvent(center, actions, mime, Qt.MouseButton.LeftButton, modifiers)
        QCoreApplication.sendEvent(target, entry)
        self.assertEqual(entry.isAccepted(), supported, "DragEnter acceptance")
        if supported:
            self.assertEqual(entry.dropAction(), Qt.DropAction.CopyAction)
        # A native drag moves repeatedly across empty space and existing rows.
        for point in (center, target.rect().topLeft() + center / 2):
            move = QDragMoveEvent(point, actions, mime, Qt.MouseButton.LeftButton, modifiers)
            QCoreApplication.sendEvent(target, move)
            self.assertEqual(move.isAccepted(), supported, "DragMove acceptance")
            if supported:
                self.assertEqual(move.dropAction(), Qt.DropAction.CopyAction)
        drop = QDropEvent(QPointF(center), actions, mime, Qt.MouseButton.LeftButton, modifiers)
        with during_drop if during_drop is not None else nullcontext():
            QCoreApplication.sendEvent(target, drop)
        self.assertEqual(drop.isAccepted(), supported and drop_accepted, "Drop acceptance")
        if drop.isAccepted():
            self.assertEqual(drop.dropAction(), Qt.DropAction.CopyAction)

    def test_image_drop_accepts_files_and_folders_appends_and_preserves_originals(self):
        panel = self.select_panel(0)
        first, second = self.file("first image.PNG"), self.file("second.webp")
        third = self.file("incoming/third.JPG")
        self.file("incoming/ignored.txt")
        self.file("incoming/nested/ignored.jpg")
        originals = {path: hashlib.sha256(path.read_bytes()).digest() for path in (first, second, third)}
        self.route_drop(panel.source_list, self.mime([first]))
        self.assertEqual(panel.sources, [first])
        chosen = self.root / "chosen output" / "share.webp"
        panel.output_edit.setText(str(chosen))
        panel.output_edit.textEdited.emit(str(chosen))
        self.route_drop(panel.source_list, self.mime([first, second, third.parent]))
        self.assertEqual(panel.sources, [first, second, third])
        self.assertEqual(Path(panel.output_edit.text()), chosen.parent)
        self.route_drop(panel.source_list, self.mime([third.parent, first]))
        self.assertEqual(panel.sources, [first, second, third])
        self.assertEqual(panel.source_list.count(), 3)
        self.assertEqual(Path(panel.output_edit.text()), chosen.parent)
        self.assertFalse(panel.source_list.placeholder.isVisible())
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).digest() for path in originals}, originals)
        self.assertFalse(chosen.parent.exists())

    def test_video_drop_accepts_files_and_folders_appends_and_preserves_originals(self):
        panel = self.select_panel(1)
        first, second = self.file("first video.MOV"), self.file("second.mp4")
        third = self.file("incoming/third.M4V")
        self.file("incoming/ignored.png")
        self.file("incoming/nested/ignored.mp4")
        originals = {path: hashlib.sha256(path.read_bytes()).digest() for path in (first, second, third)}
        self.route_drop(panel.source_list, self.mime([first]))
        self.assertEqual(panel.sources, [first])
        chosen = self.root / "chosen output" / "share.mp4"
        panel.output_edit.setText(str(chosen))
        self.route_drop(panel.source_list, self.mime([first, second, third.parent]))
        self.assertEqual(panel.sources, [first, second, third])
        self.assertEqual(Path(panel.output_edit.text()), chosen.parent)
        self.route_drop(panel.source_list, self.mime([third.parent, first]))
        self.assertEqual(panel.sources, [first, second, third])
        self.assertEqual(panel.source_list.count(), 3)
        self.assertEqual(Path(panel.output_edit.text()), chosen.parent)
        self.assertFalse(panel.source_list.placeholder.isVisible())
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).digest() for path in originals}, originals)
        self.assertFalse(chosen.parent.exists())

    def test_import_uses_copy_even_when_the_source_proposes_move(self):
        for index, name in ((0, "image.png"), (1, "video.mov")):
            with self.subTest(media=index):
                panel = self.select_panel(index)
                source = self.file(name)
                original = source.read_bytes()
                self.route_drop(panel.source_list, self.mime([source]),
                                actions=Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
                                modifiers=Qt.KeyboardModifier.ShiftModifier)
                self.assertEqual(panel.sources, [source])
                self.assertEqual(source.read_bytes(), original)

    def test_unsupported_missing_remote_text_and_move_only_drags_are_rejected(self):
        unsupported = self.file("notes.txt")
        image, video = self.file("image.png"), self.file("video.mov")
        for index, supported in ((0, image), (1, video)):
            panel = self.select_panel(index)
            for name, mime, actions in (
                ("unsupported", self.mime([unsupported]), Qt.DropAction.CopyAction),
                ("missing", self.mime([self.root / supported.name.replace(".", "-missing.")]), Qt.DropAction.CopyAction),
                ("remote", self.mime([f"https://example.invalid/{supported.name}"]), Qt.DropAction.CopyAction),
                ("text", self.mime(text=str(supported)), Qt.DropAction.CopyAction),
                ("move only", self.mime([supported]), Qt.DropAction.MoveAction),
            ):
                with self.subTest(media=index, drag=name):
                    self.route_drop(panel.source_list, mime, supported=False, actions=actions)
                    self.assertEqual(panel.sources, [])
                    self.assertEqual(panel.output_edit.text(), "")

    def test_busy_queues_reject_drops_and_accept_them_after_processing(self):
        for index, names in ((0, ("one.png", "two.jpg")), (1, ("one.mov", "two.mp4"))):
            with self.subTest(media=index):
                panel = self.select_panel(index)
                first, second = (self.file(name) for name in names)
                self.route_drop(panel.source_list, self.mime([first]))
                destination = panel.output_edit.text()
                set_busy = panel._set_processing_state if index == 0 else panel._set_busy
                set_busy(True)
                self.route_drop(panel.source_list, self.mime([second]), supported=False)
                self.assertEqual(panel.sources, [first])
                self.assertEqual(panel.output_edit.text(), destination)
                set_busy(False)
                self.route_drop(panel.source_list, self.mime([second]))
                self.assertEqual(panel.sources, [first, second])

    def test_a_folder_becoming_unreadable_during_drop_does_not_lose_other_files(self):
        source = self.file("image.png")
        folder = self.file("incoming/another.png").parent
        panel = self.select_panel(0)
        with self.subTest(folder_only=True):
            self.route_drop(panel.source_list, self.mime([folder]), drop_accepted=False,
                            during_drop=patch.object(Path, "iterdir", side_effect=PermissionError("Folder unavailable")))
            self.assertEqual(panel.sources, [])
        with self.subTest(folder_only=False):
            self.route_drop(panel.source_list, self.mime([folder, source]),
                            during_drop=patch.object(Path, "iterdir", side_effect=PermissionError("Folder unavailable")))
            self.assertEqual(panel.sources, [source])
        self.assertTrue(source.exists())

    def test_opposite_media_drop_switches_after_acceptance_and_preserves_both_queues(self):
        first_image, first_video = self.file("first.png"), self.file("first.mov")
        self.window._append_sources([first_image])
        self.window.video_panel.add_sources([first_video])
        for active, name in ((0, "second.mp4"), (1, "second.jpg")):
            with self.subTest(active=active):
                source = self.file(name)
                old_images = list(self.window.sources)
                old_videos = list(self.window.video_panel.sources)
                panel = self.select_panel(active)
                mime = self.mime([source])
                target = panel.source_list.viewport()
                enter = QDragEnterEvent(target.rect().center(), Qt.DropAction.CopyAction, mime,
                                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                QCoreApplication.sendEvent(target, enter)
                self.assertTrue(enter.isAccepted())
                self.assertEqual(self.window.media_stack.currentIndex(), active)
                move = QDragMoveEvent(target.rect().center(), Qt.DropAction.CopyAction, mime,
                                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                QCoreApplication.sendEvent(target, move)
                self.assertTrue(move.isAccepted())
                self.assertEqual(self.window.media_stack.currentIndex(), active)
                drop = QDropEvent(QPointF(target.rect().center()), Qt.DropAction.CopyAction, mime,
                                 Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                QCoreApplication.sendEvent(target, drop)
                self.assertTrue(drop.isAccepted())
                self.assertEqual(self.window.media_stack.currentIndex(), 1 - active)
                self.assertEqual([button.isChecked() for button in self.window.mode_buttons],
                                 [active == 1, active == 0])
                self.assertEqual(self.window.sources, [*old_images, *([source] if active == 1 else [])])
                self.assertEqual(self.window.video_panel.sources, [*old_videos, *([source] if active == 0 else [])])

    def test_workspace_header_settings_and_native_edit_targets_route_media(self):
        header = self.window.findChild(QLabel, "appTitle")
        for number, target in enumerate((self.window, self.window.workspace_page, header,
                                         self.window.output_edit, self.window.background_edit,
                                         self.window.preset_combo)):
            with self.subTest(target=type(target).__name__, number=number):
                self.select_panel(0)
                source = self.file(f"drop-{number}.mov")
                before = (self.window.output_edit.text(), self.window.background_edit.text(),
                          self.window.preset_combo.currentIndex())
                self.route_drop(target, self.mime([source]))
                self.assertEqual(self.window.media_stack.currentIndex(), 1)
                self.assertIn(source, self.window.video_panel.sources)
                self.assertEqual(before, (self.window.output_edit.text(), self.window.background_edit.text(),
                                          self.window.preset_combo.currentIndex()))

    def test_mixed_drop_populates_both_queues_and_keeps_current_mode(self):
        image, video = self.file("mixed/photo.png"), self.file("mixed/clip.mov")
        self.file("mixed/nested/ignored.jpg")
        for index in (0, 1):
            with self.subTest(mode=index):
                panel = self.select_panel(index)
                self.route_drop(panel.source_list, self.mime([video, image.parent, image, video]))
                self.assertEqual(self.window.sources, [image])
                self.assertEqual(self.window.video_panel.sources, [video])
                self.assertEqual(self.window.media_stack.currentIndex(), index)
                self.assertTrue(self.window.mode_buttons[index].isChecked())

    def test_custom_destinations_processing_settings_and_reports_are_preserved(self):
        self.window._append_sources([self.file("first.png")])
        self.window.video_panel.add_sources([self.file("first.mov")])
        self.window.output_edit.setText(str(self.root / "images out" / "chosen.webp"))
        self.window.default_output = False
        self.window.video_panel.output_edit.setText(str(self.root / "videos out" / "chosen.mp4"))
        self.window.format_combo.setCurrentText("WEBP")
        self.window.video_panel.preset_combo.setCurrentIndex(2)
        image_report, video_report = object(), object()
        self.window.last_report, self.window.video_panel.last_report = image_report, video_report
        self.route_drop(self.window.source_list, self.mime([self.file("another.png"), self.file("another.mov")]))
        self.assertEqual(Path(self.window.output_edit.text()), self.root / "images out")
        self.assertEqual(Path(self.window.video_panel.output_edit.text()), self.root / "videos out")
        self.assertFalse(self.window.default_output)
        self.assertEqual(self.window.format_combo.currentText(), "WEBP")
        self.assertEqual(self.window.video_panel.preset_combo.currentIndex(), 2)
        self.assertIs(self.window.last_report, image_report)
        self.assertIs(self.window.video_panel.last_report, video_report)

    def test_either_processing_flag_or_running_worker_rejects_all_drop_targets(self):
        source = self.file("incoming.mov")
        for panel in (self.window, self.window.video_panel):
            for state in ("processing", "worker"):
                with self.subTest(panel=type(panel).__name__, state=state):
                    if state == "processing":
                        panel.processing = True
                    else:
                        panel.worker = type("RunningWorker", (), {"isRunning": lambda self: True})()
                    for target in (self.window.source_list, self.window.output_edit, self.window):
                        self.route_drop(target, self.mime([source]), supported=False)
                    self.assertEqual(self.window.sources, [])
                    self.assertEqual(self.window.video_panel.sources, [])
                    self.assertEqual(self.window.media_stack.currentIndex(), 0)
                    panel.processing, panel.worker = False, None

    def test_busy_start_after_drag_enter_prevents_drop_and_switch(self):
        source = self.file("incoming.mov")
        for panel in (self.window, self.window.video_panel):
            with self.subTest(panel=type(panel).__name__):
                self.route_drop(self.window.source_list, self.mime([source]), drop_accepted=False,
                                during_drop=patch.object(panel, "processing", True))
                self.assertEqual(self.window.video_panel.sources, [])
                self.assertEqual(self.window.media_stack.currentIndex(), 0)

    def test_onboarding_closing_and_modal_dialogs_block_workspace_imports(self):
        source = self.file("incoming.mov")
        self.window._show_onboarding()
        self.route_drop(self.window, self.mime([source]), supported=False)
        self.assertIsNotNone(self.window._onboarding_page)
        self.window._finish_onboarding(interrupted=True)
        with patch.object(self.window, "_closing", True):
            self.route_drop(self.window, self.mime([source]), supported=False)
        dialog = QDialog(self.window)
        dialog.setModal(True)
        dialog.show()
        self.app.processEvents()
        try:
            self.assertIs(self.app.activeModalWidget(), dialog)
            self.route_drop(self.window.output_edit, self.mime([source]), supported=False)
        finally:
            dialog.close()
        self.assertEqual(self.window.video_panel.sources, [])
        self.assertEqual(self.window.media_stack.currentIndex(), 0)

    def test_active_dropdown_popup_blocks_workspace_imports_without_selection_changes(self):
        source = self.file("incoming.mov")
        combo = self.window.preset_combo
        previous = combo.currentIndex()
        combo.showPopup()
        self.app.processEvents()
        try:
            self.assertIsNotNone(self.app.activePopupWidget())
            self.route_drop(self.window, self.mime([source]), supported=False)
        finally:
            combo.hidePopup()
        self.assertEqual(combo.currentIndex(), previous)
        self.assertEqual(self.window.video_panel.sources, [])

    def test_append_preserves_missing_queued_originals_in_both_media(self):
        first_image, first_video = self.file("first.png"), self.file("first.mov")
        self.window._append_sources([first_image])
        self.window.video_panel.add_sources([first_video])
        first_image.unlink()
        first_video.unlink()
        second_image, second_video = self.file("second.jpg"), self.file("second.mp4")
        self.route_drop(self.window, self.mime([second_image, second_video]))
        self.assertEqual(self.window.sources, [first_image, second_image])
        self.assertEqual(self.window.video_panel.sources, [first_video, second_video])
        self.assertEqual(self.window.source_list.count(), 2)
        self.assertEqual(self.window.video_panel.source_list.count(), 2)

    def test_drop_preserves_queued_image_replaced_by_a_symlink(self):
        for replacement in ("self loop", "another image"):
            with self.subTest(replacement=replacement):
                queued = self.file(f"{replacement}/queued.png")
                incoming = self.file(f"{replacement}/incoming.png")
                self.window._set_sources([queued])
                destination = self.root / replacement / "exports" / "chosen.webp"
                self.window.output_edit.setText(str(destination))
                self.window.default_output = False
                queued.unlink()
                target = queued if replacement == "self loop" else self.file(f"{replacement}/replacement.jpg")
                queued.symlink_to(target)
                panel = self.select_panel(1)
                self.route_drop(panel.source_list, self.mime([incoming]))
                self.assertEqual(self.window.sources, [queued, incoming])
                self.assertEqual(self.window.source_list.count(), 2)
                self.assertEqual(self.window.source_list.item(0).toolTip(), str(queued))
                self.assertEqual(Path(self.window.output_edit.text()), destination.parent)
                self.assertFalse(self.window.default_output)
                self.assertEqual(self.window.media_stack.currentIndex(), 0)

    def test_deleted_file_is_revalidated_at_drop_before_tab_switch(self):
        source = self.file("incoming.mov")
        original_is_file = Path.is_file
        def disappeared(path):
            return False if path == source else original_is_file(path)
        self.route_drop(self.window.source_list, self.mime([source]), drop_accepted=False,
                        during_drop=patch.object(Path, "is_file", disappeared))
        self.assertEqual(self.window.video_panel.sources, [])
        self.assertEqual(self.window.media_stack.currentIndex(), 0)

    def test_unreadable_folder_child_does_not_discard_supported_siblings(self):
        good, unavailable = self.file("folder/one.png"), self.file("folder/two.mov")
        original_is_file = Path.is_file
        def unreadable(path):
            if path == unavailable:
                raise PermissionError("File unavailable")
            return original_is_file(path)
        with patch.object(Path, "is_file", unreadable):
            self.route_drop(self.window, self.mime([good.parent]))
        self.assertEqual(self.window.sources, [good])
        self.assertEqual(self.window.video_panel.sources, [])

    def test_alias_duplicates_and_supported_only_folders(self):
        image = self.file("photo.png")
        alias = self.root / "alias.png"
        alias.symlink_to(image)
        unsupported_folder = self.file("unsupported/notes.txt").parent
        self.route_drop(self.window, self.mime([unsupported_folder]), supported=False)
        self.route_drop(self.window, self.mime([image, alias, image.parent]))
        self.assertEqual(self.window.sources, [image])

    def test_auto_switch_keeps_menu_action_and_selected_mode_consistent(self):
        video = self.file("clip.mov")
        self.route_drop(self.window.source_list, self.mime([video]))
        with patch.object(self.window.video_panel, "choose_many") as videos, patch.object(self.window, "_choose_many") as images:
            self.window._open_current_mode()
            videos.assert_called_once_with()
            images.assert_not_called()
        self.assertTrue(self.window.mode_buttons[1].isChecked())
        self.assertTrue(self.window.save_action.isEnabled())
        image = self.file("photo.png")
        self.route_drop(self.window.video_panel.source_list, self.mime([image]))
        with patch.object(self.window.video_panel, "start_processing") as videos, patch.object(self.window, "_start_processing") as images:
            self.window._process_current_mode()
            images.assert_called_once_with()
            videos.assert_not_called()
        self.assertTrue(self.window.mode_buttons[0].isChecked())
        self.assertTrue(self.window.save_action.isEnabled())


if __name__ == "__main__":
    unittest.main()
