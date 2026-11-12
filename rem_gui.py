# -*- coding: utf-8 -*-
"""
REM GUI - A graphical interface for creating Relative Elevation Models

This GUI wraps the RiverREM package from OpenTopography to make it accessible
for non-technical users. It supports:
- Loading DEMs in various formats and projections
- Automatic river centerline detection from OpenStreetMap
- Custom centerline shapefiles
- DEM-derived drainage networks (pytopotoolbox): pick a watershed outlet or a
  trunk stream on a map, and generate a REM clipped to that watershed
- Configurable interpolation and visualization options
- Progress logging and status updates

Author: Based on original work by mmorriss, enhanced for production use
License: GPL-3.0 (same as RiverREM)
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import sys
import os
import queue
import logging
from datetime import datetime

# Configure logging before other imports
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TextHandler(logging.Handler):
    """Custom logging handler that writes to a tkinter Text widget."""

    def __init__(self, text_widget, message_queue):
        super().__init__()
        self.text_widget = text_widget
        self.message_queue = message_queue

    def emit(self, record):
        msg = self.format(record)
        self.message_queue.put(msg + '\n')


class StdoutRedirector:
    """Redirects stdout to a queue for thread-safe GUI updates."""

    def __init__(self, message_queue):
        self.message_queue = message_queue
        self.original_stdout = sys.stdout

    def write(self, text):
        if text.strip():  # Only queue non-empty messages
            self.message_queue.put(text if text.endswith('\n') else text + '\n')
        # Also write to original stdout for debugging (may be None in frozen builds)
        if self.original_stdout is not None:
            self.original_stdout.write(text)

    def flush(self):
        if self.original_stdout is not None:
            self.original_stdout.flush()


class CollapsibleFrame(ttk.Frame):
    """A frame that can be collapsed/expanded with a toggle button."""

    def __init__(self, parent, title="", **kwargs):
        super().__init__(parent, **kwargs)

        self.is_expanded = tk.BooleanVar(value=False)

        # Header frame with toggle button
        self.header = ttk.Frame(self)
        self.header.pack(fill='x', expand=False)

        self.toggle_btn = ttk.Checkbutton(
            self.header,
            text=f"+ {title}",
            variable=self.is_expanded,
            command=self._toggle,
            style='Toolbutton'
        )
        self.toggle_btn.pack(side='left', fill='x', expand=True)

        # Content frame (initially hidden)
        self.content = ttk.Frame(self)

    def _toggle(self):
        if self.is_expanded.get():
            self.content.pack(fill='x', expand=True, padx=20, pady=5)
            self.toggle_btn.config(text=self.toggle_btn.cget('text').replace('+', '-'))
        else:
            self.content.pack_forget()
            self.toggle_btn.config(text=self.toggle_btn.cget('text').replace('-', '+'))


class REMApp:
    """Main application class for the REM GUI."""

    # Available colormaps for visualization
    COLORMAPS = [
        'mako_r', 'viridis', 'plasma', 'inferno', 'magma', 'cividis',
        'Blues', 'Greens', 'Oranges', 'Reds', 'YlOrBr', 'YlOrRd',
        'OrRd', 'PuRd', 'BuPu', 'GnBu', 'PuBu', 'YlGnBu', 'PuBuGn',
        'BuGn', 'YlGn', 'terrain', 'gist_earth', 'ocean', 'cubehelix'
    ]

    def __init__(self, root):
        self.root = root
        self.root.title("REM Maker - Relative Elevation Model Generator")
        self.root.geometry("800x860")
        self.root.minsize(700, 700)

        # Message queue for thread-safe GUI updates
        self.message_queue = queue.Queue()

        # Processing state
        self.is_processing = False
        self.processing_thread = None

        # DEM-drainage (pytopotoolbox) state
        self.drainage_network = None      # drainage_selector.DrainageNetwork
        self.drainage_selection = None    # drainage_selector.DrainageSelection
        self.network_busy = False

        # Setup styles
        self._setup_styles()

        # Create main container with scrollbar
        self._create_main_layout()

        # Create all UI sections
        self._create_input_section()
        self._create_centerline_section()
        self._create_advanced_section()
        self._create_visualization_section()
        self._create_action_buttons()
        self._create_console()
        self._create_status_bar()

        # Setup stdout redirection
        self._setup_output_redirect()

        # Start queue processing
        self._process_queue()

        # Log startup
        self._log("REM Maker GUI initialized. Ready to process DEMs.")
        self._log("For best results, use UTM-projected DEMs.")

    def _setup_styles(self):
        """Configure ttk styles for the application."""
        style = ttk.Style()
        style.configure('Header.TLabel', font=('Helvetica', 10, 'bold'))
        style.configure('Status.TLabel', font=('Helvetica', 9))
        style.configure('Run.TButton', font=('Helvetica', 10, 'bold'))

    def _create_main_layout(self):
        """Create the main layout container."""
        # Main frame with padding
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill='both', expand=True)

        # Configure grid weights for resizing
        self.main_frame.columnconfigure(1, weight=1)

    def _create_input_section(self):
        """Create the input file selection section."""
        # Section header
        ttk.Label(self.main_frame, text="Input Files", style='Header.TLabel').grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 5)
        )

        # DEM file selection
        ttk.Label(self.main_frame, text="DEM File:").grid(
            row=1, column=0, sticky='w', padx=(0, 10), pady=5
        )

        self.dem_var = tk.StringVar()
        self.dem_var.trace_add('write', self._on_dem_path_changed)
        self.dem_entry = ttk.Entry(self.main_frame, textvariable=self.dem_var, width=50)
        self.dem_entry.grid(row=1, column=1, sticky='ew', pady=5)

        ttk.Button(self.main_frame, text="Browse...", command=self._browse_dem).grid(
            row=1, column=2, padx=(10, 0), pady=5
        )

        # DEM info display
        self.dem_info_var = tk.StringVar(value="No DEM loaded")
        ttk.Label(self.main_frame, textvariable=self.dem_info_var, foreground='gray').grid(
            row=2, column=1, sticky='w', pady=(0, 5)
        )

        # Output directory
        ttk.Label(self.main_frame, text="Output Directory:").grid(
            row=3, column=0, sticky='w', padx=(0, 10), pady=5
        )

        self.outdir_var = tk.StringVar()
        self.outdir_entry = ttk.Entry(self.main_frame, textvariable=self.outdir_var, width=50)
        self.outdir_entry.grid(row=3, column=1, sticky='ew', pady=5)

        ttk.Button(self.main_frame, text="Browse...", command=self._browse_outdir).grid(
            row=3, column=2, padx=(10, 0), pady=5
        )

        # Separator
        ttk.Separator(self.main_frame, orient='horizontal').grid(
            row=4, column=0, columnspan=3, sticky='ew', pady=10
        )

    def _create_centerline_section(self):
        """Create the centerline source section as a notebook with two tabs:
        OSM/custom file, and DEM-derived drainage network (pytopotoolbox)."""
        ttk.Label(self.main_frame, text="Centerline Source", style='Header.TLabel').grid(
            row=5, column=0, columnspan=3, sticky='w', pady=(0, 5)
        )

        self.centerline_notebook = ttk.Notebook(self.main_frame)
        self.centerline_notebook.grid(row=6, column=0, columnspan=3, sticky='ew', pady=5)

        osm_tab = ttk.Frame(self.centerline_notebook, padding=8)
        drainage_tab = ttk.Frame(self.centerline_notebook, padding=8)
        self.centerline_notebook.add(osm_tab, text="OSM / Custom Centerline")
        self.centerline_notebook.add(drainage_tab, text="Select from DEM Drainage")

        self._create_osm_tab(osm_tab)
        self._create_drainage_tab(drainage_tab)

        # Separator
        ttk.Separator(self.main_frame, orient='horizontal').grid(
            row=8, column=0, columnspan=3, sticky='ew', pady=10
        )

    def _using_drainage_mode(self):
        """True when the 'Select from DEM Drainage' tab is active."""
        return self.centerline_notebook.index('current') == 1

    def _create_osm_tab(self, parent):
        """Original OSM / custom-centerline controls."""
        # Centerline source selection
        self.centerline_source = tk.StringVar(value="osm")

        centerline_frame = ttk.Frame(parent)
        centerline_frame.pack(fill='x', pady=5)

        ttk.Radiobutton(
            centerline_frame, text="Use OpenStreetMap (automatic)",
            variable=self.centerline_source, value="osm",
            command=self._toggle_centerline_source
        ).pack(side='left', padx=(0, 20))

        ttk.Radiobutton(
            centerline_frame, text="Use custom centerline file",
            variable=self.centerline_source, value="custom",
            command=self._toggle_centerline_source
        ).pack(side='left')

        # Custom shapefile selection
        self.shapefile_frame = ttk.Frame(parent)
        self.shapefile_frame.pack(fill='x', pady=5)

        ttk.Label(self.shapefile_frame, text="Centerline:").pack(side='left', padx=(20, 10))

        self.shapefile_var = tk.StringVar()
        self.shapefile_entry = ttk.Entry(self.shapefile_frame, textvariable=self.shapefile_var, width=40)
        self.shapefile_entry.pack(side='left', fill='x', expand=True)

        self.shapefile_btn = ttk.Button(self.shapefile_frame, text="Browse...", command=self._browse_shapefile)
        self.shapefile_btn.pack(side='left', padx=(10, 0))

        # Initially disable custom shapefile widgets
        self._toggle_centerline_source()

    def _create_drainage_tab(self, parent):
        """Controls for deriving a stream network from the DEM (pytopotoolbox)
        and selecting a drainage / trunk stream to build the REM from."""
        parent.columnconfigure(1, weight=1)
        hint = {'foreground': 'gray'}

        # Critical drainage area (channel-initiation threshold)
        ttk.Label(parent, text="Critical drainage area:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        f = ttk.Frame(parent)
        f.grid(row=0, column=1, sticky='w', pady=3)
        self.threshold_var = tk.StringVar(value="1.0")
        ttk.Entry(f, textvariable=self.threshold_var, width=10).pack(side='left')
        self.threshold_units_var = tk.StringVar(value="km2")
        ttk.Combobox(f, textvariable=self.threshold_units_var, width=7, state='readonly',
                     values=['km2', 'm2', 'pixels']).pack(side='left', padx=(5, 0))
        ttk.Label(parent, text="(smaller = denser network)", **hint).grid(
            row=0, column=2, sticky='w', pady=3)

        # Analysis resolution
        ttk.Label(parent, text="Analysis cell size:").grid(row=1, column=0, sticky='w', pady=3)
        self.analysis_res_var = tk.StringVar(value="native")
        ttk.Entry(parent, textvariable=self.analysis_res_var, width=10).grid(
            row=1, column=1, sticky='w', pady=3)
        ttk.Label(parent, text="('native' or map units; coarser = faster on big lidar)",
                  **hint).grid(row=1, column=2, sticky='w', pady=3)

        # Watershed clipping and buffer
        ttk.Label(parent, text="Watershed buffer:").grid(row=2, column=0, sticky='w', pady=3)
        f = ttk.Frame(parent)
        f.grid(row=2, column=1, sticky='w', pady=3)
        self.ws_buffer_var = tk.StringVar(value="0")
        ttk.Entry(f, textvariable=self.ws_buffer_var, width=10).pack(side='left')
        ttk.Label(f, text="map units").pack(side='left', padx=(5, 0))
        self.clip_ws_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(parent, text="Clip DEM/REM to selected watershed",
                        variable=self.clip_ws_var).grid(row=2, column=2, sticky='w', pady=3)

        # Trunk smoothing
        ttk.Label(parent, text="Trunk smoothing:").grid(row=3, column=0, sticky='w', pady=3)
        f = ttk.Frame(parent)
        f.grid(row=3, column=1, sticky='w', pady=3)
        self.smooth_var = tk.StringVar(value="0")
        ttk.Entry(f, textvariable=self.smooth_var, width=10).pack(side='left')
        ttk.Label(f, text="cells").pack(side='left', padx=(5, 0))
        ttk.Label(parent, text="(0 = exact D8 path; >0 removes stair-steps)", **hint).grid(
            row=3, column=2, sticky='w', pady=3)

        # Action buttons
        bf = ttk.Frame(parent)
        bf.grid(row=4, column=0, columnspan=3, sticky='w', pady=(8, 3))
        self.compute_net_btn = ttk.Button(bf, text="1. Compute Stream Network",
                                          command=self._start_compute_network, width=26)
        self.compute_net_btn.pack(side='left', padx=(0, 8))
        self.open_map_btn = ttk.Button(bf, text="2. Select on Map...",
                                       command=self._open_network_map, width=20,
                                       state='disabled')
        self.open_map_btn.pack(side='left')

        self.drainage_status_var = tk.StringVar(
            value="No network computed. Select a DEM, then compute the stream network.")
        ttk.Label(parent, textvariable=self.drainage_status_var, foreground='#205090',
                  wraplength=700, justify='left').grid(
            row=5, column=0, columnspan=3, sticky='w', pady=(3, 0))

    def _create_advanced_section(self):
        """Create the advanced options section (collapsible)."""
        self.advanced_frame = CollapsibleFrame(self.main_frame, title="Advanced Options")
        self.advanced_frame.grid(row=9, column=0, columnspan=3, sticky='ew', pady=5)

        content = self.advanced_frame.content

        # Interpolation points
        ttk.Label(content, text="Interpolation Points:").grid(row=0, column=0, sticky='w', pady=3)
        self.interp_pts_var = tk.IntVar(value=1000)
        interp_spin = ttk.Spinbox(content, from_=100, to=10000, increment=100,
                                   textvariable=self.interp_pts_var, width=10)
        interp_spin.grid(row=0, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(content, text="(100-10000, higher = more detail)", foreground='gray').grid(
            row=0, column=2, sticky='w', pady=3
        )

        # K neighbors
        ttk.Label(content, text="K Neighbors:").grid(row=1, column=0, sticky='w', pady=3)
        self.k_var = tk.StringVar(value="auto")
        k_entry = ttk.Entry(content, textvariable=self.k_var, width=10)
        k_entry.grid(row=1, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(content, text="('auto' or integer, affects smoothing)", foreground='gray').grid(
            row=1, column=2, sticky='w', pady=3
        )

        # Error tolerance
        ttk.Label(content, text="Error Tolerance (eps):").grid(row=2, column=0, sticky='w', pady=3)
        self.eps_var = tk.DoubleVar(value=0.1)
        eps_spin = ttk.Spinbox(content, from_=0.0, to=1.0, increment=0.05,
                                textvariable=self.eps_var, width=10)
        eps_spin.grid(row=2, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(content, text="(0=exact, higher=faster)", foreground='gray').grid(
            row=2, column=2, sticky='w', pady=3
        )

        # Workers
        ttk.Label(content, text="CPU Workers:").grid(row=3, column=0, sticky='w', pady=3)
        max_workers = os.cpu_count() or 4
        default_workers = max(1, max_workers // 2)
        self.workers_var = tk.IntVar(value=default_workers)
        workers_spin = ttk.Spinbox(content, from_=1, to=max_workers, increment=1,
                                    textvariable=self.workers_var, width=10)
        workers_spin.grid(row=3, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(content, text=f"(1-{max_workers} available)", foreground='gray').grid(
            row=3, column=2, sticky='w', pady=3
        )

        # Chunk size
        ttk.Label(content, text="Chunk Size:").grid(row=4, column=0, sticky='w', pady=3)
        self.chunk_var = tk.IntVar(value=1000000)
        chunk_combo = ttk.Combobox(content, textvariable=self.chunk_var, width=10,
                                    values=[100000, 500000, 1000000, 2000000, 5000000])
        chunk_combo.grid(row=4, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(content, text="(higher=faster but more RAM)", foreground='gray').grid(
            row=4, column=2, sticky='w', pady=3
        )

    def _create_visualization_section(self):
        """Create the visualization options section (collapsible)."""
        self.viz_frame = CollapsibleFrame(self.main_frame, title="Visualization Options")
        self.viz_frame.grid(row=10, column=0, columnspan=3, sticky='ew', pady=5)

        content = self.viz_frame.content

        # Create visualization checkbox
        self.create_viz_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(content, text="Create visualization after REM generation",
                        variable=self.create_viz_var, command=self._toggle_viz_options).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=3
        )

        # Colormap
        ttk.Label(content, text="Colormap:").grid(row=1, column=0, sticky='w', pady=3)
        self.cmap_var = tk.StringVar(value="mako_r")
        cmap_combo = ttk.Combobox(content, textvariable=self.cmap_var, width=15,
                                   values=self.COLORMAPS, state='readonly')
        cmap_combo.grid(row=1, column=1, sticky='w', padx=10, pady=3)

        # Z factor (vertical exaggeration)
        ttk.Label(content, text="Vertical Exaggeration:").grid(row=2, column=0, sticky='w', pady=3)
        self.z_var = tk.IntVar(value=4)
        z_spin = ttk.Spinbox(content, from_=1, to=20, increment=1,
                              textvariable=self.z_var, width=10)
        z_spin.grid(row=2, column=1, sticky='w', padx=10, pady=3)

        # Blend percent
        ttk.Label(content, text="Hillshade Blend %:").grid(row=3, column=0, sticky='w', pady=3)
        self.blend_var = tk.IntVar(value=25)
        blend_spin = ttk.Spinbox(content, from_=0, to=100, increment=5,
                                  textvariable=self.blend_var, width=10)
        blend_spin.grid(row=3, column=1, sticky='w', padx=10, pady=3)

        # Output format checkboxes
        format_frame = ttk.Frame(content)
        format_frame.grid(row=4, column=0, columnspan=3, sticky='w', pady=3)

        self.make_png_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(format_frame, text="Create PNG", variable=self.make_png_var).pack(side='left', padx=(0, 20))

        self.make_kmz_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(format_frame, text="Create KMZ (Google Earth)", variable=self.make_kmz_var).pack(side='left')

        # Separator
        ttk.Separator(self.main_frame, orient='horizontal').grid(
            row=11, column=0, columnspan=3, sticky='ew', pady=10
        )

    def _create_action_buttons(self):
        """Create the main action buttons."""
        button_frame = ttk.Frame(self.main_frame)
        button_frame.grid(row=12, column=0, columnspan=3, pady=10)

        self.run_btn = ttk.Button(button_frame, text="Generate REM",
                                   command=self._start_processing, style='Run.TButton', width=20)
        self.run_btn.pack(side='left', padx=5)

        self.cancel_btn = ttk.Button(button_frame, text="Cancel",
                                      command=self._cancel_processing, state='disabled', width=15)
        self.cancel_btn.pack(side='left', padx=5)

        ttk.Button(button_frame, text="Clear Log", command=self._clear_console, width=15).pack(side='left', padx=5)

    def _create_console(self):
        """Create the output console."""
        ttk.Label(self.main_frame, text="Output Log", style='Header.TLabel').grid(
            row=13, column=0, columnspan=3, sticky='w', pady=(10, 5)
        )

        # Console frame with scrollbar
        console_frame = ttk.Frame(self.main_frame)
        console_frame.grid(row=14, column=0, columnspan=3, sticky='nsew', pady=5)

        self.main_frame.rowconfigure(14, weight=1)

        self.console = tk.Text(console_frame, height=12, wrap='word', state='disabled',
                                font=('Consolas', 9), bg='#1e1e1e', fg='#d4d4d4',
                                insertbackground='white')
        scrollbar = ttk.Scrollbar(console_frame, orient='vertical', command=self.console.yview)
        self.console.configure(yscrollcommand=scrollbar.set)

        self.console.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # Configure text tags for different message types
        self.console.tag_configure('info', foreground='#d4d4d4')
        self.console.tag_configure('success', foreground='#4ec9b0')
        self.console.tag_configure('warning', foreground='#dcdcaa')
        self.console.tag_configure('error', foreground='#f14c4c')

    def _create_status_bar(self):
        """Create the status bar at the bottom."""
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.main_frame, textvariable=self.status_var,
                               style='Status.TLabel', relief='sunken', anchor='w')
        status_bar.grid(row=15, column=0, columnspan=3, sticky='ew', pady=(5, 0))

    def _setup_output_redirect(self):
        """Setup stdout redirection to the console."""
        self.stdout_redirector = StdoutRedirector(self.message_queue)

        # Also add custom handler to logger
        handler = TextHandler(self.console, self.message_queue)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(handler)

    def _process_queue(self):
        """Process messages from the queue and update the console."""
        try:
            while True:
                msg = self.message_queue.get_nowait()
                # _log() queues (text, level) tuples; stdout/logging queue plain text
                if isinstance(msg, tuple):
                    self._append_to_console(*msg)
                else:
                    self._append_to_console(msg)
        except queue.Empty:
            pass

        # Schedule next check
        self.root.after(100, self._process_queue)

    def _append_to_console(self, text, tag='info'):
        """Append text to the console widget."""
        self.console.config(state='normal')
        self.console.insert('end', text, tag)
        self.console.see('end')
        self.console.config(state='disabled')

    def _log(self, message, level='info'):
        """Log a message to the console with appropriate formatting."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_msg = f"[{timestamp}] {message}\n"
        self.message_queue.put((formatted_msg, level))

    def _clear_console(self):
        """Clear the console output."""
        self.console.config(state='normal')
        self.console.delete('1.0', 'end')
        self.console.config(state='disabled')

    def _browse_dem(self):
        """Open file dialog for DEM selection."""
        filetypes = [
            ("GeoTIFF files", "*.tif *.tiff"),
            ("All raster files", "*.tif *.tiff *.img *.dem"),
            ("All files", "*.*")
        ]
        filepath = filedialog.askopenfilename(title="Select DEM File", filetypes=filetypes)
        if filepath:
            self.dem_var.set(filepath)
            self._update_dem_info(filepath)

    def _browse_outdir(self):
        """Open directory dialog for output directory."""
        dirpath = filedialog.askdirectory(title="Select Output Directory")
        if dirpath:
            self.outdir_var.set(dirpath)

    def _browse_shapefile(self):
        """Open file dialog for centerline file selection."""
        filetypes = [
            ("Vector files", "*.shp *.geojson *.json *.gpkg *.kml"),
            ("Shapefiles", "*.shp"),
            ("GeoJSON", "*.geojson *.json"),
            ("GeoPackage", "*.gpkg"),
            ("KML", "*.kml"),
            ("All files", "*.*")
        ]
        filepath = filedialog.askopenfilename(title="Select Centerline File", filetypes=filetypes)
        if filepath:
            self.shapefile_var.set(filepath)
            self._log(f"Custom centerline file selected: {os.path.basename(filepath)}")

    def _toggle_centerline_source(self):
        """Toggle between OSM and custom centerline source."""
        is_custom = self.centerline_source.get() == "custom"
        state = 'normal' if is_custom else 'disabled'
        self.shapefile_entry.config(state=state)
        self.shapefile_btn.config(state=state)

    def _toggle_viz_options(self):
        """Toggle visualization options based on checkbox."""
        # This could enable/disable viz options if needed
        pass

    # ------------------------------------------------------------------ #
    # DEM drainage network (pytopotoolbox)
    # ------------------------------------------------------------------ #
    def _on_dem_path_changed(self, *_):
        """Invalidate a computed network/selection if the DEM path changes."""
        net = self.drainage_network
        if net is not None and os.path.abspath(self.dem_var.get().strip()) != os.path.abspath(net.dem_path):
            self.drainage_network = None
            self.drainage_selection = None
            self.open_map_btn.config(state='disabled')
            self.drainage_status_var.set(
                "DEM changed. Recompute the stream network for the new DEM.")

    def _parse_drainage_params(self):
        """Read and validate drainage-tab parameters. Returns dict or raises ValueError."""
        try:
            threshold = float(self.threshold_var.get())
            if threshold <= 0:
                raise ValueError
        except ValueError:
            raise ValueError("Critical drainage area must be a positive number")

        res_str = self.analysis_res_var.get().strip().lower()
        if res_str in ('', 'native', 'auto'):
            analysis_res = None
        else:
            try:
                analysis_res = float(res_str)
                if analysis_res <= 0:
                    raise ValueError
            except ValueError:
                raise ValueError("Analysis cell size must be 'native' or a positive number")

        try:
            buffer = float(self.ws_buffer_var.get() or 0)
            if buffer < 0:
                raise ValueError
        except ValueError:
            raise ValueError("Watershed buffer must be a non-negative number")

        try:
            smooth = float(self.smooth_var.get() or 0)
            if smooth < 0:
                raise ValueError
        except ValueError:
            raise ValueError("Trunk smoothing must be a non-negative number of cells")

        return {'threshold': threshold, 'units': self.threshold_units_var.get(),
                'analysis_res': analysis_res, 'buffer': buffer, 'smooth': smooth}

    def _start_compute_network(self):
        """Compute flow routing + stream network in a background thread."""
        if self.network_busy or self.is_processing:
            return
        dem_path = self.dem_var.get().strip()
        if not dem_path or not os.path.isfile(dem_path):
            messagebox.showerror("No DEM", "Please select a valid DEM file first.")
            return
        try:
            params = self._parse_drainage_params()
        except ValueError as e:
            messagebox.showerror("Invalid parameter", str(e))
            return

        self.network_busy = True
        self.compute_net_btn.config(state='disabled')
        self.open_map_btn.config(state='disabled')
        self.run_btn.config(state='disabled')
        self.status_var.set("Computing stream network...")
        self.drainage_status_var.set("Computing stream network...")

        def work():
            try:
                from drainage_selector import DrainageNetwork
                self._log("=" * 50)
                self._log("Computing DEM-derived stream network (pytopotoolbox)...")
                net = DrainageNetwork(dem_path, log=self._log)
                net.compute(params['threshold'], params['units'], params['analysis_res'])
                self.root.after(0, lambda: self._network_ready(net))
            except ImportError as e:
                msg = str(e)
                self._log(f"Error: {msg}", 'error')
                self.root.after(0, lambda: self._network_failed(msg))
            except Exception as e:
                import traceback
                msg = str(e)
                self._log(f"Error computing stream network: {msg}", 'error')
                self._log(traceback.format_exc(), 'error')
                self.root.after(0, lambda: self._network_failed(msg))

        threading.Thread(target=work, daemon=True).start()

    def _network_ready(self, net):
        self.network_busy = False
        self.drainage_network = net
        self.drainage_selection = None
        self.compute_net_btn.config(state='normal')
        self.open_map_btn.config(state='normal')
        self.run_btn.config(state='normal')
        self.status_var.set("Ready")
        self._log("Stream network ready. Click '2. Select on Map...' to choose a drainage.",
                  'success')
        self.drainage_status_var.set(
            f"Network ready ({net.S.stream.size:,} stream cells, threshold "
            f"{net.threshold:g} {net.units}). No drainage selected yet.")
        self._open_network_map()

    def _network_failed(self, msg):
        self.network_busy = False
        self.compute_net_btn.config(state='normal')
        self.run_btn.config(state='normal')
        self.status_var.set("Ready")
        self.drainage_status_var.set(f"Network computation failed: {msg}")
        messagebox.showerror("Stream network error", msg)

    def _open_network_map(self):
        """Open the interactive map for picking an outlet / trunk."""
        if self.drainage_network is None:
            messagebox.showinfo("No network", "Compute the stream network first.")
            return
        try:
            from drainage_picker import NetworkPickerWindow
            NetworkPickerWindow(self.root, self.drainage_network, self._on_drainage_selected,
                                initial=self.drainage_selection, log=self._log)
        except ImportError as e:
            self._log(f"Could not open the map window: {e}", 'error')
            messagebox.showerror(
                "Missing dependency",
                f"Could not open the map window:\n{e}\n\n"
                "The map needs matplotlib's Tk backend and Pillow "
                "(pip install pillow matplotlib).")
        except Exception as e:
            import traceback
            self._log(f"Could not open the map window: {e}", 'error')
            self._log(traceback.format_exc(), 'error')
            messagebox.showerror("Map error", f"Could not open the map window:\n{e}")

    def _on_drainage_selected(self, sel):
        self.drainage_selection = sel
        mode = "outlet (auto trunk)" if sel.mode == 'outlet' else "manual trunk"
        self.drainage_status_var.set(f"Selected by {mode}. {sel.summary}")
        self._log(f"Drainage selected by {mode}: {sel.summary}", 'success')
        for w in sel.warnings:
            self._log(f"  Warning: {w}", 'warning')

    def _update_dem_info(self, filepath):
        """Read and display DEM information."""
        try:
            from osgeo import gdal
            gdal.UseExceptions()

            ds = gdal.Open(filepath)
            if ds is None:
                self.dem_info_var.set("Could not read DEM file")
                return

            # Get basic info
            cols = ds.RasterXSize
            rows = ds.RasterYSize
            bands = ds.RasterCount

            # Get projection info
            proj = ds.GetProjection()

            # Get resolution
            gt = ds.GetGeoTransform()
            res_x = abs(gt[1])
            res_y = abs(gt[5])

            # Determine projection type
            proj_type = "Unknown"
            if "UTM" in proj.upper():
                proj_type = "UTM"
            elif "GEOGCS" in proj and "PROJCS" not in proj:
                proj_type = "Geographic (lat/lon) - Consider reprojecting to UTM"
            elif proj:
                proj_type = "Projected"

            info = f"{cols}x{rows} pixels, {res_x:.2f}x{res_y:.2f} units, {proj_type}"
            self.dem_info_var.set(info)

            self._log(f"DEM loaded: {os.path.basename(filepath)}")
            self._log(f"  Size: {cols}x{rows} pixels, {bands} band(s)")
            self._log(f"  Resolution: {res_x:.4f} x {res_y:.4f}")
            self._log(f"  Projection: {proj_type}")

            ds = None  # Close dataset

        except Exception as e:
            self.dem_info_var.set(f"Error reading DEM: {str(e)}")
            self._log(f"Error reading DEM: {str(e)}", 'error')

    def _validate_inputs(self):
        """Validate all inputs before processing."""
        errors = []

        dem_path = self.dem_var.get().strip()
        if not dem_path:
            errors.append("Please select a DEM file")
        elif not os.path.isfile(dem_path):
            errors.append(f"DEM file not found: {dem_path}")

        out_dir = self.outdir_var.get().strip()
        if not out_dir:
            errors.append("Please select an output directory")
        elif not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
                self._log(f"Created output directory: {out_dir}")
            except Exception as e:
                errors.append(f"Could not create output directory: {e}")

        if self._using_drainage_mode():
            try:
                self._parse_drainage_params()
            except ValueError as e:
                errors.append(str(e))
            if self.network_busy:
                errors.append("Stream network is still being computed")
            elif self.drainage_network is None:
                errors.append("Compute the stream network (step 1) on the "
                              "'Select from DEM Drainage' tab")
            elif self.drainage_selection is None:
                errors.append("Select a drainage or trunk stream on the map (step 2)")
            elif dem_path and os.path.abspath(dem_path) != os.path.abspath(self.drainage_network.dem_path):
                errors.append("The stream network was computed for a different DEM; recompute it")
        elif self.centerline_source.get() == "custom":
            shp_path = self.shapefile_var.get().strip()
            if not shp_path:
                errors.append("Please select a centerline file or use OSM")
            elif not os.path.isfile(shp_path):
                errors.append(f"Centerline file not found: {shp_path}")

        # Validate k value
        k_str = self.k_var.get().strip()
        if k_str.lower() != 'auto':
            try:
                k = int(k_str)
                if k < 1:
                    errors.append("K neighbors must be a positive integer or 'auto'")
            except ValueError:
                errors.append("K neighbors must be a positive integer or 'auto'")

        return errors

    def _start_processing(self):
        """Start the REM generation process."""
        # Validate inputs
        errors = self._validate_inputs()
        if errors:
            messagebox.showerror("Validation Error", "\n".join(errors))
            return

        # Show confirmation dialog
        message = (
            "Before proceeding, please confirm:\n\n"
            "1. Your DEM is properly georeferenced\n"
            "2. UTM projection is recommended for best results\n"
            "3. Processing may take several minutes for large DEMs\n\n"
            "Continue with REM generation?"
        )
        if not messagebox.askyesno("Confirm Processing", message):
            return

        # Capture the centerline mode on the main thread (Tk is not thread-safe)
        self._run_drainage_mode = self._using_drainage_mode()

        # Update UI state
        self.is_processing = True
        self.run_btn.config(state='disabled')
        self.cancel_btn.config(state='normal')
        self.status_var.set("Processing...")

        # Start processing thread
        self.processing_thread = threading.Thread(target=self._run_rem_maker, daemon=True)
        self.processing_thread.start()

    def _cancel_processing(self):
        """Request cancellation of processing."""
        if self.is_processing:
            self._log("Cancellation requested. Processing will stop after current step...")
            self.is_processing = False

    def _setup_gdal_compatibility(self):
        """Setup GDAL import compatibility for older libraries like RiverREM.

        GDAL 3.x changed the import style from 'import gdal' to 'from osgeo import gdal'.
        This shim allows libraries using the old import style to work with newer GDAL.
        """
        try:
            # Check if gdal is already importable directly
            import gdal
            return True
        except ImportError:
            pass

        try:
            # Try to import from osgeo and create compatibility aliases
            from osgeo import gdal, ogr, osr, gdal_array

            # Add osgeo modules to sys.modules with their old names
            sys.modules['gdal'] = gdal
            sys.modules['ogr'] = ogr
            sys.modules['osr'] = osr
            sys.modules['gdal_array'] = gdal_array

            self._log("GDAL compatibility shim applied (osgeo -> gdal)")
            return True
        except ImportError as e:
            self._log(f"Could not setup GDAL compatibility: {e}", 'error')
            return False

    def _setup_shapely_compatibility(self):
        """Setup Shapely import compatibility for older libraries like osmnx.

        Shapely 2.0+ made several breaking changes:
        1. Moved TopologicalError from shapely.geos to shapely.errors
        2. Multi* geometries are no longer directly iterable (must use .geoms)

        This shim patches these for backwards compatibility.
        """
        patched_anything = False

        # Patch 1: TopologicalError location
        try:
            from shapely.geos import TopologicalError
        except ImportError:
            try:
                from shapely.errors import TopologicalError
                import shapely.geos
                shapely.geos.TopologicalError = TopologicalError
                self._log("Shapely compatibility: TopologicalError patched")
                patched_anything = True
            except ImportError:
                pass

        # Patch 2: Make Multi* geometries iterable (Shapely 2.0 breaking change)
        try:
            from shapely.geometry import MultiPolygon, MultiLineString, MultiPoint

            # Check if MultiPolygon is already iterable
            test_iter_needed = False
            try:
                # In Shapely 2.0+, this will fail
                iter(MultiPolygon())
            except TypeError:
                test_iter_needed = True
            except Exception:
                # Empty MultiPolygon might raise other errors, check another way
                if not hasattr(MultiPolygon, '__iter__') or MultiPolygon.__iter__ is None:
                    test_iter_needed = True

            if test_iter_needed:
                # Add __iter__ method to Multi* classes to yield from .geoms
                def multi_iter(self):
                    return iter(self.geoms)

                MultiPolygon.__iter__ = multi_iter
                MultiLineString.__iter__ = multi_iter
                MultiPoint.__iter__ = multi_iter

                self._log("Shapely compatibility: Multi* geometry iteration patched")
                patched_anything = True

        except Exception as e:
            self._log(f"Warning: Could not patch Multi* iteration: {e}", 'warning')

        if patched_anything:
            self._log("Shapely compatibility shim applied")

        return True

    def _setup_osmnx_compatibility(self):
        """Setup osmnx compatibility for RiverREM.

        osmnx 2.0+ made breaking changes:
        1. Renamed geometries_from_bbox -> features_from_bbox
        2. Changed bbox from positional (north, south, east, west) to
           a single tuple (west, south, east, north)
        """
        try:
            import osmnx

            # Check if old API still exists
            if hasattr(osmnx, 'geometries_from_bbox'):
                return True

            # osmnx 2.0+: need to create compatibility wrapper
            if hasattr(osmnx, 'features_from_bbox'):
                def geometries_from_bbox(north, south, east, west, tags):
                    # Convert old (north, south, east, west) positional args
                    # to new bbox=(west, south, east, north) tuple format
                    return osmnx.features_from_bbox(
                        bbox=(west, south, east, north), tags=tags
                    )

                osmnx.geometries_from_bbox = geometries_from_bbox
                self._log("osmnx compatibility: geometries_from_bbox patched")
                return True

            # Try the features module directly
            if hasattr(osmnx, 'features') and hasattr(osmnx.features, 'features_from_bbox'):
                def geometries_from_bbox(north, south, east, west, tags):
                    return osmnx.features.features_from_bbox(
                        bbox=(west, south, east, north), tags=tags
                    )

                osmnx.geometries_from_bbox = geometries_from_bbox
                self._log("osmnx compatibility: geometries_from_bbox patched (via features module)")
                return True

            self._log("Warning: osmnx missing both geometries_from_bbox and features_from_bbox", 'warning')
            return False
        except ImportError as e:
            self._log(f"Could not setup osmnx compatibility: {e}", 'error')
            return False

    def _run_rem_maker(self):
        """Run the REM generation in a background thread."""
        # Redirect stdout for this thread
        old_stdout = sys.stdout
        sys.stdout = self.stdout_redirector

        try:
            self._log("=" * 50)
            self._log("Starting REM generation...")
            self._log("=" * 50)

            # Setup compatibility shims before importing RiverREM
            self._log("Setting up library compatibility...")
            if not self._setup_gdal_compatibility():
                self._log("Error: GDAL is not properly installed.", 'error')
                self._log("  Install with: conda install -c conda-forge gdal", 'error')
                raise ImportError("GDAL not available")

            if not self._setup_shapely_compatibility():
                self._log("Warning: Shapely compatibility shim failed, continuing anyway...", 'warning')

            if not self._setup_osmnx_compatibility():
                self._log("Warning: osmnx compatibility shim failed, continuing anyway...", 'warning')

            # Import RiverREM
            self._log("Loading RiverREM library...")
            try:
                from riverrem.REMMaker import REMMaker
            except ImportError as e:
                self._log(f"Error: Could not import RiverREM. Make sure it's installed.", 'error')
                self._log(f"  Install with: pip install riverrem", 'error')
                self._log(f"  Or from GitHub: pip install git+https://github.com/OpenTopography/RiverREM.git", 'error')
                raise

            # Get parameters
            dem_path = self.dem_var.get().strip()
            out_dir = self.outdir_var.get().strip()

            # Centerline
            centerline_shp = None
            drainage_mode = self._run_drainage_mode
            if drainage_mode:
                # Export the DEM-derived trunk (+ watershed-clipped DEM) and
                # feed them to RiverREM as a custom centerline.
                params = self._parse_drainage_params()
                net = self.drainage_network
                sel = self.drainage_selection
                self._log("Using DEM-derived drainage network (pytopotoolbox)")
                self._log(f"  {sel.summary}")
                dem_name = os.path.splitext(os.path.basename(dem_path))[0]
                prefix = f"{dem_name}_ws{sel.outlet_node}"
                paths = net.export(sel, out_dir, prefix=prefix,
                                   buffer_m=params['buffer'],
                                   clip_dem=self.clip_ws_var.get(),
                                   smooth_cells=params['smooth'])
                centerline_shp = paths['trunk_shp']
                dem_path = paths['dem']
                if self.clip_ws_var.get():
                    self._log(f"REM will be computed on the watershed-clipped DEM: "
                              f"{os.path.basename(dem_path)}")
                if not self.is_processing:
                    self._log("Processing cancelled by user")
                    return
            elif self.centerline_source.get() == "custom":
                centerline_shp = self.shapefile_var.get().strip()
                self._log(f"Using custom centerline: {os.path.basename(centerline_shp)}")
            else:
                self._log("Using OpenStreetMap for river centerline detection")

            # Advanced options
            interp_pts = self.interp_pts_var.get()
            k_str = self.k_var.get().strip()
            k = None if k_str.lower() == 'auto' else int(k_str)
            eps = self.eps_var.get()
            workers = self.workers_var.get()
            chunk_size = self.chunk_var.get()

            self._log(f"DEM: {os.path.basename(dem_path)}")
            self._log(f"Output directory: {out_dir}")
            self._log(f"Interpolation points: {interp_pts}")
            self._log(f"K neighbors: {'auto' if k is None else k}")
            self._log(f"Workers: {workers}")

            # Create REMMaker instance - handle different API versions
            self._log("\nInitializing REMMaker...")

            # Check which parameters REMMaker accepts (varies by version)
            import inspect
            try:
                sig = inspect.signature(REMMaker.__init__)
                available_params = set(sig.parameters.keys())
            except (ValueError, TypeError):
                available_params = set()

            # Build kwargs based on available parameters
            rem_kwargs = {'dem': dem_path, 'out_dir': out_dir}

            # Add optional parameters if supported by this version
            optional_params = {
                'centerline_shp': centerline_shp,
                'interp_pts': interp_pts,
                'k': k,
                'eps': eps,
                'workers': workers,
                'chunk_size': int(chunk_size)
            }

            if drainage_mode and available_params and 'centerline_shp' not in available_params:
                raise RuntimeError(
                    "This RiverREM version does not accept a custom centerline, which the "
                    "DEM-drainage workflow requires. Install the GitHub version:\n"
                    "  pip install git+https://github.com/OpenTopography/RiverREM.git")

            for param, value in optional_params.items():
                if param in available_params or (param == 'centerline_shp' and drainage_mode):
                    rem_kwargs[param] = value
                elif param == 'centerline_shp' and value is not None:
                    self._log(f"Warning: Custom centerline not supported in this RiverREM version", 'warning')
                    self._log(f"  Install latest: pip install git+https://github.com/OpenTopography/RiverREM.git", 'warning')

            # Log which API version we're using
            if 'centerline_shp' in available_params:
                self._log("Using full RiverREM API (GitHub version)")
            else:
                self._log("Using basic RiverREM API (PyPI version 0.0.1)")
                self._log("  For more options, install from GitHub")

            rem_maker = REMMaker(**rem_kwargs)

            # Fix GDAL 3.x compatibility: ensure cell dimensions are positive
            # floats. RiverREM uses these in gdal.Rasterize -tr option, but
            # gt[5] from GeoTransform is typically negative, and newer GDAL
            # rejects negative -tr values. Also convert from numpy types.
            if hasattr(rem_maker, 'cell_h') and rem_maker.cell_h is not None:
                rem_maker.cell_h = abs(float(rem_maker.cell_h))
            if hasattr(rem_maker, 'cell_w') and rem_maker.cell_w is not None:
                rem_maker.cell_w = abs(float(rem_maker.cell_w))

            if not self.is_processing:
                self._log("Processing cancelled by user")
                return

            # Generate REM
            self._log("\nGenerating REM (this may take a while)...")
            try:
                rem_path = rem_maker.make_rem()
            except ImportError as e:
                err_msg = str(e)
                if 'pyogrio' in err_msg or 'fiona' in err_msg or 'read_file' in err_msg:
                    self._log("Error: Could not read centerline file.", 'error')
                    self._log("  The 'pyogrio' or 'fiona' package is missing or failed to load.", 'error')
                    self._log("  Install with: pip install pyogrio", 'error')
                    self._log("  Or: pip install fiona", 'error')
                    raise
                raise
            except Exception as e:
                err_msg = str(e)
                if centerline_shp and ('crs' in err_msg.lower() or 'projection' in err_msg.lower()
                                       or 'transform' in err_msg.lower()):
                    self._log(f"Error reading centerline file: {e}", 'error')
                    self._log("  The centerline file may have an incompatible projection.", 'error')
                    self._log("  Ensure the centerline CRS matches or can be reprojected to the DEM CRS.", 'error')
                    raise
                raise
            self._log(f"REM created: {rem_path}", 'success')

            if not self.is_processing:
                self._log("Processing cancelled by user")
                return

            # Create visualization if requested
            if self.create_viz_var.get():
                self._log("\nCreating visualization...")

                cmap = self.cmap_var.get()
                z = self.z_var.get()
                blend = self.blend_var.get()
                make_png = self.make_png_var.get()
                make_kmz = self.make_kmz_var.get()

                self._log(f"  Colormap: {cmap}")
                self._log(f"  Vertical exaggeration: {z}x")
                self._log(f"  Hillshade blend: {blend}%")

                rem_maker.make_rem_viz(
                    cmap=cmap,
                    z=z,
                    blend_percent=blend,
                    make_png=make_png,
                    make_kmz=make_kmz
                )
                self._log("Visualization created successfully!", 'success')

            # Clean up cache
            self._log("\nCleaning up temporary files...")
            rem_maker.clean_up()

            self._log("\n" + "=" * 50)
            self._log("REM GENERATION COMPLETE!", 'success')
            self._log("=" * 50)
            self._log(f"\nOutput files saved to: {out_dir}")

            # Show success message on main thread
            self.root.after(0, lambda: messagebox.showinfo(
                "Success",
                f"REM generation complete!\n\nOutput saved to:\n{out_dir}"
            ))

        except Exception as e:
            self._log(f"\nERROR: {str(e)}", 'error')
            import traceback
            self._log(traceback.format_exc(), 'error')

            # Capture error message before except block exits (Python 3
            # deletes 'e' after the except block, causing NameError in lambda)
            error_msg = str(e)

            # Show error message on main thread
            self.root.after(0, lambda: messagebox.showerror(
                "Error",
                f"An error occurred during processing:\n\n{error_msg}"
            ))

        finally:
            sys.stdout = old_stdout
            self.is_processing = False

            # Update UI on main thread
            self.root.after(0, self._processing_complete)

    def _processing_complete(self):
        """Reset UI state after processing completes."""
        self.run_btn.config(state='normal')
        self.cancel_btn.config(state='disabled')
        self.status_var.set("Ready")


def main():
    """Main entry point for the application."""
    root = tk.Tk()

    # Set application icon if available
    try:
        # You can add an icon file here
        pass
    except:
        pass

    app = REMApp(root)

    # Handle window close
    def on_closing():
        if app.is_processing:
            if messagebox.askokcancel("Quit", "Processing is in progress. Are you sure you want to quit?"):
                root.destroy()
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
