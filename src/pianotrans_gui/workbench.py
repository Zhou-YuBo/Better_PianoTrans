"""Official Qt Quick entry point for the Better PianoTrans workbench."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QML_PATH = Path(__file__).with_name("qml") / "Workbench.qml"


def main() -> int:
    parser = argparse.ArgumentParser(description="Better PianoTrans 工作台")
    parser.add_argument("--screenshot", type=Path, help="启动后保存截图并退出")
    args = parser.parse_args()

    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".codex-cache" / "matplotlib"))
    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc"
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.is_file() else -1
    families = QFontDatabase.applicationFontFamilies(font_id)
    app.setFont(QFont(families[1] if len(families) > 1 else "Segoe UI", 10))

    from .product_facade import WorkbenchFacade

    db_override = os.environ.get("PIANOTRANS_PRODUCT_DB")
    facade = WorkbenchFacade(Path(db_override) if db_override else None)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("facade", facade)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    if not engine.rootObjects():
        facade.close()
        return 1
    window = engine.rootObjects()[0]
    tray_pixmap = QPixmap(32, 32)
    tray_pixmap.fill(QColor("#1f1f1f"))
    painter = QPainter(tray_pixmap)
    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
    painter.drawText(tray_pixmap.rect(), 0x84, "P")
    painter.end()
    tray = QSystemTrayIcon(QIcon(tray_pixmap), app)
    menu = QMenu()
    restore = QAction("显示 Better PianoTrans", app)
    restore.triggered.connect(window.showNormal)
    quit_action = QAction("停止转录并退出", app)
    quit_action.triggered.connect(app.quit)
    menu.addAction(restore)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.setToolTip("Better PianoTrans")
    tray.activated.connect(lambda reason: window.showNormal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    if args.screenshot:
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)

        def capture() -> None:
            try:
                window.grabWindow().save(str(args.screenshot))
            finally:
                app.quit()

        QTimer.singleShot(350, capture)
    exit_code = app.exec()
    tray.hide()
    facade.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
