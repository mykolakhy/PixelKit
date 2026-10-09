"""A manual, asynchronous check for published PixelKit releases."""

from __future__ import annotations

import json
import sys

from PyQt6.QtCore import QSysInfo, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .updates import LATEST_RELEASE_API, RELEASES_URL, ReleaseInfo, parse_release


CHECK_TIMEOUT_MS = 10_000
MAX_RESPONSE_BYTES = 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class UpdateDialog(QDialog):
    """Check only on request, and open a release link only on an explicit click."""

    checkingChanged = pyqtSignal(bool)

    def __init__(self, current_version: str, parent=None, *, system=None, machine=None) -> None:
        super().__init__(parent)
        self.current_version = current_version
        self._system = sys.platform if system is None else system
        self._machine = QSysInfo.currentCpuArchitecture() if machine is None else machine
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._response = bytearray()
        self._is_checking = False
        self._link_url: QUrl | None = None
        self.state = "ready"
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(CHECK_TIMEOUT_MS)
        self._deadline.timeout.connect(self._timed_out)

        self.setObjectName("updateDialog")
        self.setWindowTitle("Check for updates")
        self.setModal(False)
        self.setMinimumWidth(420)
        self.resize(520, 280)
        self.setStyleSheet("""
            QDialog#updateDialog { background: #151b24; color: #f4f7fb; }
            QLabel { color: #f4f7fb; }
            QLabel#updateHeading { font-size: 20px; font-weight: 600; }
            QLabel#updateStatus, QLabel#updateVersions { color: #b3c0d2; }
            QLabel#updateFeedback { color: #ffb5b5; }
            QPushButton { background: #202a38; color: #f4f7fb; border: 1px solid #697d97;
                          border-radius: 8px; padding: 8px 12px; font-weight: 600; }
            QPushButton:hover { background: #2a3749; }
            QPushButton:focus { border-color: #6cdecf; }
            QPushButton:disabled { color: #7e8da2; border-color: #39485c; }
            QPushButton#updatePrimary { background: #8255ed; border-color: #8255ed; color: white; }
            QPushButton#updatePrimary:hover { background: #9369f2; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        self.heading = self._label("Check for updates", "updateHeading")
        layout.addWidget(self.heading)
        self.current_version_label = self._label(f"Installed version: {current_version}", "updateVersions")
        self.latest_version_label = self._label("Latest version: —", "updateVersions")
        layout.addWidget(self.current_version_label)
        layout.addWidget(self.latest_version_label)
        self.status_label = self._label("Check GitHub for the latest stable PixelKit release.", "updateStatus")
        self.status_label.setAccessibleName("Update check status")
        layout.addWidget(self.status_label)
        self.feedback = self._label("", "updateFeedback")
        self.feedback.setAccessibleName("Release link status")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        layout.addStretch()
        self.action_button = QPushButton("View &release")
        self.action_button.setObjectName("updatePrimary")
        self.action_button.setAccessibleName("View release")
        self.action_button.setAccessibleDescription("Open the release page in your browser.")
        self.action_button.setAutoDefault(False)
        self.action_button.clicked.connect(self._open_link)
        self.action_button.hide()
        layout.addWidget(self.action_button)
        actions = QHBoxLayout()
        self.retry_button = QPushButton("&Retry")
        self.retry_button.setAccessibleName("Retry update check")
        self.retry_button.setAccessibleDescription("Check GitHub for updates again.")
        self.retry_button.setAutoDefault(False)
        self.retry_button.clicked.connect(self.check_for_updates)
        self.close_button = QPushButton("&Close")
        self.close_button.setAccessibleName("Close update check")
        self.close_button.setAccessibleDescription("Close this window and cancel any active update check.")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.close)
        actions.addWidget(self.retry_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)

    @staticmethod
    def _label(text: str, name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @property
    def is_checking(self) -> bool:
        return self._is_checking

    def _set_checking(self, checking: bool) -> None:
        if checking != self._is_checking:
            self._is_checking = checking
            self.retry_button.setEnabled(not checking)
            self.checkingChanged.emit(checking)

    def _message(self, state: str, heading: str, message: str) -> None:
        self.state = state
        self.heading.setText(heading)
        self.status_label.setText(message)
        self.feedback.clear()
        self.feedback.hide()
        self._link_url = None
        self.action_button.setEnabled(False)
        self.action_button.hide()

    def _set_link(self, url: str, label: str) -> None:
        link = QUrl(url)
        # The parser validates the canonical repository URLs. Keep the button
        # guarded too, so it cannot open an invalid or insecure URL.
        if (not link.isValid() or link.scheme() != "https" or link.host() != "github.com"
                or link.userInfo() or not url.startswith(RELEASES_URL + "/") and url != RELEASES_URL):
            return
        self._link_url = link
        self.action_button.setText(label)
        self.action_button.setAccessibleName(label.replace("&", ""))
        self.action_button.setAccessibleDescription("Open this release link in your browser.")
        self.action_button.setEnabled(True)
        self.action_button.show()

    def check_for_updates(self) -> None:
        """Start one unauthenticated public request without blocking the GUI."""
        if self.is_checking:
            return
        self._response.clear()
        self.latest_version_label.setText("Latest version: —")
        self._message("checking", "Checking for updates…", "Looking for the latest stable release on GitHub…")
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"PixelKit/{self.current_version}".encode("ascii", "replace"))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
        request.setTransferTimeout(CHECK_TIMEOUT_MS)
        try:
            reply = self._manager.get(request)
        except RuntimeError:
            self._message("offline", "Could not check for updates", "Could not connect to GitHub. Check your internet connection and try again.")
            return
        self._reply = reply
        reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
        reply.readyRead.connect(lambda: self._read_reply(reply))
        reply.finished.connect(lambda: self._finished(reply))
        self._deadline.start()
        self._set_checking(True)

    def _read_reply(self, reply: QNetworkReply) -> None:
        if reply is not self._reply or not self.is_checking:
            return
        while reply.bytesAvailable() > 0:
            remaining = MAX_RESPONSE_BYTES - len(self._response)
            chunk = bytes(reply.read(min(_READ_CHUNK_BYTES, remaining + 1)))
            if not chunk:
                break
            if len(chunk) > remaining:
                self._fail("malformed", "Could not read the update response", "GitHub returned an unexpectedly large response. Try again later.", abort=True)
                return
            self._response.extend(chunk)

    def _finished(self, reply: QNetworkReply) -> None:
        if reply is not self._reply or not self.is_checking:
            return
        self._read_reply(reply)
        if reply is not self._reply:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status == 404:
            self._message("no_releases", "No stable releases yet", "GitHub has no published stable PixelKit release yet. You can view the releases page or try again later.")
            self._set_link(RELEASES_URL, "View &releases")
            self._release_reply()
            return
        if status in (403, 429):
            self._fail("rate_limited", "GitHub is limiting update checks", "GitHub declined the update check or its request limit was reached. Please wait and try again later.")
            return
        if status is not None and status != 200:
            self._fail("offline", "Could not check for updates", "GitHub could not complete the update check. Try again later.")
            return
        error = reply.error()
        if error in (QNetworkReply.NetworkError.TimeoutError, QNetworkReply.NetworkError.ProxyTimeoutError):
            self._timed_out()
            return
        if error != QNetworkReply.NetworkError.NoError:
            message = ("Could not make a secure connection to GitHub. Try again later."
                       if error == QNetworkReply.NetworkError.SslHandshakeFailedError
                       else "Could not connect to GitHub. Check your internet connection and try again.")
            self._fail("offline", "Could not check for updates", message)
            return
        if status != 200:
            self._fail("malformed", "Could not read the update response", "GitHub returned an unexpected response. Try again later.")
            return
        try:
            release = parse_release(json.loads(self._response), self.current_version,
                                    system=self._system, machine=self._machine)
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._fail("malformed", "Could not read the update response", "GitHub returned release information that PixelKit could not read. Try again later.")
            return
        self._show_release(release)
        self._release_reply()

    def _show_release(self, release: ReleaseInfo) -> None:
        self.latest_version_label.setText(f"Latest version: {release.version}")
        if not release.is_newer:
            self._message("current", "PixelKit is up to date", "You already have the latest stable release, or a newer version.")
            self._set_link(release.page_url, "View &release")
        elif release.download_url and release.download_label:
            self._message("available", "A new PixelKit version is available", "Open the installer link in your browser, then install the update when you are ready.")
            self._set_link(release.download_url, release.download_label)
        else:
            self._message("no_installer", "A new PixelKit version is available", "There is no matching installer for this computer in the latest release. View the release for available downloads and details.")
            self._set_link(release.page_url, "View &release")

    def _release_reply(self, *, abort: bool = False) -> None:
        reply = self._reply
        # Clear identity before abort: Qt may emit finished synchronously.
        self._reply = None
        self._deadline.stop()
        self._response.clear()
        self._set_checking(False)
        if reply is not None:
            if abort:
                reply.abort()
            reply.deleteLater()

    def _fail(self, state: str, heading: str, message: str, *, abort: bool = False) -> None:
        self._message(state, heading, message)
        self._release_reply(abort=abort)

    def _timed_out(self) -> None:
        if self.is_checking:
            self._fail("timeout", "The update check timed out", "GitHub did not respond within ten seconds. Check your internet connection and try again.", abort=True)

    def cancel_check(self) -> None:
        """Cancel immediately; late signals from this request become harmless."""
        if self.is_checking:
            self._message("cancelled", "Update check cancelled", "Choose Retry to check for updates again.")
            self._release_reply(abort=True)

    def _open_link(self) -> None:
        if self._link_url is None or self.is_checking:
            return
        try:
            opened = QDesktopServices.openUrl(self._link_url)
        except RuntimeError:
            opened = False
        self.feedback.setText("" if opened else "Could not open your browser. Try the release button again.")
        self.feedback.setVisible(not opened)

    def closeEvent(self, event) -> None:
        self.cancel_check()
        super().closeEvent(event)

    def reject(self) -> None:
        self.cancel_check()
        super().reject()

    def done(self, result: int) -> None:
        self.cancel_check()
        super().done(result)
