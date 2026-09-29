"""
main.py

Application entry point.
"""

import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from OBSOLETE.CAM_GUI import CameraWindow


def exception_hook(exc_type, exc_value, exc_traceback):
    """
    Display uncaught exceptions in a dialog instead of silently crashing.
    """

    traceback.print_exception(exc_type, exc_value, exc_traceback)

    message = "".join(
        traceback.format_exception(
            exc_type,
            exc_value,
            exc_traceback
        )
    )

    QMessageBox.critical(
        None,
        "Unhandled Exception",
        message
    )

    sys.exit(1)


def main():

    sys.excepthook = exception_hook

    app = QApplication(sys.argv)

    app.setApplicationName("RAYOPS Camera Viewer")
    app.setOrganizationName("RAYOPS")

    window = CameraWindow()

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()