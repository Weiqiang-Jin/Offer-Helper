"""Opt-in packaged-app smoke test; never runs during normal application use."""
import json
import urllib.request


def schedule(window, report, origin, token, capture=False):
    from PySide6.QtCore import QTimer, QPoint, Qt
    from PySide6.QtTest import QTest
    result = {}

    def api(path, body=None):
        request = urllib.request.Request(origin + '/api/' + path,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
            data=None if body is None else json.dumps(body).encode())
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
            return json.load(response)

    def finish(value):
        try:
            result.update(json.loads(value or '{}'))
            result['overlay'] = api('overlay/status')['running']
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            window.grab().save(str(report.with_suffix('.png')))
        finally:
            window.close()

    def inspect():
        script = '''JSON.stringify({title:document.title,logo:document.querySelector('img.logo')?.naturalWidth,
            connected:document.getElementById('connection')?.textContent,
            capture:typeof navigator.mediaDevices?.getDisplayMedia,
            previewWidth:document.getElementById('sharePreview')?.videoWidth,
            previewActive:!!document.getElementById('sharePreview')?.srcObject?.active,
            notice:document.getElementById('notice')?.textContent})'''
        window.page.runJavaScript(script, finish)

    def select_screen():
        result['screenDialogs'] = len(window.dialogs)
        if window.dialogs:
            from PySide6.QtWidgets import QListView
            dialog = window.dialogs[-1]
            view = dialog.findChild(QListView)
            result['screenCount'] = view.model().rowCount() if view else 0
            if view and view.model().rowCount():
                view.setCurrentIndex(view.model().index(0, 0))
                dialog.accept()
        QTimer.singleShot(2500, inspect)

    def click_preview(value):
        position = json.loads(value)
        def click():
            QTest.mouseClick(window.view.focusProxy(), Qt.MouseButton.LeftButton,
                             pos=QPoint(round(position['x']), round(position['y'])))
            QTimer.singleShot(1500, select_screen)
        QTimer.singleShot(500, click)

    def begin():
        try:
            api('overlay/start', {})
            if capture:
                window.showMaximized()
                QTimer.singleShot(500, lambda: window.page.runJavaScript('''(()=>{const b=document.getElementById('previewStart');
                    b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();
                    return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2});})()''', click_preview))
            else:
                QTimer.singleShot(2500, inspect)
        except Exception as exc:
            result['error'] = str(exc)
            inspect()

    QTimer.singleShot(5000, begin)
    QTimer.singleShot(25000, window.close)
