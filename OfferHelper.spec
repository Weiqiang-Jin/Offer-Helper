# Build with: python -m PyInstaller --noconfirm OfferHelper.spec
from PyInstaller.utils.hooks import collect_all, collect_submodules
import os
import sys
from pathlib import Path

# Build against Python/Windows DLLs, not unrelated programs on the developer PATH.
# In particular, a third-party icuuc.dll cannot replace Windows' ICU used by Qt.
os.environ['PATH'] = os.pathsep.join(str(p) for p in (
    Path(sys.base_prefix), Path(sys.base_prefix) / 'DLLs', Path(sys.prefix) / 'Scripts',
    Path(os.environ['WINDIR']) / 'System32', Path(os.environ['WINDIR'])))

datas = [('web', 'web'), ('.env.example', '.')]
binaries = []
hiddenimports = ['interview.app', 'interview.overlay', 'uvicorn.logging',
                 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
                 'uvicorn.protocols.websockets.websockets_impl', 'uvicorn.lifespan.on']
for package in ('sherpa_onnx',):
    data, binary, hidden = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += hidden
hiddenimports += collect_submodules('websockets')
a = Analysis(['desktop.py'], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['pytest', 'matplotlib', 'IPython', 'PyQt5', 'PyQt6'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='OfferHelper',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, icon='web/assets/app.ico')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='OfferHelper')
