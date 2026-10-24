"""Arranque: una sola ventana aunque se abra Konvi varias veces (p. ej. desde Dolphin)."""

import json
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__

SERVER = f"konvi-{os.getuid()}"


def send_to_running(paths):
    """Si ya hay un Konvi abierto, le pasa los ficheros y devuelve True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER)
    if not sock.waitForConnected(300):
        return False
    sock.write(json.dumps(paths).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(2000)
    sock.disconnectFromServer()
    return True


def listen(window):
    QLocalServer.removeServer(SERVER)          # por si quedó de un cierre brusco
    server = QLocalServer(window)
    server.listen(SERVER)

    def accept():
        sock = server.nextPendingConnection()
        buffer = bytearray()

        def read():
            buffer.extend(bytes(sock.readAll()))

        def done():
            read()
            try:
                paths = json.loads(buffer.decode("utf-8") or "[]")
            except ValueError:
                paths = []
            if paths:
                window.add_files(paths)
            window.showNormal()
            window.raise_()
            window.activateWindow()
            sock.deleteLater()

        sock.readyRead.connect(read)
        sock.disconnected.connect(done)

    server.newConnection.connect(accept)
    return server


def install_excepthook(window):
    """PySide6 se traga las excepciones de los slots: que se vean y queden en el log."""
    log = Path(QStandardPaths.writableLocation(QStandardPaths.GenericCacheLocation)) / "konvi" / "konvi.log"
    showing = []

    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        sys.stderr.write(text)
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        if showing:
            return
        showing.append(True)
        box = QMessageBox(QMessageBox.Critical, "Konvi",
                          f"Algo ha fallado dentro de Konvi:\n{value}\n\n"
                          f"El detalle está en {log}.", parent=window)
        box.setDetailedText(text)
        box.exec()
        showing.clear()

    sys.excepthook = hook


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print(f"Konvi {__version__}")
        return 0
    app = QApplication(argv[:1])
    app.setApplicationName("konvi")
    app.setApplicationDisplayName("Konvi")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("konvi")

    paths = [str(Path(a).resolve()) for a in argv[1:] if not a.startswith("-")]
    if send_to_running(paths):
        return 0

    from .ui.main_window import MainWindow
    window = MainWindow(paths)
    install_excepthook(window)
    window._server = listen(window)
    window.show()
    return app.exec()
