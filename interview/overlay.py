"""Windows-only local answer window excluded from supported screen capture."""
import ctypes
from ctypes import wintypes
import json
import os
import sys
import tkinter as tk
import urllib.request

WDA_EXCLUDEFROMCAPTURE = 0x11


def work_area(hwnd):
    """Use the window's monitor, excluding the taskbar (including secondary monitors)."""
    class MonitorInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.rcWork


class ContentFit:
    def __init__(self, root, answer, status, hwnd):
        self.root, self.answer, self.status, self.hwnd = root, answer, status, hwnd
        self.pending = None
        self.adjusting = False
        self.last_geometry = None
        root.bind('<Configure>', self.schedule)

    def schedule(self, event=None):
        if self.adjusting or (event is not None and event.widget is not self.root):
            return
        if event is not None and self.geometry() == self.last_geometry:
            return
        if self.pending:
            self.root.after_cancel(self.pending)
        self.pending = self.root.after(80, self.fit)

    def geometry(self):
        return (self.root.winfo_width(), self.root.winfo_height(),
                self.root.winfo_x(), self.root.winfo_y())

    def fit(self):
        if self.pending:
            self.root.after_cancel(self.pending)
        self.pending = None
        self.adjusting = True
        try:
            root = self.root
            area = work_area(self.hwnd)
            root.update_idletasks()
            border = max(0, root.winfo_rootx() - root.winfo_x())
            top = max(0, root.winfo_rooty() - root.winfo_y())
            max_width = max(100, area.right - area.left - 2 * border - 16)
            max_height = max(100, area.bottom - area.top - top - border - 16)
            width = min(max(330, root.winfo_width()), max_width)
            size = 17
            while True:
                self.answer.configure(font=('Microsoft YaHei UI', size), wraplength=max(1, width - 40))
                self.status.configure(wraplength=max(1, width - 28))
                root.update_idletasks()
                height = max(120, self.answer.winfo_reqheight() + self.status.winfo_reqheight())
                if height <= max_height or size == 1:
                    break
                if width < max_width:
                    width = min(max_width, width + 120)
                else:
                    size -= 1
            x = min(max(root.winfo_x(), area.left + 8), area.right - width - 2 * border - 8)
            y = min(max(root.winfo_y(), area.top + 8), area.bottom - height - top - border - 8)
            # Tk interprets negative geometry offsets relative to the right/bottom edge.
            root.geometry(f'{width}x{height}')
            root.update_idletasks()
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
            user32.SetWindowPos(self.hwnd, None, x, y, 0, 0, 0x0015)
            root.update_idletasks()
            self.last_geometry = self.geometry()
        finally:
            self.adjusting = False


def exclude_window(hwnd):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.SetWindowDisplayAffinity.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    user32.SetWindowDisplayAffinity.restype = ctypes.c_bool
    if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        raise ctypes.WinError(ctypes.get_last_error())


def run(port, token):
    root = tk.Tk()
    root.title('offer helper · 本机回答')
    root.geometry('580x340+80+80')
    root.minsize(100, 100)
    root.attributes('-topmost', True)
    root.attributes('-alpha', 0.70)
    root.configure(bg='#f7faf9')
    status = tk.Label(root, text='正在连接本地问答服务…', anchor='w', bg='#dfe9ff', fg='#233455', padx=12, pady=7)
    status.pack(fill='x')
    answer = tk.Label(root, font=('Microsoft YaHei UI', 17), bg='#f7faf9', fg='#203d34',
                      padx=18, pady=14, relief='flat', justify='left', anchor='nw', wraplength=540)
    answer.pack(fill='both', expand=True)
    root.update_idletasks()
    get_parent = ctypes.windll.user32.GetParent
    get_parent.argtypes = (ctypes.c_void_p,)
    get_parent.restype = ctypes.c_void_p
    hwnd = get_parent(root.winfo_id()) or root.winfo_id()
    exclude_window(hwnd)
    layout = ContentFit(root, answer, status, hwnd)
    previous = None

    def refresh():
        nonlocal previous
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{port}/api/state',
                                             headers={'Authorization': 'Bearer ' + token})
            with urllib.request.urlopen(request, timeout=2) as response:
                data = json.load(response)
            current = (data['question'], data['answer'], data['answer_status'])
            if current != previous:
                status.config(text=('生成中 · ' if current[2] == 'generating' else '本机显示 · ') +
                              (current[0][:65] or '等待提问'))
                answer.config(text=current[1] or '等待生成建议回答。')
                layout.schedule()
                previous = current
        except Exception:
            status.config(text='本地问答服务未连接')
        root.after(500, refresh)

    refresh()
    root.mainloop()


if __name__ == '__main__':
    run(int(sys.argv[1]), os.environ['INTERVIEW_OVERLAY_TOKEN'])
