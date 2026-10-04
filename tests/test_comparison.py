from __future__ import annotations

import math
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication
from pixelkit.comparison import ComparisonCanvas, ComparisonDialog, load_image
from pixelkit.report import BatchReport, FileResult, ReportDialog
from pixelkit.runtime import ProcessingCancelled, find_magick, run_magick


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'original image.png'
        self.output = self.root / 'result image.png'
        self.original = self.image(80, 40, 'red')
        self.result = self.image(40, 20, 'blue')
        self.original.save(str(self.source))
        self.result.save(str(self.output))

    @staticmethod
    def image(width, height, color):
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor(color))
        return image

    def loaded_dialog(self):
        dialog = ComparisonDialog(self.source, self.output)
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.loader.wait(5000))
        self.app.processEvents()
        self.assertIsNone(dialog.loader.error)
        return dialog

    def test_slider_composes_both_images_and_can_show_each_alone(self):
        canvas = ComparisonCanvas(self.original, self.result)
        self.addCleanup(canvas.close)
        canvas.set_scale(2)
        for value, left, right in [(50, 'red', 'blue'), (0, 'blue', 'blue'), (100, 'red', 'red')]:
            canvas.set_position(value)
            rendered = canvas.grab().toImage()
            self.assertEqual(rendered.pixelColor(5, 5), QColor(left))
            self.assertEqual(rendered.pixelColor(rendered.width() - 6, 5), QColor(right))

    def test_native_loading_and_limits(self):
        self.assertEqual(load_image(self.source).size(), QSize(80, 40))
        with self.assertRaises(ValueError):
            load_image(self.root / 'missing.png')
        with self.assertRaises(ProcessingCancelled):
            load_image(self.source, lambda: True)
        with patch('pixelkit.comparison.QImageReader') as reader:
            reader.return_value.size.return_value = QSize(40000, 40000)
            with self.assertRaisesRegex(ValueError, '32 megapixels'):
                load_image(self.source)
            reader.return_value.read.assert_not_called()

    def test_imagemagick_fallback_decodes_avif_when_qt_cannot(self):
        magick = find_magick()
        if not magick:
            self.skipTest('ImageMagick unavailable')
        output = self.root / 'codec fallback.avif'
        converted = run_magick([magick, str(self.source), str(output)], capture_output=True, timeout=30)
        self.assertEqual(converted.returncode, 0)
        with patch('pixelkit.comparison.QImageReader') as reader:
            reader.return_value.size.return_value = QSize(80, 40)
            reader.return_value.read.return_value = QImage()
            decoded = load_image(output)
        self.assertEqual(decoded.size(), QSize(80, 40))
        self.assertGreater(decoded.pixelColor(0, 0).red(), 200)
        self.assertLess(decoded.pixelColor(0, 0).blue(), 30)

    def test_fit_and_actual_pixels_respect_device_pixel_ratio(self):
        # A larger image must scroll at 100%, but fit inside the viewport.
        self.image(1601, 901, 'blue').save(str(self.output))
        dialog = self.loaded_dialog()
        dialog.resize(620, 420)
        dialog.show()
        self.app.processEvents()
        dialog._update_scale()
        self.assertLessEqual(dialog.canvas.width(), dialog.scroll.viewport().width())
        self.assertLessEqual(dialog.canvas.height(), dialog.scroll.viewport().height())
        dialog.actual_button.click()
        self.app.processEvents()
        ratio = dialog.canvas.devicePixelRatioF()
        self.assertEqual(dialog.canvas.width(), math.ceil(1601 / ratio))
        self.assertEqual(dialog.canvas.height(), math.ceil(901 / ratio))
        self.assertAlmostEqual(dialog.canvas.scale * ratio, 1)
        dialog.slider.setValue(75)
        self.assertEqual(dialog.canvas.position, 75)
        dialog.fit_button.click()
        self.app.processEvents()
        dialog._update_scale()
        self.assertLessEqual(dialog.canvas.width(), dialog.scroll.viewport().width())

    def test_failed_load_shows_error_and_can_close(self):
        self.source.unlink()
        dialog = ComparisonDialog(self.source, self.output)
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.loader.wait(5000))
        self.app.processEvents()
        self.assertIn('Image no longer exists', dialog.loading.text())
        dialog.loading_close.click()
        self.assertFalse(dialog.loader.isRunning())

    def test_closing_loading_dialog_cancels_and_waits_for_worker(self):
        started = Event()
        def slow_load(path, cancelled):
            started.set()
            while not cancelled():
                Event().wait(0.01)
            raise ProcessingCancelled()
        with patch('pixelkit.comparison.load_image', side_effect=slow_load):
            dialog = ComparisonDialog(self.source, self.output)
            self.addCleanup(dialog.close)
            dialog.show()
            self.assertTrue(started.wait(2))
            dialog.reject()
            self.assertTrue(dialog.closing)
            self.assertTrue(dialog.loader.wait(5000))
            self.app.processEvents()
            self.assertFalse(dialog.isVisible())

    def test_report_only_offers_comparison_for_available_successful_files(self):
        good = FileResult(self.source, self.output, 100, 50)
        bad = FileResult(self.source, self.output, 100, None, 'Failed')
        dialog = ReportDialog(BatchReport((good, bad), self.root))
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.compare_button.isEnabled())
        dialog.table.selectRow(0)
        self.assertTrue(dialog.compare_button.isEnabled())
        with patch('pixelkit.report.ComparisonDialog') as compare:
            dialog.compare_button.click()
            compare.assert_called_once_with(self.source, self.output, dialog)
            compare.return_value.exec.assert_called_once()
        dialog.table.selectRow(1)
        self.assertFalse(dialog.compare_button.isEnabled())
        self.output.unlink()
        dialog.table.selectRow(0)
        self.assertFalse(dialog.compare_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
