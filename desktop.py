"""Windows desktop entry point; also dispatches the frozen overlay subprocess."""
import json
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path


def main():
    from interview.paths import DATA_DIR, RESOURCE_DIR
    if len(sys.argv) > 1 and sys.argv[1] == '--overlay':
        from interview.overlay import run
        run(int(sys.argv[2]), os.environ['INTERVIEW_OVERLAY_TOKEN'])
        return 0

    from PySide6.QtCore import QLockFile, QTimer, QUrl, Qt, QObject, Signal
    from PySide6.QtGui import QDesktopServices, QIcon
    from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox, QDialog,
                                   QVBoxLayout, QLabel, QListView, QDialogButtonBox, QFileDialog)
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEnginePermission
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from dotenv import set_key
    import uvicorn
    from run import reserve_port, fresh_url

    log_dir = DATA_DIR / 'logs'
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(filename=log_dir / 'desktop.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    # Windowed PyInstaller has no standard streams. Keep diagnostics local.
    log_stream = (log_dir / 'runtime.log').open('a', encoding='utf-8', buffering=1)
    if sys.stdout is None:
        sys.stdout = log_stream
    if sys.stderr is None:
        sys.stderr = log_stream
    app = QApplication(sys.argv[:1])
    app.setApplicationName('Offer Helper')
    app.setOrganizationName('OfferHelper')
    app.setWindowIcon(QIcon(str(RESOURCE_DIR / 'web/assets/app.ico')))
    lock = QLockFile(str(DATA_DIR / '.desktop.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, 'Offer Helper', '软件已经运行，请切换到已有的 Offer Helper 窗口。')
        return 0

    token = secrets.token_urlsafe(32)
    os.environ['INTERVIEW_TOKEN'] = token
    sock = reserve_port(8765)
    port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config('interview.app:app', host='127.0.0.1', port=port,
                           access_log=False, log_config=None, timeout_graceful_shutdown=3))
    server_thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    server_thread.start()

    class DownloadEvents(QObject):
        finished = Signal(str)

    events = DownloadEvents()

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, kind, is_main):
            if url.scheme() == 'about' or (url.scheme() == 'http' and url.host() == '127.0.0.1' and url.port() == port):
                return True
            if is_main and url.scheme() in ('http', 'https'):
                QDesktopServices.openUrl(url)
            return False

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('Offer Helper')
            self.resize(1280, 900)
            self.setMinimumSize(850, 600)
            self.profile = QWebEngineProfile(self)  # Ephemeral browser credentials/cache.
            self.view = QWebEngineView(self)
            self.page = Page(self.profile, self.view)
            self.view.setPage(self.page)
            self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, True)
            self.page.desktopMediaRequested.connect(self.choose_screen)
            self.capture_approved = False
            self.page.permissionRequested.connect(self.capture_permission)
            self.page.newWindowRequested.connect(lambda request: QDesktopServices.openUrl(request.requestedUrl())
                if request.requestedUrl().scheme() in ('http', 'https') else None)
            self.setCentralWidget(self.view)
            file_menu = self.menuBar().addMenu('软件')
            file_menu.addAction('打开数据目录', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR))))
            file_menu.addAction('刷新页面', self.view.reload)
            file_menu.addSeparator()
            file_menu.addAction('退出', self.close)
            model_menu = self.menuBar().addMenu('语音模型')
            model_menu.addAction('选择已有模型目录…', self.select_model)
            self.download_action = model_menu.addAction('下载本地语音模型', self.download_model)
            help_menu = self.menuBar().addMenu('帮助')
            help_menu.addAction('关于', lambda: QMessageBox.information(self, 'Offer Helper',
                'Offer Helper 0.3.0\nWindows 桌面版\n\n配置和下载模型保存在：\n' + str(DATA_DIR)))
            self.statusBar().showMessage('正在启动本地服务…')
            self.setCentralWidget(self.view)
            self.dialogs = []
            self.closed = False
            events.finished.connect(self.download_finished)

        def choose_screen(self, request):
            # A real chooser: never select or capture a monitor without a user action.
            dialog = QDialog(self)
            dialog.setWindowTitle('选择预览屏幕')
            dialog.resize(450, 270)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel('选择与会议共享相同的屏幕。预览仅在本机显示。'))
            screens = QListView(dialog)
            screens.setModel(request.screensModel())
            layout.addWidget(screens)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText('开始预览')
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            screens.selectionModel().selectionChanged.connect(lambda *_:
                buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(screens.currentIndex().isValid()))
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            def selected():
                self.capture_approved = True
                request.selectScreen(screens.currentIndex())
            dialog.accepted.connect(selected)
            dialog.rejected.connect(request.cancel)
            dialog.finished.connect(lambda _: self.dialogs.remove(dialog))
            self.dialogs.append(dialog)
            dialog.open()

        def capture_permission(self, permission):
            url = permission.origin()
            if (self.capture_approved and url.scheme() == 'http' and url.host() == '127.0.0.1'
                    and url.port() == port
                    and permission.permissionType() == QWebEnginePermission.PermissionType.DesktopVideoCapture):
                permission.grant()
            else:
                permission.deny()
            self.capture_approved = False

        def select_model(self):
            folder = QFileDialog.getExistingDirectory(self, '选择包含 encoder、decoder、joiner 和 tokens 的模型目录')
            if not folder:
                return
            from interview.local_asr import ready
            if not ready({'asr_model_dir': folder}):
                QMessageBox.warning(self, '模型不完整', '该目录缺少本项目所需的 Zipformer 模型文件。')
                return
            from interview.app import CONFIG, state
            if state.audio:
                QMessageBox.information(self, '请先停止采集', '停止音频采集后再切换语音模型。')
                return
            set_key(str(DATA_DIR / '.env'), 'SHERPA_MODEL_DIR', folder)
            CONFIG['asr_model_dir'] = folder
            self.statusBar().showMessage('语音模型已就绪，开始听取提问即可使用。')

        def download_model(self):
            self.download_action.setEnabled(False)
            self.statusBar().showMessage('正在下载并解压语音模型，请保持软件开启。进度记录在数据目录的日志中。')
            def work():
                try:
                    from download_asr import main as download
                    download()
                    events.finished.emit('')
                except Exception as exc:
                    logging.exception('Model download failed')
                    events.finished.emit(str(exc))
            threading.Thread(target=work, daemon=True).start()

        def download_finished(self, error):
            self.download_action.setEnabled(True)
            if error:
                self.statusBar().showMessage('语音模型下载失败，可以重试或选择已有模型目录。')
                QMessageBox.warning(self, '下载失败', error)
            else:
                from interview.app import CONFIG
                from interview.local_asr import MODEL_DIR
                CONFIG['asr_model_dir'] = str(MODEL_DIR)
                set_key(str(DATA_DIR / '.env'), 'SHERPA_MODEL_DIR', str(MODEL_DIR))
                self.statusBar().showMessage('语音模型已就绪。')

        def closeEvent(self, event):
            self.closed = True
            # Stop preview tracks and disconnect the page before stopping Uvicorn.
            for dialog in list(self.dialogs):
                dialog.reject()
            self.view.setUrl(QUrl('about:blank'))
            super().closeEvent(event)

    window = Window()
    window.show()
    deadline = time.monotonic() + 40
    while not server.started and not window.closed and server_thread.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        if not window.closed:
            QMessageBox.critical(window, '启动失败', '本地服务未能启动。请查看数据目录 logs 中的日志。')
        window.close()
        sock.close()
        lock.unlock()
        return 1
    window.view.setUrl(QUrl(fresh_url(origin + '/#token=' + token)))
    window.statusBar().showMessage('本地服务已启动 · 关闭软件会同时停止后台服务')

    # Integration harness for validating the actual bundled browser and assets.
    if '--smoke-test' in sys.argv:
        report = Path(sys.argv[sys.argv.index('--smoke-test') + 1])
        from interview.desktop_check import schedule
        schedule(window, report, origin, token, capture='--capture-test' in sys.argv)
    try:
        return app.exec()
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        sock.close()
        lock.unlock()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        error_file = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'OfferHelper-startup-error.log'
        error_file.write_text(traceback.format_exc(), encoding='utf-8')
        logging.exception('Desktop startup failed')
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, '软件启动失败。诊断日志：\n' + str(error_file), 'Offer Helper', 0x10)
        raise
