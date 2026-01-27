#!/usr/bin/env python3
"""
Build script for REM GUI executable

This script helps create an optimized executable with minimal size.
Run with: python build.py [--onefile] [--clean]

The executable size issue with geospatial Python packages is primarily due to:
1. GDAL libraries (~50-100MB) - Required, cannot be reduced
2. NumPy/SciPy (~100MB) - Required for computation
3. Matplotlib (~40MB) - Required for visualization
4. Pandas/GeoPandas (~50MB) - Required for data handling
5. Duplicate/unused submodules

This script attempts to minimize size by creating a clean build environment.
"""

import subprocess
import sys
import os
import shutil
import argparse


def run_command(cmd, description):
    """Run a command and handle errors."""
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"{'='*60}")
    print(f"Running: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"ERROR: {description} failed!")
        return False
    return True


def get_size(path):
    """Get total size of a directory or file in MB."""
    total = 0
    if os.path.isfile(path):
        return os.path.getsize(path) / (1024 * 1024)
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total += os.path.getsize(fp)
    return total / (1024 * 1024)


def clean_build():
    """Remove previous build artifacts."""
    print("\nCleaning previous build artifacts...")
    dirs_to_clean = ['build', 'dist', '__pycache__']
    files_to_clean = ['*.pyc', '*.pyo']

    for d in dirs_to_clean:
        if os.path.exists(d):
            print(f"  Removing {d}/")
            shutil.rmtree(d)

    print("Clean complete.")


def check_dependencies():
    """Check if required dependencies are installed."""
    print("\nChecking dependencies...")

    required = [
        ('pyinstaller', 'PyInstaller'),
        ('riverrem', 'riverrem'),
        ('gdal', 'osgeo.gdal'),
        ('numpy', 'numpy'),
        ('scipy', 'scipy'),
        ('geopandas', 'geopandas'),
        ('osmnx', 'osmnx'),
        ('matplotlib', 'matplotlib'),
    ]

    missing = []
    for name, import_name in required:
        try:
            __import__(import_name.split('.')[0])
            print(f"  [OK] {name}")
        except ImportError:
            print(f"  [MISSING] {name}")
            missing.append(name)

    if missing:
        print(f"\nMissing packages: {', '.join(missing)}")
        print("Please install them before building.")
        return False

    return True


def build_executable(onefile=False):
    """Build the executable using PyInstaller."""
    spec_file = 'rem_gui.spec'

    if not os.path.exists(spec_file):
        print(f"ERROR: {spec_file} not found!")
        return False

    cmd = ['pyinstaller', spec_file, '--clean', '--noconfirm']

    if onefile:
        # For onefile, we need to modify the approach
        print("\nBuilding as single file executable...")
        print("Note: This will be larger and slower to start than folder distribution.")
        cmd = [
            'pyinstaller',
            '--onefile',
            '--noconsole',
            '--clean',
            '--noconfirm',
            '--name', 'REM_GUI',
            'rem_gui.py',
        ]

    return run_command(cmd, "Building executable")


def post_build_report():
    """Report on the built executable."""
    print("\n" + "="*60)
    print("  BUILD COMPLETE")
    print("="*60)

    dist_path = 'dist'
    if not os.path.exists(dist_path):
        print("ERROR: dist directory not found!")
        return

    # Check what was built
    for item in os.listdir(dist_path):
        item_path = os.path.join(dist_path, item)
        size = get_size(item_path)

        if os.path.isdir(item_path):
            print(f"\n  Folder distribution: {item}/")
            print(f"  Total size: {size:.1f} MB")
            print(f"  Location: {os.path.abspath(item_path)}")

            # List largest files
            print("\n  Largest files:")
            files = []
            for dirpath, dirnames, filenames in os.walk(item_path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    files.append((fp, os.path.getsize(fp)))
            files.sort(key=lambda x: x[1], reverse=True)
            for fp, size in files[:10]:
                rel_path = os.path.relpath(fp, item_path)
                print(f"    {size/1024/1024:6.1f} MB  {rel_path}")

        else:
            print(f"\n  Single file: {item}")
            print(f"  Size: {size:.1f} MB")
            print(f"  Location: {os.path.abspath(item_path)}")

    print("\n" + "="*60)
    print("  TIPS FOR SMALLER EXECUTABLES")
    print("="*60)
    print("""
  1. Use a CLEAN virtual environment with minimal packages
     - Don't install dev tools (pytest, ipython, etc.)
     - Only install what's needed for runtime

  2. Use mamba for geospatial packages (faster dependency resolution)
     mamba create -n rem_build python=3.10
     mamba activate rem_build
     mamba install -c conda-forge gdal geopandas osmnx riverrem
     pip install pyinstaller

  3. The folder distribution is usually better than onefile:
     - Smaller total size
     - Faster startup time
     - Easier to update individual components
     - Can be compressed with 7zip for distribution

  4. Expected sizes (approximate):
     - Folder distribution: 300-500 MB
     - Single file: 400-700 MB
     - These are normal for geospatial Python apps!

  5. For distribution, compress with 7zip:
     7z a -mx=9 REM_GUI.7z dist/REM_GUI/
     (Can reduce to ~100-150 MB compressed)
""")


def main():
    parser = argparse.ArgumentParser(description='Build REM GUI executable')
    parser.add_argument('--onefile', action='store_true',
                        help='Build as single file (larger, slower startup)')
    parser.add_argument('--clean', action='store_true',
                        help='Only clean build artifacts, don\'t build')
    parser.add_argument('--skip-check', action='store_true',
                        help='Skip dependency check')
    args = parser.parse_args()

    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║             REM GUI Build Script                          ║
    ║                                                           ║
    ║  Creates a standalone executable for the REM GUI          ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    # Clean if requested
    if args.clean:
        clean_build()
        return

    # Check dependencies
    if not args.skip_check:
        if not check_dependencies():
            print("\nFix missing dependencies and try again.")
            sys.exit(1)

    # Clean previous build
    clean_build()

    # Build
    if not build_executable(onefile=args.onefile):
        print("\nBuild failed!")
        sys.exit(1)

    # Report
    post_build_report()


if __name__ == '__main__':
    main()
