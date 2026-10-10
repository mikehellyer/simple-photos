import sys

from PySide6.QtWidgets import QApplication

from simple_photos import __version__
from simple_photos.core.diagnostics import enable_app_log, enable_crash_log, log_event
from simple_photos.gui.main_window import MainWindow


def self_test() -> int:
    """`Simple-Photos --self-test`: checks a built app has everything it
    needs bundled (the release workflow runs this on each OS). Exit code
    0 if so."""
    from simple_photos.core import autorotate
    from simple_photos.core.metadata import HEIF_SUPPORTED

    checks = {"heic": HEIF_SUPPORTED, "auto-rotate": autorotate.available()}
    report = ", ".join(f"{name}: {'ok' if ok else 'MISSING'}" for name, ok in checks.items())
    try:
        print(f"Simple-Photos {__version__} self-test — {report}", flush=True)
    except (AttributeError, OSError):  # no console (Windows GUI build)
        pass
    return 0 if all(checks.values()) else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    enable_app_log(__version__)
    enable_crash_log()
    app = QApplication(sys.argv)
    app.setApplicationName("Simple-Photos")
    window = MainWindow()
    window.show()
    code = app.exec()
    log_event(f"event loop finished (code {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
