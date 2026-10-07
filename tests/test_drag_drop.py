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
        with patch("pixelkit.app.find_magick", return_value="magick"), patch(
            "pixelkit.video_panel.find_ffmpeg", return_value="ffmpeg"
        ), patch("pixelkit.video_panel.find_ffprobe", return_value="ffprobe"):
            self.window = ImageMagickStudio(PresetStore(settings))
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.close_window)

    def close_window(self):
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
        center = widget.viewport().rect().center()
        entry = QDragEnterEvent(center, actions, mime, Qt.MouseButton.LeftButton, modifiers)
        QCoreApplication.sendEvent(widget.viewport(), entry)
        self.assertEqual(entry.isAccepted(), supported, "DragEnter acceptance")
        if supported:
            self.assertEqual(entry.dropAction(), Qt.DropAction.CopyAction)
        # A native drag moves repeatedly across empty space and existing rows.
        for point in (center, widget.viewport().rect().topLeft() + center / 2):
            move = QDragMoveEvent(point, actions, mime, Qt.MouseButton.LeftButton, modifiers)
            QCoreApplication.sendEvent(widget.viewport(), move)
            self.assertEqual(move.isAccepted(), supported, "DragMove acceptance")
            if supported:
                self.assertEqual(move.dropAction(), Qt.DropAction.CopyAction)
        drop = QDropEvent(QPointF(center), actions, mime, Qt.MouseButton.LeftButton, modifiers)
        with during_drop if during_drop is not None else nullcontext():
            QCoreApplication.sendEvent(widget.viewport(), drop)
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
        for index, supported, wrong_type in ((0, image, video), (1, video, image)):
            panel = self.select_panel(index)
            for name, mime, actions in (
                ("unsupported", self.mime([unsupported]), Qt.DropAction.CopyAction),
                ("wrong media", self.mime([wrong_type]), Qt.DropAction.CopyAction),
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


if __name__ == "__main__":
    unittest.main()
