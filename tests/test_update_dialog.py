from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QIODevice, QObject, QTimer, Qt
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from pixelkit.update_dialog import CHECK_TIMEOUT_MS, MAX_RESPONSE_BYTES, UpdateDialog
from pixelkit.updates import LATEST_RELEASE_API, RELEASES_URL


def release_payload(version="1.1.0", arch="arm64", *, with_installer=True):
    name = f"PixelKit-{version}-macOS-{arch}.dmg"
    return {
        "tag_name": f"v{version}",
        "html_url": f"{RELEASES_URL}/tag/v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [{
            "name": name,
            "state": "uploaded",
            "size": 42,
            "browser_download_url": f"{RELEASES_URL}/download/v{version}/{name}",
        }] if with_installer else [],
    }


class FakeReply(QNetworkReply):
    """A sequential Qt reply, including abort's synchronous finished signal."""

    def __init__(self, parent, request):
        super().__init__(parent)
        self._unread = bytearray()
        self.aborted = False
        self.deletion_requested = False
        self.setRequest(request)
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def isSequential(self):
        return True

    def bytesAvailable(self):
        return len(self._unread) + super().bytesAvailable()

    def readData(self, maxlen):
        chunk = bytes(self._unread[:maxlen])
        del self._unread[:maxlen]
        return chunk

    def push(self, body):
        self._unread.extend(body)
        self.readyRead.emit()

    def finish(self, *, status=200, error=QNetworkReply.NetworkError.NoError):
        if status is not None:
            self.setAttribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute, status)
        if error != QNetworkReply.NetworkError.NoError:
            self.setError(error, "Injected failure")
        self.setFinished(True)
        self.finished.emit()

    def abort(self):
        self.aborted = True
        self.setError(QNetworkReply.NetworkError.OperationCanceledError, "Cancelled")
        self.setFinished(True)
        self.finished.emit()

    def deleteLater(self):
        # Keep the fake alive so tests can deliberately deliver stale signals.
        # Its QObject parent still owns its lifetime.
        self.deletion_requested = True


class FakeManager(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.requests = []
        self.replies = []

    def get(self, request):
        self.requests.append(request)
        reply = FakeReply(self, request)
        self.replies.append(reply)
        return reply


class UpdateDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        manager_patch = patch("pixelkit.update_dialog.QNetworkAccessManager", FakeManager)
        manager_patch.start()
        self.addCleanup(manager_patch.stop)
        self.dialog = UpdateDialog("1.0.0", system="darwin", machine="arm64")
        self.addCleanup(self.dialog.close)
        self.manager = self.dialog._manager
        self.transitions = []
        self.dialog.checkingChanged.connect(self.transitions.append)

    def start(self):
        self.dialog.check_for_updates()
        return self.manager.replies[-1]

    def complete(self, payload=None, *, status=200, error=QNetworkReply.NetworkError.NoError):
        reply = self.start()
        if payload is not None:
            reply.push(json.dumps(payload).encode("utf-8"))
        reply.finish(status=status, error=error)
        return reply

    def assert_finished(self, reply):
        self.assertFalse(self.dialog.is_checking)
        self.assertFalse(self.dialog._deadline.isActive())
        self.assertIsNone(self.dialog._reply)
        self.assertTrue(reply.deletion_requested)
        self.assertTrue(self.dialog.retry_button.isEnabled())

    def test_construction_and_show_do_not_request_or_open_browser(self):
        with patch("pixelkit.update_dialog.QDesktopServices.openUrl") as browser:
            self.dialog.show()
            self.app.processEvents()
        self.assertEqual(self.manager.requests, [])
        browser.assert_not_called()
        self.assertFalse(self.dialog.isModal())
        self.assertEqual(self.dialog.windowModality(), Qt.WindowModality.NonModal)
        self.assertIs(self.manager.parent(), self.dialog)
        self.assertIs(self.dialog._deadline.parent(), self.dialog)
        for label in self.dialog.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
            self.assertTrue(label.wordWrap())
        for button in (self.dialog.action_button, self.dialog.retry_button, self.dialog.close_button):
            self.assertTrue(button.accessibleName())
            self.assertTrue(button.accessibleDescription())

    def test_duplicate_checks_make_one_bounded_public_request(self):
        reply = self.start()
        self.dialog.check_for_updates()
        self.dialog.retry_button.click()
        self.assertEqual(len(self.manager.requests), 1)
        self.assertEqual(self.transitions, [True])
        self.assertEqual(self.dialog.state, "checking")
        self.assertTrue(self.dialog.is_checking)
        self.assertFalse(self.dialog.retry_button.isEnabled())
        request = self.manager.requests[0]
        self.assertEqual(request.url().toString(), LATEST_RELEASE_API)
        self.assertEqual(bytes(request.rawHeader(b"Accept")), b"application/vnd.github+json")
        self.assertEqual(bytes(request.rawHeader(b"User-Agent")), b"PixelKit/1.0.0")
        self.assertFalse(request.hasRawHeader(b"Authorization"))
        self.assertEqual(request.transferTimeout(), CHECK_TIMEOUT_MS)
        self.assertEqual(self.dialog._deadline.interval(), CHECK_TIMEOUT_MS)
        self.assertEqual(reply.readBufferSize(), MAX_RESPONSE_BYTES + 1)

    def test_request_keeps_the_main_event_loop_responsive(self):
        parent = QWidget()
        self.addCleanup(parent.close)
        parent.show()
        self.start()
        delivered = []
        QTimer.singleShot(0, lambda: delivered.append(True))
        self.app.processEvents()
        self.assertEqual(delivered, [True])
        self.assertTrue(parent.isEnabled())
        self.assertTrue(self.dialog.is_checking)

    def test_newer_release_drains_chunks_and_opens_only_explicit_download(self):
        payload = release_payload()
        reply = self.start()
        body = json.dumps(payload).encode("utf-8")
        with patch("pixelkit.update_dialog.QDesktopServices.openUrl", return_value=True) as browser:
            reply.push(body[:15])
            self.assertEqual(reply.bytesAvailable(), 0)
            reply.push(body[15:])
            reply.finish()
            browser.assert_not_called()
            self.assertEqual(self.dialog.state, "available")
            self.assertIn("Installed version: 1.0.0", self.dialog.current_version_label.text())
            self.assertIn("Latest version: 1.1.0", self.dialog.latest_version_label.text())
            self.assertIn("Apple silicon", self.dialog.action_button.text())
            self.dialog.action_button.click()
        browser.assert_called_once()
        self.assertEqual(browser.call_args.args[0].toString(), payload["assets"][0]["browser_download_url"])
        self.assertEqual(self.transitions, [True, False])
        self.assert_finished(reply)

    def test_intel_installer_is_selected_for_this_computer(self):
        self.dialog._machine = "x86_64"
        self.complete(release_payload(arch="x86_64"))
        self.assertIn("Intel", self.dialog.action_button.text())
        self.assertIn("x86_64.dmg", self.dialog._link_url.toString())

    def test_equal_and_older_releases_report_current_version(self):
        for version in ("1.0.0", "0.9.9"):
            with self.subTest(version=version):
                reply = self.complete(release_payload(version))
                self.assertEqual(self.dialog.state, "current")
                self.assertIn("up to date", self.dialog.heading.text())
                self.assertEqual(self.dialog._link_url.toString(), f"{RELEASES_URL}/tag/v{version}")
                self.assert_finished(reply)

    def test_missing_installer_or_unknown_platform_offers_release_page(self):
        for system, arch, installer in (("darwin", "arm64", False), ("win32", "arm64", True), ("darwin", "mips", True)):
            with self.subTest(system=system, arch=arch, installer=installer):
                self.dialog._system = system
                self.dialog._machine = arch
                self.complete(release_payload(with_installer=installer))
                self.assertEqual(self.dialog.state, "no_installer")
                self.assertIn("no matching installer", self.dialog.status_label.text())
                self.assertEqual(self.dialog.action_button.text(), "View &release")
                self.assertEqual(self.dialog._link_url.toString(), f"{RELEASES_URL}/tag/v1.1.0")

    def test_404_is_no_stable_release_even_with_qt_http_error(self):
        reply = self.complete({"message": "Not Found"}, status=404, error=QNetworkReply.NetworkError.ContentNotFoundError)
        self.assertEqual(self.dialog.state, "no_releases")
        self.assertIn("No stable releases", self.dialog.heading.text())
        self.assertEqual(self.dialog._link_url.toString(), RELEASES_URL)
        self.assert_finished(reply)

    def test_rate_limit_and_server_failures_have_friendly_retry_states(self):
        for status, state in ((403, "rate_limited"), (429, "rate_limited"), (500, "offline"), (302, "offline")):
            with self.subTest(status=status):
                reply = self.complete({"message": "Failure"}, status=status, error=QNetworkReply.NetworkError.UnknownContentError)
                self.assertEqual(self.dialog.state, state)
                self.assertIn("try again later", self.dialog.status_label.text().lower())
                self.assertIsNone(self.dialog._link_url)
                self.assert_finished(reply)

    def test_offline_timeout_and_ssl_errors_do_not_show_success(self):
        cases = (
            (QNetworkReply.NetworkError.HostNotFoundError, "offline", "internet connection"),
            (QNetworkReply.NetworkError.TimeoutError, "timeout", "ten seconds"),
            (QNetworkReply.NetworkError.ProxyTimeoutError, "timeout", "ten seconds"),
            (QNetworkReply.NetworkError.SslHandshakeFailedError, "offline", "secure connection"),
        )
        for error, state, message in cases:
            with self.subTest(error=error):
                reply = self.start()
                with patch.object(reply, "ignoreSslErrors") as ssl_bypass:
                    reply.finish(status=None, error=error)
                ssl_bypass.assert_not_called()
                self.assertEqual(self.dialog.state, state)
                self.assertIn(message, self.dialog.status_label.text())
                self.assertIsNone(self.dialog._link_url)
                self.assert_finished(reply)

    def test_malformed_and_unsafe_responses_cannot_enable_release_link(self):
        bad_payloads = [b"not json", b"\xff", b"[]", b"{" + b"[" * 2000]
        for changes in ({"prerelease": True}, {"html_url": "https://evil.example/release"}, {"assets": None}):
            payload = release_payload()
            payload.update(changes)
            bad_payloads.append(json.dumps(payload).encode("utf-8"))
        for body in bad_payloads:
            with self.subTest(body=body[:30]):
                reply = self.start()
                with patch("pixelkit.update_dialog.QDesktopServices.openUrl") as browser:
                    reply.push(body)
                    reply.finish()
                    self.dialog._open_link()
                browser.assert_not_called()
                self.assertEqual(self.dialog.state, "malformed")
                self.assertIsNone(self.dialog._link_url)
                self.assert_finished(reply)

    def test_missing_http_status_is_an_unexpected_response(self):
        reply = self.complete(release_payload(), status=None)
        self.assertEqual(self.dialog.state, "malformed")
        self.assert_finished(reply)

    def test_overall_deadline_aborts_and_allows_retry(self):
        reply = self.start()
        # Shorten the real Qt deadline, keeping production's ten-second contract.
        self.dialog._deadline.start(1)
        QTest.qWait(20)
        self.assertEqual(self.dialog.state, "timeout")
        self.assertTrue(reply.aborted)
        self.assertEqual(self.transitions, [True, False])
        self.assert_finished(reply)
        self.dialog.retry_button.click()
        self.assertTrue(self.dialog.is_checking)
        self.assertEqual(len(self.manager.requests), 2)

    def test_cap_is_enforced_while_draining_before_completion(self):
        reply = self.start()
        reply.push(b"x" * MAX_RESPONSE_BYTES)
        self.assertTrue(self.dialog.is_checking)
        self.assertEqual(len(self.dialog._response), MAX_RESPONSE_BYTES)
        self.assertEqual(reply.bytesAvailable(), 0)
        reply.push(b"x")
        self.assertEqual(self.dialog.state, "malformed")
        self.assertIn("large response", self.dialog.status_label.text())
        self.assertTrue(reply.aborted)
        self.assertEqual(len(self.dialog._response), 0)
        self.assert_finished(reply)

    def test_single_oversize_chunk_is_aborted_without_unbounded_accumulation(self):
        reply = self.start()
        reply.push(b"x" * (MAX_RESPONSE_BYTES + 100))
        self.assertEqual(self.dialog.state, "malformed")
        self.assertTrue(reply.aborted)
        self.assertEqual(len(self.dialog._response), 0)
        self.assert_finished(reply)

    def test_cancellation_ignores_old_signals_during_next_check(self):
        old = self.start()
        old.push(b'{"tag_name":')
        self.dialog.cancel_check()
        self.dialog.cancel_check()
        self.assertEqual(self.dialog.state, "cancelled")
        self.assertTrue(old.aborted)
        self.assert_finished(old)
        current = self.start()
        old.push(json.dumps(release_payload("9.0.0")).encode())
        old.finish()
        self.assertIs(self.dialog._reply, current)
        self.assertEqual(self.dialog.state, "checking")
        current.push(json.dumps(release_payload()).encode())
        current.finish()
        self.assertEqual(self.dialog.state, "available")
        self.assertEqual(self.dialog.latest_version_label.text(), "Latest version: 1.1.0")
        self.assertEqual(self.transitions, [True, False, True, False])

    def test_close_reject_and_done_cancel_without_destroying_cached_dialog(self):
        for close in (self.dialog.close, self.dialog.reject, lambda: self.dialog.done(0)):
            with self.subTest(action=close):
                self.dialog.show()
                reply = self.start()
                close()
                self.assertTrue(reply.aborted)
                self.assertFalse(self.dialog.isVisible())
                self.assert_finished(reply)
        self.dialog.show()
        self.assertTrue(self.dialog.isVisible())
        self.assertFalse(self.dialog.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))

    def test_explicit_release_link_browser_failure_is_inline_and_retryable(self):
        self.complete(release_payload(with_installer=False))
        expected = f"{RELEASES_URL}/tag/v1.1.0"
        with patch("pixelkit.update_dialog.QDesktopServices.openUrl", return_value=False) as browser:
            self.dialog.action_button.click()
        self.assertEqual(browser.call_args.args[0].toString(), expected)
        self.assertIn("Could not open your browser", self.dialog.feedback.text())
        self.assertFalse(self.dialog.feedback.isHidden())
        self.assertEqual(self.dialog.state, "no_installer")
        with patch("pixelkit.update_dialog.QDesktopServices.openUrl", return_value=True):
            self.dialog.action_button.click()
        self.assertTrue(self.dialog.feedback.isHidden())

    def test_recheck_hides_previous_link_and_does_not_open_it(self):
        self.complete(release_payload())
        with patch("pixelkit.update_dialog.QDesktopServices.openUrl") as browser:
            self.start()
            self.dialog._open_link()
            self.dialog.action_button.click()
        browser.assert_not_called()
        self.assertIsNone(self.dialog._link_url)
        self.assertTrue(self.dialog.action_button.isHidden())
        self.assertEqual(self.dialog.latest_version_label.text(), "Latest version: —")


if __name__ == "__main__":
    unittest.main()
