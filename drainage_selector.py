# -*- coding: utf-8 -*-
"""
DEM-derived drainage network selection for REM generation.

This module derives a stream network directly from a DEM using
pytopotoolbox (https://github.com/TopoToolbox/pytopotoolbox), lets a caller
pick a drainage (by its outlet) or a trunk stream (by a channel head and an
outlet), and exports everything RiverREM needs to build a watershed-clipped
REM:

    * the trunk stream as a line shapefile (used as RiverREM's centerline)
    * the selected upstream network and the watershed polygon (GeoPackage)
    * a copy of the original DEM masked/cropped to the watershed

It is deliberately free of any GUI code so it can be scripted and tested on
its own.

Terminology
-----------
D8      : single-flow-direction routing to the steepest of 8 neighbours.
node    : one stream pixel in the StreamObject's node attribute list.
trunk   : the single flow path from a channel head to the outlet that the
          REM is detrended against.

Notes on coordinates
--------------------
pytopotoolbox's ``StreamObject.x/y/xy()`` (v0.0.12) return the *upper-left
corner* of each pixel (``transform * (col, row)``).  For export we use pixel
*centres* (``transform * (col + 0.5, row + 0.5)``) so that RiverREM samples
the DEM in the channel pixel rather than half a cell up and to the left.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

# Units accepted for the channel-initiation (critical drainage area) threshold
AREA_UNITS = ('km2', 'm2', 'pixels')

# Trunk definitions
TRUNK_BY_AREA = 'area'          # follow the tributary with the largest drainage area
TRUNK_BY_LENGTH = 'length'      # follow the longest flow path (TopoToolbox default)


def _require_topotoolbox():
    try:
        import topotoolbox  # noqa: F401
    except ImportError as e:  # pragma: no cover - depends on environment
        raise ImportError(
            "pytopotoolbox is required for drainage-network selection.\n"
            "  Install with: pip install topotoolbox"
        ) from e
    import topotoolbox
    return topotoolbox


@dataclass
class DrainageSelection:
    """Result of selecting a drainage / trunk stream."""

    mode: str                           # 'outlet' or 'manual'
    outlet_node: int                    # index into the full network's node list
    head_node: Optional[int]            # channel-head node (manual mode only)
    trunk_nodes: np.ndarray             # node indices, ordered upstream -> downstream
    network_mask: np.ndarray            # bool node mask of the network upstream of outlet
    basin_mask: np.ndarray              # bool grid mask (analysis grid) of the watershed
    basin_polygon: object               # shapely (Multi)Polygon in DEM CRS
    area_km2: float
    trunk_length_m: float
    touches_edge: bool                  # watershed reaches DEM edge / nodata
    warnings: list = field(default_factory=list)

    @property
    def summary(self) -> str:
        txt = (f"Drainage area: {self.area_km2:,.3f} km² | "
               f"Trunk length: {self.trunk_length_m / 1000:,.2f} km")
        if self.touches_edge:
            txt += " | WARNING: watershed touches DEM edge/nodata"
        return txt


class DrainageNetwork:
    """Flow routing + stream network for a DEM, with selection and export.

    Parameters
    ----------
    dem_path : str
        Path to the input DEM (GeoTIFF or anything rasterio can read).
    log : callable, optional
        Function taking a single string, used for progress messages.
    """

    def __init__(self, dem_path: str, log: Optional[Callable[[str], None]] = None):
        self.dem_path = dem_path
        self.log = log or (lambda msg: None)
        self.tt = _require_topotoolbox()

        self.dem = None
        self.fd = None
        self.acc = None
        self.S = None
        self.threshold = None
        self.units = None
        self.analysis_res = None

    # ------------------------------------------------------------------ #
    # Network computation
    # ------------------------------------------------------------------ #
    def compute(self, threshold: float, units: str = 'km2',
                analysis_res: Optional[float] = None,
                sink_resolution: str = 'carve') -> None:
        """Run flow routing and extract the stream network.

        Parameters
        ----------
        threshold : float
            Critical (channel-initiation) drainage area.
        units : {'km2', 'm2', 'pixels'}
            Units of ``threshold``.
        analysis_res : float, optional
            If given and coarser than the DEM, the DEM is resampled to this
            cell size (map units) before flow routing. Speeds up large lidar
            DEMs at the cost of a less precise thalweg.
        sink_resolution : {'carve', 'lcat'}
            How pytopotoolbox routes flow through pits/flats. 'carve' cuts
            through obstructions (e.g. road fills / bridges in lidar).
        """
        if units not in AREA_UNITS:
            raise ValueError(f"units must be one of {AREA_UNITS}")
        if threshold <= 0:
            raise ValueError("Threshold must be positive")

        tt = self.tt
        self.log(f"Reading DEM: {os.path.basename(self.dem_path)}")
        dem = tt.read_tif(self.dem_path)

        if dem.georef is None:
            self.log("WARNING: DEM has no CRS. Areas/lengths will be in pixel units.")
        elif not dem.georef.is_projected:
            self.log("WARNING: DEM is in geographic coordinates. Area thresholds in "
                     "m²/km² will be wrong; reproject to UTM first.")

        native = float(dem.cellsize)
        if analysis_res and analysis_res > native * 1.0001:
            self.log(f"Resampling DEM from {native:g} to {analysis_res:g} map units "
                     "for flow routing...")
            dem = dem.resample(analysis_res)
            # resample() can return a non-float grid or zero-filled nodata;
            # make sure nodata stays NaN.
            dem.z = np.asarray(dem.z, dtype=np.float32)
        ncells = dem.shape[0] * dem.shape[1]
        self.log(f"Analysis grid: {dem.shape[1]} x {dem.shape[0]} cells "
                 f"({ncells / 1e6:.1f} M), cell size {float(dem.cellsize):g}")
        if ncells > 100e6:
            self.log("WARNING: >100 M cells. Flow routing may need many GB of RAM; "
                     "consider a coarser analysis resolution.")

        self.log(f"Routing flow (D8, sink resolution = {sink_resolution})...")
        fd = tt.FlowObject(dem, sink_resolution=sink_resolution)
        self.log("Computing flow accumulation...")
        acc = fd.flow_accumulation()

        self.log(f"Extracting stream network (threshold = {threshold:g} {units})...")
        S = tt.StreamObject(fd, units=units, threshold=threshold)
        if S.stream.size == 0:
            raise RuntimeError("No stream pixels exceed the threshold. "
                               "Try a smaller critical drainage area.")

        self.dem, self.fd, self.acc, self.S = dem, fd, acc, S
        self.threshold, self.units, self.analysis_res = threshold, units, analysis_res
        self._build_node_tables()

        self.log(f"Stream network: {S.stream.size:,} nodes, "
                 f"{int(self.outlets.sum()):,} outlets, "
                 f"max Strahler order {int(self.order.max())}")

    def _build_node_tables(self):
        """Pre-compute per-node attributes used for snapping and tracing."""
        from scipy.spatial import cKDTree

        S = self.S
        rows, cols = S.node_indices
        self.node_rows = np.asarray(rows)
        self.node_cols = np.asarray(cols)
        # Pixel CENTRE coordinates (see module docstring)
        xs, ys = S.transform * np.vstack((self.node_cols + 0.5, self.node_rows + 0.5))
        self.node_x = np.asarray(xs, dtype=np.float64)
        self.node_y = np.asarray(ys, dtype=np.float64)
        self._tree = cKDTree(np.column_stack((self.node_x, self.node_y)))

        self.cell_area = float(self.dem.cellsize) ** 2
        self.node_acc = np.asarray(S.ezgetnal(self.acc), dtype=np.float64)   # pixels
        self.node_area_km2 = self.node_acc * self.cell_area / 1e6
        # NB: in pytopotoolbox, downstream_distance() is the longest flow-path
        # length from the channel heads down to each node (0 at heads), while
        # upstream_distance() is the distance measured up from the outlet.
        self.node_updist = np.asarray(S.downstream_distance(), dtype=np.float64)
        self.order = np.asarray(S.streamorder('strahler'))
        self.outlets = np.asarray(S.streampoi('outlets'), dtype=bool)

        src = np.asarray(S.source)
        tgt = np.asarray(S.target)
        self.src, self.tgt = src, tgt
        # D8: every node has at most one receiver
        self.receiver = np.full(S.stream.size, -1, dtype=np.int64)
        self.receiver[src] = tgt

    # ------------------------------------------------------------------ #
    # Display helpers
    # ------------------------------------------------------------------ #
    def display_segments(self):
        """Return (segments, strahler_order) for every network edge.

        segments has shape (n_edges, 2, 2) in map coordinates (pixel centres),
        suitable for a matplotlib LineCollection.
        """
        seg = np.empty((self.src.size, 2, 2), dtype=np.float64)
        seg[:, 0, 0] = self.node_x[self.src]
        seg[:, 0, 1] = self.node_y[self.src]
        seg[:, 1, 0] = self.node_x[self.tgt]
        seg[:, 1, 1] = self.node_y[self.tgt]
        return seg, self.order[self.src]

    def display_hillshade(self, max_dim: int = 2000):
        """Return (hillshade_array, extent) of a decimated DEM for plotting."""
        z = np.asarray(self.dem.z, dtype=np.float32)
        step = max(1, int(np.ceil(max(z.shape) / max_dim)))
        zs = z[::step, ::step]
        cs = float(self.dem.cellsize) * step
        hs = _hillshade(zs, cs)
        b = self.dem.bounds
        extent = (b.left, b.right, b.bottom, b.top)
        return hs, extent

    def subset_segments(self, node_mask: np.ndarray):
        """Segments of edges whose source and target are both in node_mask."""
        keep = node_mask[self.src] & node_mask[self.tgt]
        seg, _ = self.display_segments()
        return seg[keep]

    # ------------------------------------------------------------------ #
    # Selection
    # ------------------------------------------------------------------ #
    def snap(self, x: float, y: float, min_area_km2: float = 0.0):
        """Snap a map coordinate to the nearest stream node.

        Returns (node_index, distance). If ``min_area_km2`` > 0 only nodes
        with at least that drainage area are considered.
        """
        if min_area_km2 > 0:
            cand = np.flatnonzero(self.node_area_km2 >= min_area_km2)
            if cand.size == 0:
                cand = np.arange(self.node_x.size)
            d = np.hypot(self.node_x[cand] - x, self.node_y[cand] - y)
            i = int(np.argmin(d))
            return int(cand[i]), float(d[i])
        d, i = self._tree.query([x, y])
        return int(i), float(d)

    def _upstream_mask(self, node: int) -> np.ndarray:
        """Bool node mask of all nodes draining to ``node`` (inclusive)."""
        S = self.S
        nal = np.zeros(S.stream.size, dtype=bool)
        nal[node] = True
        # edges are topologically sorted (upstream first), so one reverse
        # sweep propagates the flag from receivers to donors.
        src, tgt = self.src, self.tgt
        for e in range(src.size - 1, -1, -1):
            if nal[tgt[e]]:
                nal[src[e]] = True
        return nal

    def _upstream_mask_fast(self, node: int) -> np.ndarray:
        """Vectorised upstream mask using pytopotoolbox's compiled traversal."""
        try:
            sub = self.S.upstreamto(self._onehot(node))
            return np.isin(self.S.stream, sub.stream)
        except Exception:
            return self._upstream_mask(node)

    def _onehot(self, node: int) -> np.ndarray:
        nal = np.zeros(self.S.stream.size, dtype=bool)
        nal[node] = True
        return nal

    def trace_trunk(self, outlet: int, method: str = TRUNK_BY_AREA) -> np.ndarray:
        """Trace the trunk upstream from ``outlet``.

        At every confluence the donor with the largest metric is followed:
        drainage area (``'area'``) or upstream flow-path length
        (``'length'``). Returns node indices ordered upstream -> downstream.
        """
        src, tgt = self.src, self.tgt
        if method == TRUNK_BY_AREA:
            edge_metric = self.node_acc[src]
        else:
            # longest path through each donor edge = donor's upstream length
            # + the edge itself (diagonal edges are longer than cardinal ones)
            edge_metric = self.node_updist[src] + np.hypot(
                self.node_x[src] - self.node_x[tgt], self.node_y[src] - self.node_y[tgt])
        # For each receiver pick the donor edge with the maximum metric.
        order = np.lexsort((edge_metric, tgt))
        tgt_sorted = tgt[order]
        last = np.r_[tgt_sorted[1:] != tgt_sorted[:-1], True]
        best_donor = np.full(self.S.stream.size, -1, dtype=np.int64)
        best_donor[tgt_sorted[last]] = src[order][last]

        path = [outlet]
        cur = outlet
        while best_donor[cur] >= 0:
            cur = int(best_donor[cur])
            path.append(cur)
        return np.asarray(path[::-1], dtype=np.int64)

    def trace_downstream(self, head: int, outlet: int) -> np.ndarray:
        """Flow path from ``head`` down to ``outlet`` (upstream -> downstream).

        Raises ValueError if ``head`` does not drain through ``outlet``.
        """
        path = [head]
        cur = head
        while cur != outlet:
            nxt = int(self.receiver[cur])
            if nxt < 0:
                raise ValueError(
                    "The upstream point does not drain to the selected outlet. "
                    "Pick an outlet downstream of the upstream point on the same network.")
            path.append(nxt)
            cur = nxt
        return np.asarray(path, dtype=np.int64)

    def select_outlet(self, outlet: int, trunk_method: str = TRUNK_BY_AREA) -> DrainageSelection:
        """Select the whole drainage above ``outlet`` and its trunk stream."""
        trunk = self.trace_trunk(outlet, trunk_method)
        return self._finish_selection('outlet', outlet, None, trunk)

    def select_path(self, head: int, outlet: int) -> DrainageSelection:
        """Select a manual trunk running from ``head`` down to ``outlet``."""
        trunk = self.trace_downstream(head, outlet)
        return self._finish_selection('manual', outlet, head, trunk)

    def _finish_selection(self, mode, outlet, head, trunk) -> DrainageSelection:
        network = self._upstream_mask_fast(outlet)
        basin_grid = self.fd.drainagebasins(np.array([self.S.stream[outlet]]))
        basin = np.asarray(basin_grid.z) > 0
        area_km2 = basin.sum() * self.cell_area / 1e6

        dx = np.diff(self.node_x[trunk])
        dy = np.diff(self.node_y[trunk])
        length = float(np.hypot(dx, dy).sum())

        touches = _touches_edge(basin, np.asarray(self.dem.z))
        warnings = []
        if touches:
            warnings.append("The watershed reaches the DEM boundary or nodata cells, so "
                            "its true drainage area is probably larger than computed.")
        if trunk.size < 10:
            warnings.append("Trunk is very short (<10 cells); the REM will be poorly "
                            "constrained.")

        poly = _mask_to_polygon(basin, self.dem.transform)

        return DrainageSelection(
            mode=mode, outlet_node=int(outlet), head_node=None if head is None else int(head),
            trunk_nodes=trunk, network_mask=network, basin_mask=basin,
            basin_polygon=poly, area_km2=float(area_km2), trunk_length_m=length,
            touches_edge=bool(touches), warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def trunk_linestring(self, sel: DrainageSelection, smooth_cells: float = 0.0):
        """Shapely LineString of the trunk (pixel centres, upstream -> downstream).

        ``smooth_cells`` > 0 applies Douglas-Peucker simplification with a
        tolerance of that many cells to remove D8 stair-stepping.
        """
        from shapely.geometry import LineString
        coords = np.column_stack((self.node_x[sel.trunk_nodes], self.node_y[sel.trunk_nodes]))
        line = LineString(coords)
        if smooth_cells > 0:
            line = line.simplify(smooth_cells * float(self.dem.cellsize), preserve_topology=False)
        return line

    def network_lines(self, sel: DrainageSelection):
        """List of shapely LineStrings for each edge of the selected network,
        merged into continuous lines, with Strahler order attribute."""
        from shapely.geometry import LineString, MultiLineString
        from shapely.ops import linemerge

        mask = sel.network_mask
        keep = mask[self.src] & mask[self.tgt]
        src, tgt = self.src[keep], self.tgt[keep]
        out = []
        for k in np.unique(self.order[src]):
            sel_e = self.order[src] == k
            segs = [((self.node_x[a], self.node_y[a]), (self.node_x[b], self.node_y[b]))
                    for a, b in zip(src[sel_e], tgt[sel_e])]
            merged = linemerge(MultiLineString(segs))
            geoms = list(merged.geoms) if hasattr(merged, 'geoms') else [merged]
            out.extend((int(k), g) for g in geoms if isinstance(g, LineString))
        return out

    def export(self, sel: DrainageSelection, out_dir: str, prefix: Optional[str] = None,
               buffer_m: float = 0.0, clip_dem: bool = True,
               smooth_cells: float = 0.0) -> dict:
        """Write trunk centerline, network, watershed and (optionally) clipped DEM.

        Returns a dict of output paths: 'trunk_shp', 'network_gpkg',
        'basin_gpkg', and 'dem' (the clipped DEM, or the original DEM when
        ``clip_dem`` is False).
        """
        import geopandas as gpd

        os.makedirs(out_dir, exist_ok=True)
        if prefix is None:
            prefix = os.path.splitext(os.path.basename(self.dem_path))[0]
        crs = self.dem.georef
        paths = {}

        # Trunk centerline -> shapefile (most robust format for RiverREM)
        trunk_line = self.trunk_linestring(sel, smooth_cells)
        gdf_trunk = gpd.GeoDataFrame(
            {
                'mode': [sel.mode],
                'area_km2': [round(sel.area_km2, 4)],
                'length_m': [round(trunk_line.length, 2)],
                'thresh': [float(self.threshold)],
                'th_units': [self.units],
            },
            geometry=[trunk_line], crs=crs)
        paths['trunk_shp'] = os.path.join(out_dir, f"{prefix}_trunk_centerline.shp")
        gdf_trunk.to_file(paths['trunk_shp'])
        self.log(f"Wrote trunk centerline: {os.path.basename(paths['trunk_shp'])}")

        # Network (for QA in GIS)
        net = self.network_lines(sel)
        if net:
            gdf_net = gpd.GeoDataFrame({'strahler': [k for k, _ in net]},
                                       geometry=[g for _, g in net], crs=crs)
            paths['network_gpkg'] = os.path.join(out_dir, f"{prefix}_stream_network.gpkg")
            gdf_net.to_file(paths['network_gpkg'], driver='GPKG')
            self.log(f"Wrote stream network: {os.path.basename(paths['network_gpkg'])}")

        # Watershed polygon
        gdf_basin = gpd.GeoDataFrame({'area_km2': [round(sel.area_km2, 4)],
                                      'edge_warn': [int(sel.touches_edge)]},
                                     geometry=[sel.basin_polygon], crs=crs)
        paths['basin_gpkg'] = os.path.join(out_dir, f"{prefix}_watershed.gpkg")
        gdf_basin.to_file(paths['basin_gpkg'], driver='GPKG')
        self.log(f"Wrote watershed polygon: {os.path.basename(paths['basin_gpkg'])}")

        if clip_dem:
            paths['dem'] = os.path.join(out_dir, f"{prefix}_watershed_dem.tif")
            clip_dem_to_polygon(self.dem_path, sel.basin_polygon, paths['dem'],
                                buffer=buffer_m)
            self.log(f"Wrote watershed-clipped DEM: {os.path.basename(paths['dem'])}")
        else:
            paths['dem'] = self.dem_path

        return paths


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _hillshade(z: np.ndarray, cellsize: float, azimuth: float = 315.0,
               altitude: float = 45.0) -> np.ndarray:
    """Simple Horn-style hillshade (0-1) that tolerates NaNs."""
    zf = np.where(np.isnan(z), np.nanmean(z) if np.isfinite(z).any() else 0.0, z)
    gy, gx = np.gradient(zf, cellsize)
    slope = np.pi / 2.0 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az = np.radians(360.0 - azimuth + 90.0)
    alt = np.radians(altitude)
    hs = (np.sin(alt) * np.sin(slope)
          + np.cos(alt) * np.cos(slope) * np.cos(az - aspect))
    hs = np.clip(hs, 0, 1)
    hs[np.isnan(z)] = np.nan
    return hs


def _touches_edge(mask: np.ndarray, z: np.ndarray) -> bool:
    """True if mask touches the grid border or a NaN (nodata) cell."""
    if mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any():
        return True
    nan = np.isnan(z)
    if not nan.any():
        return False
    from scipy.ndimage import binary_dilation
    return bool((binary_dilation(nan, structure=np.ones((3, 3), bool)) & mask).any())


def _mask_to_polygon(mask: np.ndarray, transform):
    """Polygonise a boolean mask; returns the union of all True regions."""
    from rasterio import features
    from shapely.geometry import shape
    from shapely.ops import unary_union

    polys = [shape(geom) for geom, val in
             features.shapes(mask.astype(np.uint8), mask=mask, transform=transform)
             if val == 1]
    return unary_union(polys)


def clip_dem_to_polygon(dem_path: str, polygon, out_path: str, buffer: float = 0.0,
                        nodata: float = -9999.0) -> str:
    """Crop ``dem_path`` to ``polygon`` (+``buffer`` map units), masking outside.

    Uses the original (full-resolution) DEM regardless of the analysis
    resolution. Output is float32 with an explicit, non-zero nodata value
    (RiverREM treats a nodata value of 0 as "no nodata").
    """
    import rasterio
    from rasterio.mask import mask as rio_mask

    geom = polygon.buffer(buffer) if buffer else polygon
    with rasterio.open(dem_path) as src:
        arr, transform = rio_mask(src, [geom.__geo_interface__], crop=True,
                                  all_touched=True, filled=False, indexes=1)
        data = arr.astype(np.float32).filled(np.nan)
        if src.nodata is not None:
            data[data == np.float32(src.nodata)] = np.nan
        data = np.where(np.isnan(data), nodata, data).astype(np.float32)
        profile = src.profile.copy()

    profile.update(driver='GTiff', height=data.shape[0], width=data.shape[1],
                   transform=transform, dtype='float32', nodata=nodata, count=1,
                   compress='deflate', tiled=True, blockxsize=256, blockysize=256,
                   BIGTIFF='IF_SAFER')
    # Tiled output requires dimensions >= block size in some drivers
    if data.shape[0] < 256 or data.shape[1] < 256:
        for k in ('tiled', 'blockxsize', 'blockysize'):
            profile.pop(k, None)
    with rasterio.open(out_path, 'w', **profile) as dst:
        dst.write(data, 1)
    return out_path
