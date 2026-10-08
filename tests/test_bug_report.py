from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSaveFile

from pixelkit import __version__
from pixelkit.bug_report import BugReportContext, ISSUE_URL, URL_LIMIT, compose_report, diagnostics, github_issue_url, save_report
from pixelkit.report import FileResult


class BugReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def failure(self, error="Converter failed", **values):
        defaults = dict(source=self.root / "Private portrait.png", output=self.root / "Private result.webp", before=12345, after=None, error=error)
        defaults.update(values)
        return FileResult(**defaults)

    def test_general_diagnostics_only_include_environment(self):
        result = diagnostics(BugReportContext())
        self.assertIn(f"PixelKit version: {__version__}", result)
        self.assertIn("OS:", result)
        self.assertIn("Architecture:", result)
        self.assertIn("Mode: Images", result)
        self.assertNotIn("Source", result)
        self.assertNotIn("Error:", result)

    def test_diagnostics_capture_failed_settings_without_media_paths(self):
        file = SimpleNamespace(
            source=Path("/Users/alice/Family album/Family portrait.png"),
            output=Path("/Users/alice/Family album/Family portrait.webp"),
            before=12345, after=None, error="Cannot encode Family portrait.png", stopped=None,
            succeeded=False, elapsed_seconds=1.5, target_bytes=2000000, quality=72,
            processing_settings=(("Resize", "1280 × 720"), ("Output folder", "/Users/alice/Family album")),
        )
        result = diagnostics(BugReportContext(mode="Video", file=file))
        for expected in ("Mode: Video", "Source format: PNG", "Output format: WEBP", "12345 bytes", "1.5 seconds", "2000000 bytes", "Quality used: 72", "Resize: 1280 × 720", "Cannot encode"):
            self.assertIn(expected, result)
        for private in ("alice", "Family album", "Family portrait", "/Users"):
            self.assertNotIn(private, result)

    def test_stopped_successful_or_missing_errors_are_not_failed_file_reports(self):
        files = (
            self.failure(stopped="Cancelled"),
            self.failure(error=None, after=50),
            self.failure(error=None),
        )
        for file in files:
            with self.subTest(file=file):
                result = diagnostics(BugReportContext(file=file))
                self.assertNotIn("Error:", result)
                self.assertNotIn("Source format:", result)

    def test_known_other_batch_names_are_hidden_even_as_bare_basenames(self):
        others = (Path("/Users/alice/Secret folder/Another private photo.jpg"), Path("/Users/alice/Exports/Secret output.webp"))
        error = "Cannot read Another private photo.jpg\nCleanup failed: Secret output.webp"
        result = diagnostics(BugReportContext(file=self.failure(error), protected_paths=others))
        for private in ("Another private photo", "Secret output", "alice", "Secret folder"):
            self.assertNotIn(private, result)
        self.assertIn("Cannot read", result)
        self.assertIn("Cleanup failed", result)

    def test_standalone_usernames_and_named_or_quoted_media_names_are_private(self):
        error = "Permission denied to user reportuser for file Secret Trip.mp4\nCannot open image 'Another family photo.jpg'\nCould not read '/Users/Jane Doe/Secret/image.png' for Jane Doe"
        with patch.dict(os.environ, {"USER": "reportuser", "LOGNAME": "reportuser", "HOME": "/Users/reportuser"}):
            result = diagnostics(BugReportContext(file=self.failure(error)))
        for private in ("reportuser", "Secret Trip", "Another family photo", "Jane Doe"):
            self.assertNotIn(private, result)
        self.assertIn("Permission denied to user", result)
        self.assertIn("Cannot open image", result)
        self.assertIn("Could not read", result)

    def test_general_context_redacts_protected_names_without_selected_failure(self):
        media = Path("/Users/Person/Private image.png")
        result = diagnostics(BugReportContext(mode="Private image.png", protected_paths=(media,)))
        self.assertNotIn("Private image.png", result)
        self.assertIn("Mode: [file]", result)

    def test_paths_and_names_are_redacted_across_platforms(self):
        cases = (
            ("magick: unable to open image '/Users/Ірина/Мої фото/Родина влітку.png': Permission denied\nРодина влітку.png", ("Ірина", "Мої фото", "Родина влітку")),
            (r"ffmpeg: Error opening 'C:\Users\Jane Doe\Secret Plan\Video One.mp4': Invalid data" + "\nVideo One.mp4", ("Jane Doe", "Secret Plan", "Video One")),
            (r"open '\\server\Jane Doe\Video One.mp4': Access is denied" + "\nVideo One.mp4", ("server", "Jane Doe", "Video One")),
            (r"open 'C:\\Users\\Jane Doe\\Secret Plan\\Video One.mp4': Invalid data", ("Jane Doe", "Secret Plan", "Video One")),
            ("Cannot open /opt/secret/Photo One.png: No such file or directory", ("/opt", "secret", "Photo One")),
            ("Cleanup at /private/var/folders/ab/random/.pixelkit-video-ab12.tmp: [Errno 13] Permission denied", ("/private", "random", ".pixelkit-video-ab12")),
            ("Cannot open '~/Private folder/Private.png': Permission denied", ("Private folder", "Private.png", "~/")),
            ("Input file:///Users/admin/My%20Projects/Family%20Trip.mov\nFamily Trip.mov", ("admin", "My%20Projects", "Family%20Trip", "Family Trip")),
            ("Cannot open '/tmp/O'Brien portrait.png': Permission denied", ("O'Brien", "portrait.png")),
        )
        for error, private_values in cases:
            with self.subTest(error=error):
                result = diagnostics(BugReportContext(file=self.failure(error)))
                for private in private_values:
                    self.assertNotIn(private, result)
                for meaningful in ("Permission denied", "Invalid data", "Access is denied", "No such file or directory"):
                    if meaningful in error:
                        self.assertIn(meaningful, result)

    @unittest.skipIf(os.name == "nt", "The filename uses quote characters allowed on POSIX systems.")
    def test_real_batch_stat_failure_hides_filename_with_both_quotes(self):
        from pixelkit.app import BatchWorker

        names = ('''Anna's "Medical diagnosis".png''', '''Family\\Record's "Medical diagnosis".png''')
        for name in names:
            with self.subTest(name=name):
                source = self.root / name
                output = source.with_suffix(".webp")
                worker = BatchWorker([(["magick", str(source), str(output)], output)], self.root)
                reports = []
                worker.finished.connect(reports.append)
                # Exercise the actual missing-file stat OSError, before any
                # converter invocation or temporary output is possible.
                with patch("pixelkit.app.run_magick") as convert:
                    worker.run()
                convert.assert_not_called()
                self.assertEqual(len(reports), 1)
                file = reports[0].files[0]
                self.assertIn("[Errno 2]", file.error)
                self.assertIn("\\'", file.error)
                result = diagnostics(BugReportContext(file=file))
                quote = repr(str(source))[0]
                expected_error = file.error.replace(repr(str(source)), quote + "[file]" + quote)
                self.assertEqual(result.partition("Error:\n")[2], expected_error)
                for private in ("Anna", "Family", "Record", "Medical diagnosis", str(self.root)):
                    self.assertNotIn(private, result)

    def test_known_names_are_hidden_in_single_double_and_repr_quoted_forms(self):
        source = self.root / '''Anna's "Medical diagnosis"\\Private record.png'''
        values = (str(source), source.name)
        representations = (
            repr,
            lambda value: "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'",
            lambda value: '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"',
            lambda value: '"' + value.replace("'", "\\'").replace('"', '\\"') + '"',
        )
        tail = "Decoder returned error code 42\n" + "Дані пошкоджені 🙂 é\n" * 200
        for representation in representations:
            with self.subTest(representation=representation):
                quoted = tuple(representation(value) for value in values)
                error = f"Cannot open {quoted[0]}: Permission denied\nInput name: {quoted[1]}\n{tail}"
                file = self.failure(error, source=source, output=source.with_suffix(".webp"))
                result = diagnostics(BugReportContext(file=file))
                expected = error
                for value in quoted:
                    expected = expected.replace(value, value[0] + "[file]" + value[-1])
                self.assertEqual(result.partition("Error:\n")[2], expected)

    def test_unknown_escaped_paths_and_repeated_basenames_are_private(self):
        cases = (
            ('''/Users/Jane Doe/Anna's "Medical diagnosis".png''', '''Anna's "Medical diagnosis".png'''),
            ('''C:\\Users\\Jane Doe\\Anna's "Medical diagnosis".png''', '''Anna's "Medical diagnosis".png'''),
            ('''\\\\server\\Jane Doe\\Anna's "Medical diagnosis".png''', '''Anna's "Medical diagnosis".png'''),
        )
        tail = "Decoder returned error code 42\nДані пошкоджені 🙂 é"
        for path, name in cases:
            with self.subTest(path=path):
                error = f"Could not read {path!r}: Permission denied\nRepeated input: {name}\nQuoted input: {name!r}\n{tail}"
                result = diagnostics(BugReportContext(file=self.failure(error)))
                self.assertEqual(result.partition("Error:\n")[2], "Could not read '[file]': Permission denied\nRepeated input: [file]\nQuoted input: '[file]'\n" + tail)
                for private in ("Jane Doe", "server", "Anna", "Medical diagnosis"):
                    self.assertNotIn(private, result)

    def test_unquoted_directories_and_temporary_paths_with_spaces_are_private(self):
        cases = (
            ("Could not remove temporary files at /Users/Jane Doe/Output Folder/.pixelkit-video-ab12cd3: Permission denied", ("Jane Doe", "Output Folder", ".pixelkit-video-ab12cd3"), "Permission denied"),
            ("Cleanup failed at /private/tmp/Secret Folder/.pixelkit-image-ab12cd3: Permission denied", ("/private", "Secret Folder", ".pixelkit-image-ab12cd3"), "Permission denied"),
            ("Could not open /opt/My Secret Folder for reading", ("/opt", "My Secret Folder"), "for reading"),
            (r"Cleanup failed at C:\Users\Jane Doe\Secret Folder\.pixelkit-video-ab12cd3: Access is denied", ("Jane Doe", "Secret Folder", ".pixelkit-video-ab12cd3"), "Access is denied"),
            ("Cannot write to /Users/Jane Doe/Output Folder", ("Jane Doe", "Output Folder"), "Cannot write to"),
        )
        for error, private, reason in cases:
            with self.subTest(error=error):
                file = self.failure(error, source=Path("/Users/Jane Doe/Input Folder/Family Trip.png"), output=Path("/Users/Jane Doe/Output Folder/Family Trip.webp"))
                result = diagnostics(BugReportContext(file=file))
                for name in private:
                    self.assertNotIn(name, result)
                self.assertIn(reason, result)

    def test_quoted_directory_before_second_filename_preserves_error_reason(self):
        result = diagnostics(BugReportContext(file=self.failure("Cannot open '/opt/My Secret Folder': Permission denied while reading 'image.png'")))
        self.assertNotIn("My Secret Folder", result)
        self.assertIn("Permission denied while reading", result)

    def test_unquoted_file_paths_preserve_all_following_converter_text(self):
        cases = (
            ("Could not read /tmp/Family Trip.mov because the video is damaged", "because the video is damaged"),
            ("failed reading /tmp/Family Trip.png - insufficient image data in file", "- insufficient image data in file"),
            ("Cannot encode /tmp/Family Trip.png. Decoder returned error code 42", ". Decoder returned error code 42"),
            ("Cannot encode /tmp/Family Trip.png unexpected frame type 4", "unexpected frame type 4"),
            ("Could not open /opt/My Secret Folder for processing", "for processing"),
        )
        for error, reason in cases:
            with self.subTest(error=error):
                result = diagnostics(BugReportContext(file=self.failure(error)))
                self.assertNotIn("Family Trip", result)
                self.assertNotIn("My Secret Folder", result)
                self.assertIn(reason, result)

    def test_dotted_directory_component_does_not_expose_remaining_private_path(self):
        error = "Cannot open /tmp/My.folder Secret/Subfolder/private: Permission denied"
        result = diagnostics(BugReportContext(file=self.failure(error)))
        for private in ("My.folder", "Secret", "Subfolder", "/private"):
            self.assertNotIn(private, result)
        self.assertIn("Permission denied", result)

    def test_file_uri_with_unencoded_spaces_hides_full_path_and_repeated_names(self):
        cases = (
            ("Could not open file:///Users/Jane Doe/Secret Folder/video.mov: Permission denied\nvideo.mov belongs to Jane Doe", "Permission denied"),
            ("Could not open file:///Users/Jane Doe/Secret Folder/video.mov because the video is damaged", "because the video is damaged"),
            ("Could not open file:///Users/Jane Doe/Secret Folder/video.mov. Decoder returned error code 42", ". Decoder returned error code 42"),
            ("Could not open file:///Users/Jane%20Doe/Secret%20Folder/video.mov: Permission denied\nvideo.mov belongs to Jane Doe", "Permission denied"),
        )
        for error, reason in cases:
            with self.subTest(error=error):
                file = self.failure(error, source=Path("/tmp/source.png"), output=Path("/tmp/output.webp"))
                result = diagnostics(BugReportContext(file=file))
                for private in ("Jane Doe", "Jane%20Doe", "Secret Folder", "Secret%20Folder", "video.mov"):
                    self.assertNotIn(private, result)
                self.assertIn(reason, result)

    def test_file_error_labels_are_not_mistaken_for_file_uris(self):
        error = "file: unsupported format\nFile: input.png failed\nfile: failed to decode frame 42"
        result = diagnostics(BugReportContext(file=self.failure(error)))
        self.assertIn("file: unsupported format", result)
        self.assertIn("File: [file] failed", result)
        self.assertIn("file: failed to decode frame 42", result)

    def test_converter_source_locations_and_unicode_errors_are_kept_in_full(self):
        error = "magick: no decode delegate @ error/constitute.c/ReadImage/587\n" + ("Дані пошкоджені 🙂 é\n" * 1800)
        result = diagnostics(BugReportContext(file=self.failure(error)))
        self.assertTrue(result.endswith(error))
        self.assertIn("error/constitute.c/ReadImage/587", result)

    def test_diagnostics_do_not_read_media_resolve_paths_or_invoke_commands(self):
        file = self.failure()
        with patch("pathlib.Path.resolve", side_effect=AssertionError("Resolved a file")), patch("pathlib.Path.stat", side_effect=AssertionError("Read a file")), patch("builtins.open", side_effect=AssertionError("Opened a file")), patch("subprocess.run", side_effect=AssertionError("Ran a command")), patch("socket.gethostname", side_effect=AssertionError("Queried a hostname")):
            self.assertIn("Converter failed", diagnostics(BugReportContext(file=file)))

    def test_manual_fields_and_technical_edits_are_preserved(self):
        title, steps, actual, expected, technical = ("Title 🙂", "  Open /Users/alice/private.jpg\nThen run", "Actual", "Expected", "Manually added /private/custom/path")
        result = compose_report(title, steps, actual, expected, technical)
        for field in (title, steps, actual, expected, technical):
            self.assertIn(field, result)
        self.assertIn("## Steps to reproduce", result)

    def test_issue_url_has_fixed_host_and_only_encoded_title_and_body(self):
        title = "Bug & label=evil # ? 🙂"
        body = "Line one\nУкраїнська /Users/a?b=1&x=y"
        url = github_issue_url(title, body)
        self.assertIsNotNone(url)
        parts = urlsplit(url)
        self.assertEqual(parts.scheme, "https")
        self.assertEqual(parts.netloc, "github.com")
        self.assertEqual(parts.path, "/mykolakhy/PixelKit/issues/new")
        self.assertEqual(parts.fragment, "")
        self.assertEqual(parse_qs(parts.query), {"title": [title], "body": [body]})
        self.assertEqual(ISSUE_URL, "https://github.com/mykolakhy/PixelKit/issues/new")

    def test_issue_url_uses_encoded_total_length_with_exact_boundary(self):
        overhead = len(github_issue_url("", "").encode("utf-8"))
        body = "a" * (URL_LIMIT - overhead)
        self.assertEqual(len(github_issue_url("", body).encode("utf-8")), URL_LIMIT)
        self.assertIsNone(github_issue_url("", body + "a"))
        self.assertIsNone(github_issue_url("", "🙂" * 600))
        self.assertIsNotNone(github_issue_url("", "🙂" * 500))

    def test_save_utf8_replaces_existing_report_atomically(self):
        destination = self.root / "report.md"
        destination.write_text("Previous report")
        report = "Український звіт 🙂\n" * 600
        save_report(destination, report)
        self.assertEqual(destination.read_text(encoding="utf-8"), report)
        self.assertEqual(tuple(self.root.iterdir()), (destination,))

    def test_save_refuses_every_input_output_and_casefold_alias(self):
        input_path, output_path = self.root / "Original.jpg", self.root / "Output.webp"
        for path in (input_path, output_path):
            path.write_bytes(b"private media")
        for destination in (input_path, output_path, self.root / "original.JPG", self.root / "OUTPUT.WEBP"):
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, "input or output"):
                save_report(destination, "report", (input_path, output_path))
        self.assertEqual(input_path.read_bytes(), b"private media")
        self.assertEqual(output_path.read_bytes(), b"private media")

    def test_save_refuses_symlinks_hardlinks_and_symlink_parent(self):
        media = self.root / "private.mov"
        media.write_bytes(b"media")
        symlink, hardlink = self.root / "alias.txt", self.root / "hardlink.txt"
        symlink.symlink_to(media)
        os.link(media, hardlink)
        folder_alias = self.root / "folder-alias"
        folder_alias.symlink_to(self.root, target_is_directory=True)
        for destination in (symlink, hardlink, folder_alias / media.name):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                save_report(destination, "report", (media,))
        self.assertEqual(media.read_bytes(), b"media")

    def test_save_refuses_protected_paths_that_do_not_exist_yet(self):
        destination = self.root / "planned-output.mp4"
        with self.assertRaises(ValueError):
            save_report(destination, "report", (destination,))
        self.assertFalse(destination.exists())

    def test_short_write_preserves_existing_report(self):
        destination = self.root / "report.txt"
        destination.write_text("Previous report")

        class ShortWrite(QSaveFile):
            def write(self, data):
                return super().write(data[:-1])

        with patch("pixelkit.bug_report.QSaveFile", ShortWrite), self.assertRaisesRegex(OSError, "complete report"):
            save_report(destination, "New report")
        self.assertEqual(destination.read_text(), "Previous report")
        self.assertEqual(tuple(self.root.iterdir()), (destination,))

    def test_failed_commit_preserves_existing_report(self):
        destination = self.root / "report.txt"
        destination.write_text("Previous report")

        class FailedCommit(QSaveFile):
            def commit(self):
                return False

        with patch("pixelkit.bug_report.QSaveFile", FailedCommit), self.assertRaisesRegex(OSError, "finish saving"):
            save_report(destination, "New report")
        self.assertEqual(destination.read_text(), "Previous report")

    def test_missing_directory_reports_open_failure(self):
        with self.assertRaisesRegex(OSError, "open the report file"):
            save_report(self.root / "missing" / "report.txt", "Report")

    def test_direct_write_fallback_is_disabled(self):
        with patch("pixelkit.bug_report.QSaveFile") as save_file:
            destination = save_file.return_value
            destination.open.return_value = True
            destination.write.return_value = 6
            destination.commit.return_value = True
            save_report(self.root / "report.txt", "Report")
            destination.setDirectWriteFallback.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()
