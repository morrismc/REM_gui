# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for REM GUI

This spec is optimized to create a smaller executable by:
1. Only including necessary hidden imports
2. Excluding unused packages and submodules
3. Using UPX compression
4. Stripping debug symbols

Build command:
    pyinstaller rem_gui.spec

For a one-file executable (larger, slower startup):
    pyinstaller rem_gui.spec --onefile

For a one-folder distribution (smaller total, faster startup):
    pyinstaller rem_gui.spec
"""

import sys
import os

# Increase recursion limit for complex dependency trees
sys.setrecursionlimit(5000)

# Base path for finding files
block_cipher = None

# Define which packages are truly needed
# These are the core dependencies for RiverREM
HIDDEN_IMPORTS = [
    # Core numerical/scientific
    'numpy',
    'numpy.core._methods',
    'numpy.lib.format',

    # Geospatial core
    'osgeo',
    'osgeo.gdal',
    'osgeo.ogr',
    'osgeo.osr',
    'osgeo._gdal',
    'osgeo._ogr',
    'osgeo._osr',

    # Shapely for geometry
    'shapely',
    'shapely.geometry',
    'shapely.ops',
    'shapely.prepared',

    # GeoPandas for vector data
    'geopandas',
    'geopandas.datasets',
    'pyproj',
    'pyproj.crs',
    'fiona',
    'fiona.crs',
    'fiona._shim',
    'fiona.schema',

    # OSMnx for OpenStreetMap data
    'osmnx',

    # NetworkX (dependency of osmnx)
    'networkx',

    # Scipy for interpolation
    'scipy',
    'scipy.spatial',
    'scipy.interpolate',
    'scipy.ndimage',

    # Visualization
    'matplotlib',
    'matplotlib.pyplot',
    'matplotlib.colors',
    'matplotlib.cm',
    'seaborn',

    # Image handling
    'PIL',
    'PIL.Image',

    # Data handling
    'pandas',
    'pandas.core',

    # HTTP/networking (for OSM requests)
    'requests',
    'urllib3',

    # RiverREM itself
    'riverrem',
    'riverrem.REMMaker',
]

# Packages to completely exclude (not needed, saves space)
EXCLUDES = [
    # Testing frameworks
    'pytest',
    'nose',
    'unittest',
    'test',
    'tests',

    # Documentation tools
    'sphinx',
    'docutils',

    # Development tools
    'IPython',
    'ipykernel',
    'ipywidgets',
    'jupyter',
    'jupyter_client',
    'jupyter_core',
    'notebook',
    'nbformat',
    'nbconvert',

    # Unused scientific packages
    'sympy',
    'numba',
    'dask',
    'distributed',

    # Unused plotting backends
    'PyQt5',
    'PyQt6',
    'PySide2',
    'PySide6',
    'wx',

    # Unused database
    'sqlalchemy',
    'sqlite3',
    'psycopg2',

    # Other large unused packages
    'tornado',
    'zmq',
    'jedi',
    'parso',
    'pydoc',
    'lib2to3',
    'xml.etree.ElementTree',  # If not needed

    # Unused encodings
    'encodings.cp1250',
    'encodings.cp1251',
    'encodings.cp1252',
    'encodings.cp1253',
    'encodings.cp1254',
    'encodings.cp1255',
    'encodings.cp1256',
    'encodings.cp1257',
    'encodings.cp1258',
]

a = Analysis(
    ['rem_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Remove unnecessary binaries to reduce size
# Filter out test files and documentation
a.datas = [x for x in a.datas if not any(
    pattern in x[0].lower() for pattern in [
        'test', 'tests', 'testing',
        'doc', 'docs', 'documentation',
        'example', 'examples',
        'sample', 'samples',
        'license', 'readme',
        '.md', '.rst', '.txt',
    ]
)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# For one-folder distribution (recommended - smaller, faster)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # Keep as folder distribution
    name='REM_GUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,  # Strip debug symbols (Linux/Mac)
    upx=True,  # Use UPX compression
    upx_exclude=[
        # Don't compress these (they don't compress well or break)
        'vcruntime140.dll',
        'python*.dll',
        'libpython*.so*',
    ],
    console=False,  # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add path to .ico file if you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=True,
    upx=True,
    upx_exclude=[
        'vcruntime140.dll',
        'python*.dll',
        'libpython*.so*',
    ],
    name='REM_GUI',
)

# =============================================================================
# ALTERNATIVE: One-file executable (comment out above COLLECT, uncomment below)
# Note: One-file is larger and slower to start, but easier to distribute
# =============================================================================
# exe_onefile = EXE(
#     pyz,
#     a.scripts,
#     a.binaries,
#     a.zipfiles,
#     a.datas,
#     [],
#     name='REM_GUI',
#     debug=False,
#     bootloader_ignore_signals=False,
#     strip=True,
#     upx=True,
#     upx_exclude=[
#         'vcruntime140.dll',
#         'python*.dll',
#         'libpython*.so*',
#     ],
#     runtime_tmpdir=None,
#     console=False,
#     disable_windowed_traceback=False,
#     argv_emulation=False,
#     target_arch=None,
#     codesign_identity=None,
#     entitlements_file=None,
#     icon=None,
# )
