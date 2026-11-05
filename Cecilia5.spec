# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller recipe for the macOS Cecilia5.app bundle.

Build with:   ./scripts/build_macos_app.sh
(or: .venv/bin/pyinstaller --noconfirm --clean Cecilia5.spec)

The app ends up in dist/Cecilia5.app.
"""
import os
import re

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

with open(os.path.join(SPECPATH, 'Resources', 'constants.py')) as f:
    APP_VERSION = re.search(r"^APP_VERSION = '([^']+)'", f.read(), re.M).group(1)

# Everything in Resources/ that isn't Python source is runtime data (modules,
# images, icons...). constants.py looks for it in sys._MEIPASS/Resources.
datas = []
for root, dirs, files in os.walk(os.path.join(SPECPATH, 'Resources')):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    for f in files:
        if f.endswith(('.py', '.pyc')) or f == '.DS_Store':
            continue
        src = os.path.join(root, f)
        datas.append((src, os.path.relpath(root, SPECPATH)))

a = Analysis(
    ['Cecilia5.py'],
    pathex=[SPECPATH],
    binaries=collect_dynamic_libs('pyo'),
    datas=datas,
    # Cecilia modules (.c5) are exec'd at runtime with `from pyo import *`,
    # so every pyo submodule must be in the bundle.
    hiddenimports=collect_submodules('pyo.lib') + ['wx.adv', 'wx.html', 'wx.xml'],
    excludes=['tkinter', 'pyo.editor', 'pyo.examples', 'matplotlib', 'IPython'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Cecilia5',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch=None,
    icon='Resources/Cecilia5.icns',
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Cecilia5')

app = BUNDLE(
    coll,
    name='Cecilia5.app',
    icon='Resources/Cecilia5.icns',
    bundle_identifier='com.davidpiazza.cecilia5',
    version=APP_VERSION,
    info_plist={
        'CFBundleDisplayName': 'Cecilia5',
        'CFBundleName': 'Cecilia5',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'NSHighResolutionCapable': True,
        'NSMicrophoneUsageDescription': 'Cecilia5 uses audio input when live input is enabled in the preferences.',
        'LSMinimumSystemVersion': '13.0',
        'CFBundleDocumentTypes': [{
            'CFBundleTypeName': 'Cecilia5 module',
            'CFBundleTypeRole': 'Editor',
            'CFBundleTypeExtensions': ['c5'],
            'LSHandlerRank': 'Owner',
        }],
    },
)
