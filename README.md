# REM GUI

A graphical user interface for creating Relative Elevation Models (REMs) using the [RiverREM](https://github.com/OpenTopography/RiverREM) package from OpenTopography.

This tool makes REM creation accessible to non-technical users by providing a simple interface for:
- Loading DEMs in various formats and projections
- Automatic river centerline detection from OpenStreetMap
- Custom centerline shapefiles for areas without OSM coverage
- Configurable interpolation and visualization options
- Real-time progress logging

## Screenshot

```
┌─────────────────────────────────────────────────────────────────┐
│  REM Maker - Relative Elevation Model Generator                 │
├─────────────────────────────────────────────────────────────────┤
│  Input Files                                                    │
│  DEM File:      [________________________] [Browse...]          │
│                 1024x1024 pixels, 1.00x1.00 units, UTM          │
│  Output Dir:    [________________________] [Browse...]          │
├─────────────────────────────────────────────────────────────────┤
│  Centerline Options                                             │
│  (•) Use OpenStreetMap (automatic)                              │
│  ( ) Use custom shapefile                                       │
├─────────────────────────────────────────────────────────────────┤
│  [+] Advanced Options                                           │
│  [+] Visualization Options                                      │
├─────────────────────────────────────────────────────────────────┤
│         [Generate REM]  [Cancel]  [Clear Log]                   │
├─────────────────────────────────────────────────────────────────┤
│  Output Log                                                     │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ [10:30:15] REM Maker GUI initialized.                       ││
│  │ [10:30:15] DEM loaded: example.tif                          ││
│  │ [10:30:20] Starting REM generation...                       ││
│  └─────────────────────────────────────────────────────────────┘│
│  Ready                                                          │
└─────────────────────────────────────────────────────────────────┘
```

## Features

- **Easy DEM Loading**: Browse and select GeoTIFF files with automatic projection detection
- **Dual Centerline Sources**:
  - Automatic river detection via OpenStreetMap
  - Custom shapefile support for areas without OSM data
- **Advanced Options**: Fine-tune interpolation points, workers, and processing parameters
- **Visualization Controls**: Choose colormaps, hillshade blending, and output formats (PNG, KMZ)
- **Progress Logging**: Real-time feedback on processing status
- **Threaded Processing**: UI remains responsive during long operations

## Installation

### Option 1: Run from Source

1. Create a conda environment (recommended for geospatial packages):
```bash
conda create -n rem_gui python=3.10
conda activate rem_gui
conda install -c conda-forge gdal geopandas osmnx scipy matplotlib seaborn
pip install riverrem
```

2. Clone and run:
```bash
git clone https://github.com/yourusername/REM_gui.git
cd REM_gui
python rem_gui.py
```

### Option 2: Pre-built Executable

Download the latest release from the [Releases](releases) page.

## Building an Executable

### Why are geospatial executables large?

Python geospatial executables are inherently large due to bundled libraries:
- **GDAL**: 50-100 MB (required for reading/writing rasters)
- **NumPy/SciPy**: ~100 MB (numerical computation)
- **Pandas/GeoPandas**: ~50 MB (data handling)
- **Matplotlib**: ~40 MB (visualization)

A 300-500 MB distribution is **normal** for this type of application.

### Building Steps

1. **Set up a clean build environment** (crucial for smaller size):
```bash
# Create minimal conda environment
conda create -n rem_build python=3.10
conda activate rem_build

# Install only runtime dependencies via conda
conda install -c conda-forge gdal geopandas osmnx scipy matplotlib seaborn riverrem

# Install PyInstaller
pip install pyinstaller
```

2. **Build the executable**:
```bash
# Using the build script (recommended)
python build.py

# Or directly with PyInstaller
pyinstaller rem_gui.spec
```

3. **Find your executable**:
   - Folder distribution: `dist/REM_GUI/REM_GUI.exe`
   - Single file (if built with `--onefile`): `dist/REM_GUI.exe`

### Size Optimization Tips

1. **Use a clean virtual environment** - Don't install development tools
2. **Use conda for geospatial packages** - Better dependency resolution
3. **Prefer folder distribution** - Smaller and faster than single-file
4. **Compress for distribution**: `7z a -mx=9 REM_GUI.7z dist/REM_GUI/` (reduces to ~100-150 MB)

## Usage Guide

### For Best Results

1. **Use UTM-projected DEMs** - Geographic (lat/lon) projections may produce artifacts
2. **Ensure proper georeferencing** - The DEM must have valid coordinate system information
3. **Check OSM coverage** - For remote areas, you may need a custom centerline

### Using Custom Centerlines

If OpenStreetMap doesn't have your river or the centerline is inaccurate:

1. Create a centerline shapefile in GIS software (ArcGIS, QGIS)
2. Ensure the shapefile:
   - Uses the same projection as your DEM (or can be reprojected)
   - Contains line geometry (not points or polygons)
   - Follows the river thalweg
3. Select "Use custom shapefile" and browse to your `.shp` file

### Advanced Options

| Option | Default | Description |
|--------|---------|-------------|
| Interpolation Points | 1000 | Maximum points for elevation interpolation (higher = more detail) |
| K Neighbors | auto | Nearest neighbors for interpolation (auto-estimated based on sinuosity) |
| Error Tolerance | 0.1 | KD-tree query tolerance (0 = exact, higher = faster) |
| CPU Workers | half | Number of threads for parallel processing |
| Chunk Size | 1,000,000 | Raster cells per chunk (higher = faster but more RAM) |

### Visualization Options

| Option | Default | Description |
|--------|---------|-------------|
| Colormap | mako_r | Color scheme for the REM visualization |
| Vertical Exaggeration | 4x | Hillshade relief enhancement |
| Hillshade Blend | 25% | Amount of hillshade mixed with colormap |
| Create PNG | Yes | Output georeferenced PNG |
| Create KMZ | No | Output KMZ for Google Earth |

## File Structure

```
REM_gui/
├── rem_gui.py              # Main GUI application
├── rem_gui.spec            # PyInstaller configuration
├── build.py                # Build automation script
├── requirements.txt        # Runtime dependencies
├── requirements-build.txt  # Build dependencies
├── README.md               # This file
└── LICENSE                 # GPL-3.0 license
```

## Troubleshooting

### "Could not import RiverREM"
Install RiverREM: `pip install riverrem` or `conda install -c conda-forge riverrem`

### "GDAL not found" or "osgeo import error"
GDAL requires system libraries. Use conda: `conda install -c conda-forge gdal`

### "No rivers found in extent"
- Your DEM extent may not contain OpenStreetMap river data
- Use a custom centerline shapefile instead

### Executable is too large
- This is normal for geospatial applications
- See the "Size Optimization Tips" section above
- Use 7zip compression for distribution

## License

GPL-3.0 - Same as [RiverREM](https://github.com/OpenTopography/RiverREM)

## Acknowledgments

- [RiverREM](https://github.com/OpenTopography/RiverREM) by OpenTopography
- [OpenStreetMap](https://www.openstreetmap.org/) for river centerline data
