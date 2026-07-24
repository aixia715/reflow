# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

datas 的目标路径必须剥掉 app/ 前缀，落成 <bundle>/templates 与 <bundle>/static，
与 app.paths.resource_dir() 在 frozen 模式下返回 sys._MEIPASS 的约定对齐。
"""

a = Analysis(
    ['app/desktop.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app/templates', 'templates'),
        ('app/static', 'static'),
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Reflow',
    debug=False,
    strip=False,
    upx=False,
    console=True,          # 保留控制台窗口：既是退出方式，也是出错时唯一线索
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Reflow',
)
