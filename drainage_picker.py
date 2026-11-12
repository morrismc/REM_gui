# -*- coding: utf-8 -*-
"""
Interactive map window for picking a drainage / trunk stream.

Shows a hillshade of the DEM with the pytopotoolbox stream network on top.
The user zooms/pans with the matplotlib toolbar and clicks to select:

* Outlet mode  - one click on a stream picks the outlet; the whole upstream
                 network, its watershed and an automatic trunk are selected.
* Manual mode  - first click picks an upstream point (channel head end),
                 second click picks the outlet; the trunk is the D8 flow path
                 between them.

Clicks are snapped to the nearest stream pixel (optionally only to streams
with at least a given drainage area, which helps grab the main stem instead
of a tiny tributary next to it).
"""

import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np

from drainage_selector import TRUNK_BY_AREA, TRUNK_BY_LENGTH


class NetworkPickerWindow(tk.Toplevel):
    """Toplevel window with an embedded matplotlib map for selection.

    Parameters
    ----------
    parent : tk widget
    network : drainage_selector.DrainageNetwork
        A network on which ``compute()`` has already been run.
    on_accept : callable
        Called with the accepted ``DrainageSelection``.
    initial : DrainageSelection, optional
        A previous selection to display when the window opens.
    """

    def __init__(self, parent, network, on_accept, initial=None, log=None):
        super().__init__(parent)
        self.title("Select Drainage / Trunk Stream")
        self.geometry("1000x800")
        self.minsize(700, 550)

        self.net = network
        self.on_accept = on_accept
        self.log = log or (lambda msg: None)
        self.selection = initial
        self.pending_head = None          # manual mode: first click
        self.overlay = []                 # artists to clear on reset

        self.mode_var = tk.StringVar(value='outlet')
        self.trunk_var = tk.StringVar(value=TRUNK_BY_AREA)
        self.min_area_var = tk.StringVar(value='0')
        self.status_var = tk.StringVar()

        self._build_controls()
        try:
            self._build_map()
        except Exception:
            # Don't leave a half-built window behind (e.g. missing PIL.ImageTk)
            self.destroy()
            raise
        self._update_instructions()

        if initial is not None:
            self._draw_selection(initial)

        self.transient(parent)
        self.focus_set()

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_controls(self):
        ctl = ttk.Frame(self, padding=(8, 6))
        ctl.pack(side='top', fill='x')

        mode = ttk.LabelFrame(ctl, text="Selection mode", padding=4)
        mode.pack(side='left', fill='y', padx=(0, 8))
        ttk.Radiobutton(mode, text="Click outlet → whole drainage + automatic trunk",
                        variable=self.mode_var, value='outlet',
                        command=self._on_mode_change).pack(anchor='w')
        ttk.Radiobutton(mode, text="Click upstream point, then outlet → manual trunk",
                        variable=self.mode_var, value='manual',
                        command=self._on_mode_change).pack(anchor='w')

        trunk = ttk.LabelFrame(ctl, text="Automatic trunk follows", padding=4)
        trunk.pack(side='left', fill='y', padx=(0, 8))
        self.rb_area = ttk.Radiobutton(trunk, text="Largest drainage area",
                                       variable=self.trunk_var, value=TRUNK_BY_AREA,
                                       command=self._recompute_trunk)
        self.rb_area.pack(anchor='w')
        self.rb_len = ttk.Radiobutton(trunk, text="Longest flow path",
                                      variable=self.trunk_var, value=TRUNK_BY_LENGTH,
                                      command=self._recompute_trunk)
        self.rb_len.pack(anchor='w')

        snap = ttk.LabelFrame(ctl, text="Snap to streams with area ≥", padding=4)
        snap.pack(side='left', fill='y', padx=(0, 8))
        f = ttk.Frame(snap)
        f.pack(anchor='w')
        ttk.Entry(f, textvariable=self.min_area_var, width=8).pack(side='left')
        ttk.Label(f, text="km²").pack(side='left', padx=(4, 0))

        btns = ttk.Frame(ctl)
        btns.pack(side='right', fill='y')
        ttk.Button(btns, text="Reset", command=self._reset, width=18).pack(pady=1)
        self.accept_btn = ttk.Button(btns, text="Use This Selection",
                                     command=self._accept, width=18,
                                     state='normal' if self.selection else 'disabled')
        self.accept_btn.pack(pady=1)
        ttk.Button(btns, text="Close", command=self.destroy, width=18).pack(pady=1)

        info = ttk.Frame(self, padding=(8, 0))
        info.pack(side='top', fill='x')
        self.instr_label = ttk.Label(info, foreground='#205090')
        self.instr_label.pack(anchor='w')
        ttk.Label(info, textvariable=self.status_var, foreground='gray').pack(anchor='w')

    def _build_map(self):
        from matplotlib.figure import Figure
        from matplotlib.collections import LineCollection
        from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                                       NavigationToolbar2Tk)

        frame = ttk.Frame(self)
        frame.pack(side='top', fill='both', expand=True)

        self.fig = Figure(figsize=(9, 7), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_aspect('equal')

        hs, extent = self.net.display_hillshade()
        self.ax.imshow(hs, extent=extent, cmap='gray', vmin=0, vmax=1,
                       interpolation='bilinear', origin='upper')

        seg, order = self.net.display_segments()
        omax = max(1, int(order.max()))
        widths = 0.4 + 1.6 * (order - 1) / max(1, omax - 1)
        lc = LineCollection(seg, linewidths=widths, colors='#1f6fd1', alpha=0.85)
        self.ax.add_collection(lc)
        self.ax.set_xlim(extent[0], extent[1])
        self.ax.set_ylim(extent[2], extent[3])
        self.ax.ticklabel_format(useOffset=False, style='plain')
        self.ax.tick_params(labelsize=7)
        self.ax.set_title(
            f"Stream network: threshold {self.net.threshold:g} {self.net.units} "
            f"(line width ∝ Strahler order)", fontsize=9)
        self.fig.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.toolbar = NavigationToolbar2Tk(self.canvas, frame, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side='bottom', fill='x')
        self.canvas.get_tk_widget().pack(side='top', fill='both', expand=True)
        self.canvas.mpl_connect('button_press_event', self._on_click)
        self.canvas.mpl_connect('motion_notify_event', self._on_motion)
        self.canvas.draw()

    # ------------------------------------------------------------------ #
    # Interaction
    # ------------------------------------------------------------------ #
    def _update_instructions(self):
        if self.mode_var.get() == 'outlet':
            txt = ("Click on a stream to set the OUTLET. Use the toolbar's "
                   "zoom/pan first (then click the tool again to turn it off).")
        elif self.pending_head is None:
            txt = "Manual trunk: click the UPSTREAM end of the trunk on a stream."
        else:
            txt = "Manual trunk: now click the OUTLET (downstream end) on the same network."
        self.instr_label.config(text=txt)
        state = 'normal' if self.mode_var.get() == 'outlet' else 'disabled'
        self.rb_area.config(state=state)
        self.rb_len.config(state=state)

    def _on_mode_change(self):
        self.pending_head = None
        self._clear_overlay()
        self.selection = None
        self.accept_btn.config(state='disabled')
        self.status_var.set("")
        self._update_instructions()
        self.canvas.draw_idle()

    def _min_area(self):
        try:
            return max(0.0, float(self.min_area_var.get()))
        except ValueError:
            return 0.0

    def _on_motion(self, event):
        if event.inaxes is not self.ax or event.xdata is None:
            return
        if self.selection is None and self.pending_head is None:
            self.status_var.set(f"x = {event.xdata:,.1f}   y = {event.ydata:,.1f}")

    def _on_click(self, event):
        # Ignore clicks while zoom/pan is active or outside the map
        if getattr(self.toolbar, 'mode', ''):
            return
        if event.inaxes is not self.ax or event.button != 1 or event.xdata is None:
            return

        node, dist = self.net.snap(event.xdata, event.ydata, self._min_area())
        try:
            self.config(cursor='watch')
            self.update_idletasks()
            if self.mode_var.get() == 'outlet':
                sel = self.net.select_outlet(node, self.trunk_var.get())
                self._set_selection(sel)
            else:
                if self.pending_head is None:
                    self._clear_overlay()
                    self.selection = None
                    self.accept_btn.config(state='disabled')
                    self.pending_head = node
                    self._mark(node, 'o', '#ffd400', 'Upstream point')
                    self.status_var.set(
                        f"Upstream point snapped {dist:.1f} m to a stream with "
                        f"{self.net.node_area_km2[node]:.3f} km² drainage area")
                else:
                    head = self.pending_head
                    try:
                        sel = self.net.select_path(head, node)
                    except ValueError as e:
                        messagebox.showwarning("Invalid outlet", str(e), parent=self)
                        return
                    self.pending_head = None
                    self._set_selection(sel)
                self._update_instructions()
        except Exception as e:  # keep the window alive on unexpected errors
            messagebox.showerror("Selection error", str(e), parent=self)
        finally:
            self.config(cursor='')
            self.canvas.draw_idle()

    def _recompute_trunk(self):
        if self.selection is not None and self.selection.mode == 'outlet':
            sel = self.net.select_outlet(self.selection.outlet_node, self.trunk_var.get())
            self._set_selection(sel)
            self.canvas.draw_idle()

    def _set_selection(self, sel):
        self.selection = sel
        self._clear_overlay()
        self._draw_selection(sel)
        self.accept_btn.config(state='normal')
        msg = sel.summary
        if sel.warnings:
            msg += "\n" + "\n".join(sel.warnings)
        self.status_var.set(msg)

    # ------------------------------------------------------------------ #
    # Drawing
    # ------------------------------------------------------------------ #
    def _clear_overlay(self):
        for a in self.overlay:
            try:
                a.remove()
            except Exception:
                pass
        self.overlay = []

    def _mark(self, node, marker, color, label):
        (h,) = self.ax.plot(self.net.node_x[node], self.net.node_y[node], marker=marker,
                            ms=9, mfc=color, mec='k', mew=1.2, ls='none', zorder=10,
                            label=label)
        self.overlay.append(h)
        return h

    def _draw_selection(self, sel):
        from matplotlib.collections import LineCollection

        # Watershed outline
        poly = sel.basin_polygon
        geoms = list(poly.geoms) if hasattr(poly, 'geoms') else [poly]
        first = True
        for g in geoms:
            x, y = g.exterior.xy
            (h,) = self.ax.plot(x, y, color='#e8590c', lw=1.5, zorder=5,
                                label='Watershed' if first else None)
            first = False
            self.overlay.append(h)

        # Upstream network
        seg = self.net.subset_segments(sel.network_mask)
        if len(seg):
            lc = LineCollection(seg, colors='#00b7c7', linewidths=1.0, zorder=6)
            self.ax.add_collection(lc)
            self.overlay.append(lc)

        # Trunk
        tn = sel.trunk_nodes
        (h,) = self.ax.plot(self.net.node_x[tn], self.net.node_y[tn], color='#d6001c',
                            lw=2.5, zorder=8, label='Trunk (REM centerline)')
        self.overlay.append(h)

        self._mark(sel.outlet_node, 'v', '#d6001c', 'Outlet')
        if sel.head_node is not None:
            self._mark(sel.head_node, 'o', '#ffd400', 'Upstream point')

        leg = self.ax.legend(loc='upper right', fontsize=8, framealpha=0.85)
        self.overlay.append(leg)

    # ------------------------------------------------------------------ #
    # Buttons
    # ------------------------------------------------------------------ #
    def _reset(self):
        self.pending_head = None
        self.selection = None
        self._clear_overlay()
        self.accept_btn.config(state='disabled')
        self.status_var.set("")
        self._update_instructions()
        self.canvas.draw_idle()

    def _accept(self):
        if self.selection is None:
            return
        self.on_accept(self.selection)
        self.destroy()
