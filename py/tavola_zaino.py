#!/usr/bin/env python3
"""
TAVOLA STRUTTURALE LEGGERA PER ZAINO  -  generatore parametrico
================================================================

Lo stesso file gira:
  * da riga di comando (CPython):  python3 tavola_zaino.py --out cartella
  * nel browser (Pyodide), usato dal sito tramite le funzioni api_*().

Genera un modello PIENO da stampare SENZA primo/ultimo layer (solo perimetri +
infill). Rigidita' da:
  * fori "isogrid" (reticolo triangolare, nervature dritte in 3 direzioni);
  * spessore pieno (rigidita' ~ spessore^3), bombatura opzionale;
  * cornice perimetrale.

Sezionamento automatico in base al piatto: i tagli corrono LUNGO l'asse delle
nervature (linee dritte + zig-zag), quindi ogni giunzione e' una lunga superficie
di incollaggio. Incastri verticali a testa tonda o coda di rondine, con gioco,
rastremazione (battuta) e pad di materiale.

Dipendenze: numpy, shapely >= 2.0 e un triangolatore tra:
  mapbox_earcut (pip install mapbox_earcut)  |  earcut.js (nel browser)  |  triangle
Unita': mm.  X = lato lungo, Y = lato corto, Z = spessore.
"""

from __future__ import annotations

import argparse
import io
import itertools
import json
import os
import string
import struct
import time
import zipfile
from dataclasses import dataclass, asdict, fields

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import (LinearRing, LineString, MultiLineString, Point,
                              Polygon, box)
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

BIG = 1e4
VERSION = "1.0"

# ----------------------------------------------------------------------------
# Triangolatore disponibile
# ----------------------------------------------------------------------------
_TRI = None
try:
    import mapbox_earcut as _mec
    _TRI = "mapbox_earcut"
except Exception:
    try:
        import js as _js  # Pyodide
        if hasattr(_js, "earcut"):
            from pyodide.ffi import to_js as _to_js
            _TRI = "earcut.js"
    except Exception:
        pass
    if _TRI is None:
        try:
            import triangle as _triangle
            _TRI = "triangle"
        except Exception:
            pass


def _restore_collinear(T, loops, verts):
    """earcut elimina i vertici collineari del contorno: qui i triangoli che li
    'saltano' vengono divisi a ventaglio, cosi' ogni lato di bordo esiste e la
    mesh resta chiusa (niente T-junction con le pareti)."""
    ln = np.array([len(l) for l in loops])
    st = np.concatenate([[0], np.cumsum(ln)[:-1]])
    lid = np.repeat(np.arange(len(loops)), ln)
    pos = np.arange(len(verts)) - st[lid]
    # pre-filtro vettoriale: solo i triangoli con un lato tra vertici dello
    # stesso anello non adiacenti e "vicini" lungo l'anello
    sus = np.zeros(len(T), bool)
    for e in range(3):
        u, v = T[:, e], T[:, (e + 1) % 3]
        same = lid[u] == lid[v]
        n = ln[lid[u]]
        d = (pos[v] - pos[u]) % n
        dmin = np.minimum(d, n - d)
        sus |= same & (dmin > 1)
    out, queue = [T[~sus]], [tuple(t) for t in T[sus]]
    flat = []
    while queue:
        t = queue.pop()
        p0, p1, p2 = verts[t[0]], verts[t[1]], verts[t[2]]
        ar = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
        if abs(ar) < 1e-9:
            continue                      # triangolo degenere (area nulla): si scarta
        done = False
        for e in range(3):
            u, v, w = t[e], t[(e + 1) % 3], t[(e + 2) % 3]
            if lid[u] != lid[v]:
                continue
            n = ln[lid[u]]
            for direction in (1, -1):
                d = (direction * (pos[v] - pos[u])) % n
                if d <= 1:
                    continue
                a, b = verts[u], verts[v]
                ab = b - a
                L2 = ab @ ab
                if L2 <= 0:
                    continue
                # scarto rapido: il primo punto intermedio deve stare sul segmento
                f = verts[st[lid[u]] + (pos[u] + direction) % n] - a
                if abs(f[0] * ab[1] - f[1] * ab[0]) > 1e-6 * L2 or not (0 < f @ ab < L2):
                    continue
                mids = st[lid[u]] + (pos[u] + direction * np.arange(1, d)) % n
                pm = verts[mids] - a
                tt = pm @ ab / L2
                dist = np.abs(pm[:, 0] * ab[1] - pm[:, 1] * ab[0]) / np.sqrt(L2)
                if (dist < 1e-6 * max(1.0, np.sqrt(L2))).all() and (tt > 0).all() and (tt < 1).all():
                    seq = [u] + list(mids) + [v]
                    for i in range(len(seq) - 1):
                        queue.append((seq[i], seq[i + 1], w))
                    done = True
                    break
            if done:
                break
        if not done:
            flat.append(t)
    if flat:
        out.append(np.array(flat, dtype=np.int64))
    return np.vstack(out).astype(np.int64)


def _jitter(loops, eps=2e-5):
    """Sposta ogni vertice di una quantita' pseudo-casuale (|d| <= eps) lungo la
    normale: nessuna terna di punti resta esattamente collineare, quindi earcut
    li usa tutti (niente T-junction). I triangoli puntano ai vertici originali."""
    rng = np.random.default_rng(1234)
    out = []
    for l in loops:
        t = np.roll(l, -1, 0) - np.roll(l, 1, 0)
        t /= np.maximum(np.linalg.norm(t, axis=1)[:, None], 1e-12)
        nrm = np.column_stack([t[:, 1], -t[:, 0]])
        d = eps * rng.uniform(0.3, 1.0, len(l)) * rng.choice([-1.0, 1.0], len(l))
        out.append(l + nrm * d[:, None])
    return np.vstack(out)


def _earcut(loops):
    verts = np.vstack(loops).astype(np.float64)
    jv = _jitter(loops)
    if _TRI == "mapbox_earcut":
        ends = np.cumsum([len(l) for l in loops]).astype(np.uint32)
        T = np.asarray(_mec.triangulate_float64(jv, ends), dtype=np.int64).reshape(-1, 3)
    else:
        starts = np.cumsum([len(l) for l in loops])[:-1].tolist()
        res = _js.earcut(_to_js(jv.ravel().tolist()), _to_js(starts), 2)
        T = np.asarray(res.to_py(), dtype=np.int64).reshape(-1, 3)
    expected = len(verts) + 2 * (len(loops) - 1) - 2
    if len(T) != expected:
        T = _restore_collinear(T, loops, verts)
    return _ccw(T, jv)


def _ccw(T, V2):
    """Orienta i triangoli in senso antiorario (con le coordinate usate per la
    triangolazione, cosi' anche i triangoli quasi degeneri hanno il verso giusto)."""
    a, b, c = V2[T[:, 0]], V2[T[:, 1]], V2[T[:, 2]]
    sa = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    T = T.copy()
    T[sa < 0] = T[sa < 0][:, [0, 2, 1]]
    return T


def triangulate_loops(loops):
    """loops: lista di array (n,2); il primo e' il contorno, gli altri fori.
    Restituisce triangoli (m,3) sugli indici dei vertici concatenati."""
    verts = np.vstack(loops).astype(np.float64)
    if _TRI in ("mapbox_earcut", "earcut.js"):
        return _earcut(loops)
    if _TRI == "triangle":
        segs, off, hp = [], 0, []
        for i, l in enumerate(loops):
            m = len(l)
            segs.append(np.column_stack([np.arange(m), (np.arange(m) + 1) % m]) + off)
            if i:
                hp.append(Polygon(l).buffer(0).representative_point().coords[0])
            off += m
        d = dict(vertices=verts, segments=np.vstack(segs))
        if hp:
            d["holes"] = np.array(hp)
        r = _triangle.triangulate(d, "pY")
        if len(r["vertices"]) != len(verts):
            raise RuntimeError("triangle ha aggiunto vertici")
        return _ccw(r["triangles"].astype(np.int64), verts)
    raise RuntimeError("Nessun triangolatore: installa mapbox_earcut "
                       "(pip install mapbox_earcut)")


def refine(V2, T, max_len, max_iter=40):
    """Raffinamento conforme "rosso-verde": a ogni passata divide a meta' TUTTI
    i lati interni piu' lunghi di max_len (i lati di bordo non si toccano, cosi'
    la faccia resta cucita alle pareti)."""
    V2 = np.asarray(V2, float)
    T = np.asarray(T, np.int64)
    for _ in range(max_iter):
        m = len(T)
        N = len(V2) + 1
        e0 = np.stack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], axis=1).reshape(-1, 2)
        lo, hi = np.minimum(e0[:, 0], e0[:, 1]), np.maximum(e0[:, 0], e0[:, 1])
        key = lo * N + hi
        uk, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
        inv = inv.ravel()
        ua, ub = uk // N, uk % N
        ln = np.linalg.norm(V2[ua] - V2[ub], axis=1)
        mark = (cnt == 2) & (ln > max_len)
        if not mark.any():
            break
        mid = -np.ones(len(uk), np.int64)
        ns = np.nonzero(mark)[0]
        mid[ns] = len(V2) + np.arange(len(ns))
        V2 = np.vstack([V2, (V2[ua[ns]] + V2[ub[ns]]) / 2])
        M = mid[inv].reshape(m, 3)                 # punto medio dei lati e0,e1,e2
        k = (M >= 0).sum(axis=1)
        out = [T[k == 0]]
        # un lato marcato
        s1 = np.nonzero(k == 1)[0]
        if len(s1):
            j = np.argmax(M[s1] >= 0, axis=1)
            t = T[s1]; r_ = np.arange(len(s1))
            p, q, r = t[r_, j], t[r_, (j + 1) % 3], t[r_, (j + 2) % 3]
            mm = M[s1, j]
            out += [np.column_stack([p, mm, r]), np.column_stack([mm, q, r])]
        # due lati marcati: kk = lato NON marcato (a,b), c opposto
        s2 = np.nonzero(k == 2)[0]
        if len(s2):
            kk = np.argmin(M[s2] >= 0, axis=1)
            t = T[s2]; r_ = np.arange(len(s2))
            a_, b_, c_ = t[r_, kk], t[r_, (kk + 1) % 3], t[r_, (kk + 2) % 3]
            mbc, mca = M[s2, (kk + 1) % 3], M[s2, (kk + 2) % 3]
            out += [np.column_stack([a_, b_, mbc]), np.column_stack([a_, mbc, mca]),
                    np.column_stack([mca, mbc, c_])]
        # tre lati marcati
        s3 = np.nonzero(k == 3)[0]
        if len(s3):
            t, mm = T[s3], M[s3]
            out += [np.column_stack([t[:, 0], mm[:, 0], mm[:, 2]]),
                    np.column_stack([mm[:, 0], t[:, 1], mm[:, 1]]),
                    np.column_stack([mm[:, 2], mm[:, 1], t[:, 2]]),
                    np.column_stack([mm[:, 0], mm[:, 1], mm[:, 2]])]
        T = np.vstack(out)
    return V2, T


# =============================================================================
# PARAMETRI
# =============================================================================

@dataclass
class Params:
    # --- Forma generale -----------------------------------------------------
    length: float = 390.0
    width: float = 290.0
    corner_r: float = 20.0
    t_center: float = 20.0
    t_edge: float = 20.0
    crown_exp_long: float = 1.0
    crown_exp_short: float = 0.5
    bend_radius_long: float | None = None
    bend_radius_short: float | None = None

    # --- Fori isogrid -------------------------------------------------------
    frame: float = 10.0
    cell: float = 80.0
    rib: float = 5.0
    hole_corner_r: float = 8.0
    lattice_angle: float = 0.0
    min_hole_area: float = 120.0

    # --- Raccordi ---------------------------------------------------------------
    outer_top_r: float = 4.0
    outer_bot_r: float = 3.0
    outer_bot_chamfer: float = 1.5
    hole_top_r: float = 2.0
    hole_bot_r: float = 2.0
    hole_bot_chamfer: float = 0.6

    # --- Sezionamento --------------------------------------------------------
    split: bool = True
    bed_x: float = 250.0
    bed_y: float = 250.0
    bed_margin: float = 5.0
    seam_lines: list | None = None
    seam_zigzags: list | None = None
    stagger: bool = True
    max_pieces: int = 16

    # --- Incastri ------------------------------------------------------------
    key_type: str = "bulb"
    key_neck: float = 5.0
    key_head: float = 8.0
    key_neck_len: float = 3.0
    key_root: float = 3.0
    key_clearance: float = 0.15
    key_taper: float = 0.3
    key_fillet: float = 0.8
    key_wall: float = 1.5
    joint_spacing: float = 60.0
    junction_clear: float = 20.0
    joints_at_nodes: bool = True
    joints_at_frame: bool = True

    # --- Mesh ----------------------------------------------------------------
    ring_step: float = 1.0
    arc_steps: int = 6
    refine_len: float = 8.0
    blend: float = 6.0

    # --- Stima peso -------------------------------------------------------------
    est_perimeters: int = 2
    est_line_width: float = 0.45
    est_infill: float = 0.10
    est_density: float = 1.24

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        out = {}
        for k, v in (d or {}).items():
            if k in names:
                out[k] = v
        P = cls(**out)
        for k in ("bend_radius_long", "bend_radius_short"):
            v = getattr(P, k)
            if v in ("", 0, 0.0):
                setattr(P, k, None)
        for k in ("seam_lines", "seam_zigzags"):
            v = getattr(P, k)
            if isinstance(v, str):
                v = [float(x) for x in v.replace(";", ",").split(",") if x.strip()]
            setattr(P, k, list(v) if v else None)
        P.arc_steps = int(P.arc_steps)
        P.est_perimeters = int(P.est_perimeters)
        P.max_pieces = int(P.max_pieces)
        return P


QUALITY = {
    "preview": dict(ring_step=2.2, arc_steps=2, refine_len=22.0),
    "draft": dict(ring_step=1.6, arc_steps=4, refine_len=12.0),
    "standard": dict(ring_step=1.0, arc_steps=6, refine_len=8.0),
    "fine": dict(ring_step=0.6, arc_steps=8, refine_len=5.0),
}


def with_quality(P, q):
    d = asdict(P)
    d.update(QUALITY.get(q, {}))
    return Params.from_dict(d)


# =============================================================================
# GEOMETRIA DI BASE
# =============================================================================

def thickness(P, x, y):
    u = np.clip(np.asarray(x, float) / (P.length / 2), -1, 1)
    v = np.clip(np.asarray(y, float) / (P.width / 2), -1, 1)
    f = (np.cos(np.pi / 2 * u) ** 2) ** P.crown_exp_long * \
        (np.cos(np.pi / 2 * v) ** 2) ** P.crown_exp_short
    return P.t_edge + (P.t_center - P.t_edge) * f


def board_outline(P):
    r = min(P.corner_r, P.length / 2 - 1, P.width / 2 - 1)
    a, b = P.length / 2 - r, P.width / 2 - r
    return orient(Polygon([(-a, -b), (a, -b), (a, b), (-a, b)]).buffer(r, quad_segs=32), 1.0)


class Lattice:
    def __init__(self, a):
        self.a = a
        self.h = a * np.sqrt(3) / 2

    def off(self, j):
        return (j % 2) * self.a / 2

    def nearest_x(self, j, xc):
        o = self.off(j)
        return o + round((xc - o) / self.a) * self.a

    def nodes(self, bounds):
        minx, miny, maxx, maxy = bounds
        out = []
        for j in range(int(np.floor(miny / self.h)) - 1, int(np.ceil(maxy / self.h)) + 2):
            o = self.off(j)
            for i in range(int(np.floor((minx - o) / self.a)) - 1,
                           int(np.ceil((maxx - o) / self.a)) + 2):
                out.append((o + i * self.a, j * self.h))
        return out

    def rib_dirs(self):
        return [np.array([np.cos(t), np.sin(t)]) for t in np.radians([0, 60, 120])]


def isogrid_holes(P, board, lat):
    field = board.buffer(-P.frame, quad_segs=32)
    if field.is_empty:
        return []
    a, h = lat.a, lat.h
    minx, miny, maxx, maxy = board.bounds
    holes = []
    for j in range(int(miny / h) - 2, int(maxy / h) + 2):
        y0, y1 = j * h, (j + 1) * h
        o = lat.off(j)
        for i in range(int((minx - o) / a) - 2, int((maxx - o) / a) + 2):
            x0 = o + i * a
            for t in ([(x0, y0), (x0 + a, y0), (x0 + a / 2, y1)],
                      [(x0 + a / 2, y1), (x0 + 3 * a / 2, y1), (x0 + a, y0)]):
                p = Polygon(t).buffer(-P.rib / 2, join_style="mitre")
                if p.is_empty:
                    continue
                p = p.intersection(field)
                if p.is_empty:
                    continue
                p = p.buffer(-P.hole_corner_r, quad_segs=24).buffer(P.hole_corner_r, quad_segs=24)
                holes += [q for q in getattr(p, "geoms", [p])
                          if q.geom_type == "Polygon" and q.area >= P.min_hole_area]
    return holes


# =============================================================================
# SEZIONAMENTO
# =============================================================================

def make_seams(P, board, lat, n_lines, n_zig, stagger_off, man_lines=None, man_zig=None):
    minx, miny, maxx, maxy = board.bounds
    h = lat.h
    if man_lines is not None:
        ys = sorted({round(y / h) * h for y in man_lines})
    else:
        ys = sorted({round((miny + k * (maxy - miny) / (n_lines + 1)) / h) * h
                     for k in range(1, n_lines + 1)})
    ys = [y for y in ys if miny + P.frame < y < maxy - P.frame]
    lines = [LineString([(-BIG, y), (BIG, y)]) for y in ys]
    bands = list(zip([-BIG] + ys, ys + [BIG]))
    xs0 = list(man_zig) if man_zig is not None else \
        [minx + m * (maxx - minx) / (n_zig + 1) for m in range(1, n_zig + 1)]
    zigs = []
    for b, (lo, hi) in enumerate(bands):
        sh = (stagger_off / 2) * (1 if b % 2 else -1) if stagger_off else 0.0
        jlo = int(np.floor(max(lo, miny - 3 * h) / h)) - 1
        jhi = int(np.ceil(min(hi, maxy + 3 * h) / h)) + 1
        for xc in xs0:
            pts = [(lat.nearest_x(j, xc + sh), j * h) for j in range(jlo, jhi + 1)]
            ln = LineString(pts).intersection(box(-BIG, lo, BIG, hi))
            if not ln.is_empty:
                zigs.append(ln)
    return lines, zigs


def cells_from_seams(board, seams):
    parts = [board.exterior] + [s.intersection(board) for s in seams]
    noded = unary_union([p for p in parts if not p.is_empty])
    return [orient(c, 1.0) for c in polygonize(noded)
            if c.area > 1.0 and board.contains(c.representative_point())]


def fits_bed(P, poly):
    r = poly.minimum_rotated_rectangle
    c = np.array(r.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    l1, l2 = np.linalg.norm(e1), np.linalg.norm(e2)
    d = sorted([l1, l2])
    el = e1 if l1 >= l2 else e2
    ang = float(np.degrees(np.arctan2(el[1], el[0])))
    bx, by = sorted([P.bed_x - 2 * P.bed_margin, P.bed_y - 2 * P.bed_margin])
    return (d[0] <= bx + 1e-6 and d[1] <= by + 1e-6), tuple(d), ang


# =============================================================================
# INCASTRI
# =============================================================================

def key_local(P):
    wn, rh = P.key_neck / 2, P.key_head / 2
    D = P.key_neck_len + 2 * rh
    if P.key_type == "dovetail":
        k = Polygon([(-wn, -P.key_root), (wn, -P.key_root), (wn, 0),
                     (rh, D), (-rh, D), (-wn, 0)])
    else:
        neck = box(-wn, -P.key_root, wn, P.key_neck_len + rh * 0.5)
        head = Point(0, P.key_neck_len + rh).buffer(rh, quad_segs=24)
        k = neck.union(head)
    f = P.key_fillet
    k = k.buffer(f, quad_segs=8).buffer(-2 * f, quad_segs=8).buffer(f, quad_segs=8)
    return k, D


def to_world(geom, p, axis):
    u = np.array([axis[1], -axis[0]])
    return affinity.affine_transform(geom, [u[0], axis[0], u[1], axis[1], p[0], p[1]])


def place_joints(P, board, base_solid, lat, seams, cells):
    if P.key_type == "none":
        return []
    ring_r = P.key_head / 2 + P.key_clearance + P.key_taper + P.key_wall
    pad_r = ring_r + P.hole_top_r
    d_in = max(P.frame / 2, ring_r + max(P.outer_top_r, P.outer_bot_r + P.outer_bot_chamfer))
    inner = board.buffer(-d_in, quad_segs=32)
    if inner.is_empty:
        return []
    frame_ring = inner.exterior
    field = board.buffer(-P.frame, quad_segs=32)
    segs = [s.intersection(board) for s in seams]
    segs = [s for s in segs if not s.is_empty]
    junc = []
    for s1, s2 in itertools.combinations(segs, 2):
        x = s1.intersection(s2)
        junc += [g for g in getattr(x, "geoms", [x]) if g.geom_type == "Point"]
    nodes = [Point(n) for n in lat.nodes(board.bounds)]
    keyp, D = key_local(P)
    joints = []
    order = sorted(range(len(cells)), key=lambda i: (-round(cells[i].area), i))
    rank = {c: r for r, c in enumerate(order)}

    def cell_of(pt):
        for i, c in enumerate(cells):
            if c.contains(pt):
                return i
        return None

    for si, s in enumerate(segs):
        if s.geom_type != "LineString":
            s = shapely.line_merge(s)
            if s.geom_type != "LineString":
                continue
        cand = []
        if P.joints_at_frame:
            x = s.intersection(frame_ring)
            cand += [(g, "frame") for g in getattr(x, "geoms", [x]) if g.geom_type == "Point"]
        if P.joints_at_nodes:
            cand += [(n, "node") for n in nodes
                     if s.distance(n) < 1e-6 and field.contains(n) and base_solid.contains(n)]
        cand = [c for c in cand if all(c[0].distance(j) >= P.junction_clear for j in junc)]
        cand.sort(key=lambda c: (c[1] != "frame", s.project(c[0])))
        chosen = []
        for pt, kind in cand:
            if all(pt.distance(q) >= P.joint_spacing for q, _ in chosen):
                chosen.append((pt, kind))
        chosen.sort(key=lambda c: s.project(c[0]))
        for k, (pt, kind) in enumerate(chosen):
            d = s.project(pt)
            a1, a2 = s.interpolate(max(d - 1, 0)), s.interpolate(min(d + 1, s.length))
            tvec = np.array([a2.x - a1.x, a2.y - a1.y])
            tvec /= np.linalg.norm(tvec)
            if kind == "frame":
                df = frame_ring.project(pt)
                b1, b2 = frame_ring.interpolate(df - 1), frame_ring.interpolate(df + 1)
                axis = np.array([b2.x - b1.x, b2.y - b1.y])
                axis /= np.linalg.norm(axis)
            else:
                dirs = lat.rib_dirs()
                score = [abs(dv[0] * tvec[1] - dv[1] * tvec[0]) for dv in dirs]
                best = max(score)
                opts = [dv for dv, sc in zip(dirs, score) if sc > best - 1e-6]
                axis = opts[k % len(opts)]
            p = np.array([pt.x, pt.y])
            ca, cb = cell_of(Point(p + 2.5 * axis)), cell_of(Point(p - 2.5 * axis))
            if ca is None or cb is None or ca == cb:
                continue
            male = ca if rank[ca] > rank[cb] else cb
            female = cb if male == ca else ca
            if ca != female:
                axis = -axis
            key = to_world(keyp, p, axis)
            socket = key.buffer(P.key_clearance, quad_segs=8)
            pad = to_world(LineString([(0, -P.key_root), (0, D - P.key_head / 2)]).buffer(
                pad_r, quad_segs=16), p, axis)
            joints.append(dict(p=p, axis=axis, male=male, female=female, kind=kind,
                               key=key, socket=socket, pad=pad))
    return joints


# =============================================================================
# LAYOUT 2D
# =============================================================================

def layout_2d(P, n_lines, n_zig, stagger_off, board, lat, holes, base_solid):
    lines, zigs = make_seams(P, board, lat, n_lines, n_zig, stagger_off,
                             P.seam_lines, P.seam_zigzags)
    seams = lines + zigs
    cells = cells_from_seams(board, seams) if seams else [board]
    cells.sort(key=lambda c: (-round(c.centroid.y / 20), c.centroid.x))
    joints = place_joints(P, board, base_solid, lat, seams, cells)
    if joints:
        pads = unary_union([j["pad"] for j in joints])
        rr = min(4.0, P.hole_corner_r)
        new_holes = []
        for hl in holes:
            q = hl.difference(pads)
            if q.is_empty:
                continue
            q = q.buffer(-rr, quad_segs=16).buffer(rr, quad_segs=16)
            new_holes += [g for g in getattr(q, "geoms", [q])
                          if g.geom_type == "Polygon" and g.area >= P.min_hole_area]
        holes = new_holes
    solid = orient(Polygon(board.exterior.coords, [h.exterior.coords for h in holes]), 1.0)
    pieces = []
    for i, c in enumerate(cells):
        g = solid.intersection(c)
        for j in joints:
            if j["male"] == i:
                g = g.union(j["key"].intersection(solid))
        for j in joints:
            if j["female"] == i:
                g = g.difference(j["socket"])
        g = g.buffer(0)
        pieces.append([q for q in getattr(g, "geoms", [g])
                       if q.geom_type == "Polygon" and q.area > 30])
    return dict(seams=seams, cells=cells, joints=joints, solid=solid, pieces=pieces)


def choose_layout(P, board, lat, holes, base_solid):
    manual = P.seam_lines is not None or P.seam_zigzags is not None
    if not P.split or (not manual and fits_bed(P, board)[0]):
        return layout_2d(P, 0, 0, 0, board, lat, holes, base_solid) | dict(n=(0, 0), stagger=0)
    if manual:
        return layout_2d(P, 0, 0, 0, board, lat, holes, base_solid) | dict(n=("man", "man"), stagger=0)
    combos = sorted(((nl, nz) for nl in range(0, 6) for nz in range(0, 8) if nl + nz > 0),
                    key=lambda c: ((c[0] + 1) * (c[1] + 1), c[0] + c[1]))
    best = None
    for nl, nz in combos:
        npc = (nl + 1) * (nz + 1)
        if npc > P.max_pieces or (best and npc > best[0]):
            break
        offs = [lat.a, lat.a / 2, 0.0] if (P.stagger and nl > 0 and nz > 0) else [0.0]
        for so in offs:
            L = layout_2d(P, nl, nz, so, board, lat, holes, base_solid)
            if not L["pieces"] or not all(len(ps) == 1 for ps in L["pieces"]):
                continue
            fit = [fits_bed(P, ps[0]) for ps in L["pieces"]]
            if all(f[0] for f in fit):
                score = (len(L["pieces"]), -so, max(f[1][1] for f in fit))
                if best is None or score < best[1]:
                    best = (len(L["pieces"]), score, L | dict(n=(nl, nz), stagger=so))
                break
    if best is None:
        raise RuntimeError("Nessun sezionamento valido: il piatto e' troppo piccolo "
                           "rispetto alla tavola (aumenta il piatto o max_pieces).")
    return best[2]


def check(P):
    errs = []
    if P.length < 60 or P.width < 60:
        errs.append("La tavola deve essere almeno 60 x 60 mm.")
    if 2 * P.hole_top_r >= P.rib:
        errs.append("Il raccordo superiore dei fori deve essere minore di meta' nervatura.")
    if P.key_type not in ("bulb", "dovetail", "none"):
        errs.append("Tipo di incastro non valido.")
    if P.key_type != "none" and P.key_head <= P.key_neck:
        errs.append("La testa dell'incastro deve essere piu' larga del collo.")
    if P.cell <= P.rib * 3:
        errs.append("Il reticolo e' troppo fitto rispetto alla larghezza delle nervature.")
    zb_o = max(P.outer_bot_chamfer, .05) + P.outer_bot_r * np.sin(np.pi / 4)
    zb_h = max(P.hole_bot_chamfer, .05) + P.hole_bot_r * np.sin(np.pi / 4)
    tmin = min(P.t_edge, P.t_center)
    if tmin - P.outer_top_r <= zb_o or tmin - P.hole_top_r <= zb_h:
        errs.append("Spessore troppo basso per i raccordi scelti.")
    for k in ("bend_radius_long", "bend_radius_short"):
        v = getattr(P, k)
        if v is not None and abs(v) < 2 * max(P.t_center, P.t_edge):
            errs.append("Raggio di curvatura troppo piccolo.")
    if errs:
        raise ValueError(" ".join(errs))


def layout(P):
    """Solo 2D: sezionamento, incastri e contorni dei pezzi."""
    check(P)
    lat = Lattice(P.cell)
    board_w = board_outline(P)
    board = affinity.rotate(board_w, -P.lattice_angle, origin=(0, 0))
    holes = isogrid_holes(P, board, lat)
    base_solid = Polygon(board.exterior.coords, [h.exterior.coords for h in holes])
    L = choose_layout(P, board, lat, holes, base_solid)
    rot = lambda g: affinity.rotate(g, P.lattice_angle, origin=(0, 0))
    L["board"] = board_w
    L["solid"] = rot(L["solid"])
    L["cells"] = [rot(c) for c in L["cells"]]
    L["pieces"] = [[rot(q) for q in ps] for ps in L["pieces"]]
    L["seams"] = [rot(s) for s in L["seams"]]
    for j in L["joints"]:
        for k in ("key", "socket", "pad"):
            j[k] = rot(j[k])
        j["p"] = np.array(rot(Point(j["p"])).coords[0])
    return L


# =============================================================================
# MESH
# =============================================================================

class Mesh:
    def __init__(self, V, F):
        self.V = np.asarray(V, np.float64)
        self.F = np.asarray(F, np.int64)

    def copy(self):
        return Mesh(self.V.copy(), self.F.copy())

    @staticmethod
    def concat(ms):
        V, F, off = [], [], 0
        for m in ms:
            V.append(m.V); F.append(m.F + off); off += len(m.V)
        return Mesh(np.vstack(V), np.vstack(F))

    @property
    def volume(self):
        a, b, c = self.V[self.F[:, 0]], self.V[self.F[:, 1]], self.V[self.F[:, 2]]
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)

    @property
    def bounds(self):
        return self.V.min(axis=0), self.V.max(axis=0)

    def is_watertight(self):
        e = np.concatenate([self.F[:, [0, 1]], self.F[:, [1, 2]], self.F[:, [2, 0]]])
        key = e[:, 0] * (len(self.V) + 1) + e[:, 1]
        rkey = e[:, 1] * (len(self.V) + 1) + e[:, 0]
        u, c = np.unique(key, return_counts=True)
        if (c != 1).any():
            return False
        return bool(np.isin(rkey, u).all())

    def rotate_z(self, deg):
        a = np.radians(deg)
        R = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
        self.V = self.V @ R.T
        return self

    def translate(self, d):
        self.V = self.V + np.asarray(d, float)
        return self


def edge_profile(r_top, r_bot, chamfer, n):
    chamfer = max(chamfer, 0.05)
    r_top, r_bot = max(r_top, 0.05), max(r_bot, 0.05)
    c45, s45 = np.cos(np.pi / 4), np.sin(np.pi / 4)
    prof = [(r_bot * (1 - c45) + chamfer, True, 0.0)]
    zb = chamfer + r_bot * s45
    for th in np.linspace(np.pi / 4, 0, n + 1):
        prof.append((r_bot * (1 - np.cos(th)), True, zb - r_bot * np.sin(th)))
    for ph in np.linspace(0, np.pi / 2, n + 1):
        prof.append((r_top * (1 - np.cos(ph)), False, r_top * (1 - np.sin(ph))))
    return (np.array([q[0] for q in prof]), np.array([q[1] for q in prof]),
            np.array([q[2] for q in prof]))


def ring_points(coords, step):
    ls = shapely.segmentize(LinearRing(coords), step)
    p = np.array(ls.coords)[:-1]
    d = np.linalg.norm(np.diff(p, axis=0), axis=1)
    keep = np.concatenate([[True], d > 0.05])
    p = p[keep]
    if len(p) > 3 and np.linalg.norm(p[-1] - p[0]) <= 0.05:
        p = p[:-1]
    return p


def bend(P, V):
    V = V.copy()
    if P.bend_radius_long:
        R = P.bend_radius_long; th = V[:, 0] / R; r = R - V[:, 2]
        V[:, 0], V[:, 2] = r * np.sin(th), R - r * np.cos(th)
    if P.bend_radius_short:
        R = P.bend_radius_short; th = V[:, 1] / R; r = R - V[:, 2]
        V[:, 1], V[:, 2] = r * np.sin(th), R - r * np.cos(th)
    return V


def _near(geom, pts, tol=0.02):
    if geom is None:
        return np.zeros(len(pts), bool)
    return shapely.dwithin(geom, pts, tol)


def piece_mesh(P, poly, ctx):
    poly = orient(poly, 1.0)
    prof_o = edge_profile(P.outer_top_r, P.outer_bot_r, P.outer_bot_chamfer, P.arc_steps)
    prof_h = edge_profile(P.hole_top_r, P.hole_bot_r, P.hole_bot_chamfer, P.arc_steps)
    K = len(prof_o[0])
    bb = box(*poly.bounds).buffer(2)
    orig = ctx["orig"].intersection(bb)
    ext = ctx["ext"].intersection(bb)
    for g in (orig, ext, ctx.get("male_keys"), ctx.get("sockets")):
        if g is not None and not g.is_empty:
            shapely.prepare(g)
    V, F, bottoms, tops = [], [], [], []
    nv, ring_len = 0, 0.0
    for rc in [poly.exterior.coords] + [r.coords for r in poly.interiors]:
        b = ring_points(rc, P.ring_step)
        n = len(b)
        if n < 3:
            continue
        pts = shapely.points(b)
        cut = ~_near(orig, pts) if not orig.is_empty else np.ones(n, bool)
        outer = (~cut) & (_near(ext, pts) if not ext.is_empty else np.zeros(n, bool))
        seg = np.linalg.norm(np.roll(b, -1, 0) - b, axis=1)
        s = np.concatenate([[0], np.cumsum(seg)[:-1]])
        Ltot = seg.sum()
        ring_len += Ltot
        if cut.all():
            w = np.zeros(n)
        elif cut.any():
            cs = np.sort(s[cut])
            pos = np.searchsorted(cs, s)
            lo = cs[np.clip(pos - 1, 0, len(cs) - 1)]
            hi = cs[np.clip(pos, 0, len(cs) - 1)]
            d1 = np.minimum(np.abs(s - lo), Ltot - np.abs(s - lo))
            d2 = np.minimum(np.abs(s - hi), Ltot - np.abs(s - hi))
            d3 = np.minimum(np.abs(s - cs[0]), Ltot - np.abs(s - cs[0]))
            d4 = np.minimum(np.abs(s - cs[-1]), Ltot - np.abs(s - cs[-1]))
            dd = np.minimum.reduce([d1, d2, d3, d4])
            w = np.clip(dd / P.blend, 0, 1)
            w = w * w * (3 - 2 * w)
        else:
            w = np.ones(n)
        sgn = np.zeros(n)
        if P.key_taper > 0:
            if ctx.get("male_keys") is not None:
                sgn[cut & _near(ctx["male_keys"], pts)] = 1.0
            if ctx.get("sockets") is not None:
                sgn[cut & _near(ctx["sockets"], pts)] = -1.0
            nz = sgn != 0
            if nz.any() and ctx.get("cell") is not None:
                sgn[nz] *= np.clip(shapely.distance(ctx["cell"], pts[nz]) / 1.0, 0, 1)
        t = np.roll(b, -1, 0) - np.roll(b, 1, 0)
        t /= np.maximum(np.linalg.norm(t, axis=1)[:, None], 1e-12)
        nrm = np.column_stack([t[:, 1], -t[:, 0]])
        tl = thickness(P, b[:, 0], b[:, 1])
        for k in range(K):
            ins = np.where(outer, prof_o[0][k], prof_h[0][k]) * w
            isb = np.where(outer, prof_o[1][k], prof_h[1][k])
            val = np.where(outer, prof_o[2][k], prof_h[2][k])
            z = np.where(isb, val, tl - val)
            ins = ins + sgn * P.key_taper * (1 - z / tl)
            V.append(np.column_stack([b - nrm * ins[:, None], z]))
        idx = nv + np.arange(K * n).reshape(K, n)
        a0, b0 = idx[:-1], np.roll(idx[:-1], -1, axis=1)
        c0, d0 = np.roll(idx[1:], -1, axis=1), idx[1:]
        F.append(np.stack([a0, b0, c0], -1).reshape(-1, 3))
        F.append(np.stack([a0, c0, d0], -1).reshape(-1, 3))
        bottoms.append(idx[0]); tops.append(idx[-1])
        nv += K * n
    V = np.vstack(V)
    crown = abs(P.t_center - P.t_edge) > 1e-9
    bent = bool(P.bend_radius_long) or bool(P.bend_radius_short)

    def cap(loops, zf, up, need_refine):
        nonlocal V
        pts2 = [V[lp, :2] for lp in loops]
        T = triangulate_loops(pts2)
        V2 = np.vstack(pts2)
        n0 = len(V2)
        if need_refine:
            V2, T = refine(V2, T, P.refine_len)
        if not up:                       # triangolatore -> antiorario; sotto va invertito
            T = T[:, [0, 2, 1]]
        new = V2[n0:]
        gmap = np.concatenate([np.concatenate(loops), len(V) + np.arange(len(new))])
        if len(new):
            V = np.vstack([V, np.column_stack([new, zf(new[:, 0], new[:, 1])])])
        return gmap[T]

    F.append(cap(bottoms, lambda x, y: np.zeros_like(x), False, bent))
    F.append(cap(tops, lambda x, y: thickness(P, x, y), True, crown or bent))
    F = np.vstack(F)
    return Mesh(bend(P, V), F), ring_len


# =============================================================================
# COSTRUZIONE DI UNA VARIANTE
# =============================================================================

VARIANTS = {
    "whole": "Tavola intera",
    "split_keys": "Sezionata con incastri",
    "split_plain": "Sezionata senza incastri",
}


def variant_params(P, variant):
    d = asdict(P)
    if variant == "whole":
        d["split"] = False
    elif variant == "split_plain":
        d["split"] = True
        d["key_type"] = "none"
    else:
        d["split"] = True
    return Params.from_dict(d)


def build(P, progress=None):
    """Costruisce i pezzi di una configurazione. Restituisce (L, pezzi, report)."""
    L = layout(P)
    many = len(L["pieces"]) > 1
    orig, ext = L["solid"].boundary, L["board"].exterior
    tw = P.est_perimeters * P.est_line_width
    pieces = []
    for i, parts in enumerate(L["pieces"]):
        if progress:
            progress(i, len(L["pieces"]))
        poly = unary_union(parts)
        mk = [j["key"].exterior for j in L["joints"] if j["male"] == i]
        sk = [j["socket"].exterior for j in L["joints"] if j["female"] == i]
        ctx = dict(orig=orig, ext=ext, cell=L["cells"][i].boundary,
                   male_keys=MultiLineString(mk) if mk else None,
                   sockets=MultiLineString(sk) if sk else None)
        ms, rl = [], 0.0
        for q in parts:
            m, r = piece_mesh(P, q, ctx)
            ms.append(m); rl += r
        mesh = Mesh.concat(ms) if len(ms) > 1 else ms[0]
        vol = mesh.volume
        tm = float(np.mean(thickness(P, *np.array(poly.exterior.coords).T)))
        pv = min(rl * tm * tw, vol)
        mass = P.est_density / 1000 * (pv + P.est_infill * (vol - pv))
        ok, dims, ang = fits_bed(P, poly)
        lab = string.ascii_uppercase[i] if many else "tavola"
        pieces.append(dict(
            label=lab, mesh=mesh, angle=ang,
            info=dict(label=lab, size=[round(dims[1], 1), round(dims[0], 1)], fits_bed=bool(ok),
                      mass_g=round(mass, 1), volume_cm3=round(vol / 1000, 1),
                      keys=int(sum(j["male"] == i for j in L["joints"])),
                      sockets=int(sum(j["female"] == i for j in L["joints"])))))
    zmin = min(p["mesh"].bounds[0][2] for p in pieces)
    for p in pieces:
        p["mesh"].translate([0, 0, -zmin])
    order = sorted(range(len(pieces)), key=lambda i: (-round(L["cells"][i].area), i))
    rep = dict(
        pieces=[p["info"] for p in pieces],
        n_pieces=len(pieces), joints=len(L["joints"]),
        key_type=P.key_type if L["joints"] else "none",
        seams=dict(lines=L["n"][0], zigzags=L["n"][1]),
        mass_total_g=round(sum(p["info"]["mass_g"] for p in pieces), 1),
        open_area_pct=round(100 * (1 - L["solid"].area / L["board"].area), 1),
        assembly_order=[pieces[i]["label"] for i in order],
        bed=[P.bed_x, P.bed_y], board=[P.length, P.width, max(P.t_center, P.t_edge)])
    return L, pieces, rep


def bed_oriented(piece):
    m = piece["mesh"].copy().rotate_z(-piece["angle"])
    lo, hi = m.bounds
    return m.translate([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])


# =============================================================================
# ESPORTAZIONE
# =============================================================================

def _normals(m):
    a, b, c = m.V[m.F[:, 0]], m.V[m.F[:, 1]], m.V[m.F[:, 2]]
    n = np.cross(b - a, c - a)
    return n / np.maximum(np.linalg.norm(n, axis=1)[:, None], 1e-12)


def stl_binary(m, name="tavola"):
    dt = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec = np.zeros(len(m.F), dtype=dt)
    rec["n"] = _normals(m)
    rec["v"] = m.V[m.F]
    head = (f"{name} - tavola zaino v{VERSION}").encode()[:80].ljust(80, b" ")
    return head + struct.pack("<I", len(m.F)) + rec.tobytes()


def _fmt(row_fmt, arr):
    """Formatta un array riga per riga con una sola operazione (molto piu' veloce
    di un ciclo Python, importante nel browser)."""
    arr = np.asarray(arr)
    if len(arr) == 0:
        return ""
    return (row_fmt * len(arr)) % tuple(arr.ravel().tolist())


def stl_ascii(m, name="tavola"):
    n = _normals(m)
    tri = m.V[m.F].reshape(len(m.F), 9)
    body = _fmt("facet normal %.6e %.6e %.6e\n outer loop\n  vertex %.5f %.5f %.5f\n"
                "  vertex %.5f %.5f %.5f\n  vertex %.5f %.5f %.5f\n endloop\nendfacet\n",
                np.hstack([n, tri]))
    return (f"solid {name}\n" + body + f"endsolid {name}\n").encode()


def obj_text(objs):
    """objs: lista di (nome, Mesh)."""
    out, off = [f"# tavola zaino v{VERSION}\n"], 1
    for name, m in objs:
        out.append(f"o {name}\n")
        out.append(_fmt("v %.5f %.5f %.5f\n", m.V))
        out.append(_fmt("f %d %d %d\n", m.F + off))
        off += len(m.V)
    return "".join(out).encode()


def threemf(objs):
    """3MF con un oggetto per pezzo (ogni pezzo e' un oggetto separato nello slicer)."""
    res, items = [], []
    for k, (name, m) in enumerate(objs, start=1):
        vs = _fmt('<vertex x="%.4f" y="%.4f" z="%.4f"/>', m.V)
        ts = _fmt('<triangle v1="%d" v2="%d" v3="%d"/>', m.F)
        res.append(f'<object id="{k}" name="{name}" type="model"><mesh><vertices>{vs}'
                   f'</vertices><triangles>{ts}</triangles></mesh></object>')
        items.append(f'<item objectid="{k}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="it-IT" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<metadata name="Application">Tavola zaino v{VERSION}</metadata>'
             f'<resources>{"".join(res)}</resources><build>{"".join(items)}</build></model>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/'
          'package/2006/content-types"><Default Extension="rels" ContentType="application/'
          'vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" '
          'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" '
            'Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
    return buf.getvalue()


def _arrange(meshes, gap=10.0):
    """Dispone i pezzi in riga (per i file multi-oggetto da piatto)."""
    out, x = [], 0.0
    for m in meshes:
        lo, hi = m.bounds
        out.append(m.copy().translate([x - lo[0], -(lo[1] + hi[1]) / 2, 0]))
        x += (hi[0] - lo[0]) + gap
    return out


def readme_text(P, reports):
    L = [f"TAVOLA PER ZAINO - file generati (v{VERSION})", ""]
    L.append(f"Tavola: {P.length:.0f} x {P.width:.0f} mm, spessore {P.t_center:g}/{P.t_edge:g} mm "
             f"(centro/bordi), reticolo {P.cell:g} mm, nervature {P.rib:g} mm.")
    L.append(f"Piatto: {P.bed_x:g} x {P.bed_y:g} mm (margine {P.bed_margin:g} mm).")
    L.append("")
    for v, rep in reports.items():
        L.append(f"== {VARIANTS[v]} ==")
        L.append(f"  Pezzi: {rep['n_pieces']}  incastri: {rep['joints']}  "
                 f"peso stimato: ~{rep['mass_total_g']:.0f} g")
        for p in rep["pieces"]:
            L.append(f"  {p['label']}: {p['size'][0]:.0f} x {p['size'][1]:.0f} mm, ~{p['mass_g']:.0f} g"
                     + ("" if p["fits_bed"] else "  (NON entra nel piatto!)"))
        if rep["n_pieces"] > 1:
            L.append("  Ordine di montaggio: " + " -> ".join(rep["assembly_order"]))
        L.append("")
    L += ["IMPOSTAZIONI DI STAMPA CONSIGLIATE",
          "  - Layer superiori e inferiori: 0 (solo perimetri + infill).",
          f"  - Perimetri: {P.est_perimeters} (3 se serve piu' rigidita').",
          f"  - Infill gyroid {P.est_infill:.0%} (5-10% e' il miglior rapporto peso/rigidita').",
          "  - I pezzi sono gia' orientati e centrati per il piatto (cartella 'piatto').",
          "",
          "MONTAGGIO",
          "  - Incolla con epossidica bicomponente sulle facce di taglio e nelle sedi.",
          "  - Appoggia i pezzi su un piano dritto; monta nell'ordine indicato calando",
          "    ogni pezzo dall'alto: le chiavi rastremate fanno da battuta.",
          "  - Prima di stampare tutto, prova un incastro: il gioco si regola con key_clearance."]
    return "\n".join(L) + "\n"


def export_bundle(params, opts, progress=None):
    """Crea uno ZIP con le varianti e i formati richiesti.
    opts: variants{whole,split_keys,split_plain}, formats{stl,stl_ascii,obj,3mf},
          bed_layout, assembled, combined, include_json, quality."""
    P0 = Params.from_dict(params)
    q = opts.get("quality", "standard")
    P0 = with_quality(P0, q)
    vsel = [v for v in ("whole", "split_keys", "split_plain") if opts.get("variants", {}).get(v)]
    fsel = [f for f in ("stl", "stl_ascii", "obj", "3mf") if opts.get("formats", {}).get(f)]
    if not vsel:
        raise ValueError("Scegli almeno una variante da esportare.")
    if not fsel:
        raise ValueError("Scegli almeno un formato.")
    bed_layout = opts.get("bed_layout", True)
    assembled = opts.get("assembled", True)
    combined = opts.get("combined", True)
    buf = io.BytesIO()
    reports = {}
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1)
    nsteps = len(vsel)

    def write(path, objs):
        """objs: lista (nome, Mesh). Scrive in tutti i formati scelti."""
        single = objs[0][1] if len(objs) == 1 else Mesh.concat([m for _, m in objs])
        for f in fsel:
            if f == "stl":
                zf.writestr(path + ".stl", stl_binary(single, objs[0][0]))
            elif f == "stl_ascii":
                zf.writestr(path + "_ascii.stl", stl_ascii(single, objs[0][0]))
            elif f == "obj":
                zf.writestr(path + ".obj", obj_text(objs))
            elif f == "3mf":
                zf.writestr(path + ".3mf", threemf(objs))

    for vi, v in enumerate(vsel):
        P = variant_params(P0, v)

        def prog(i, n, vi=vi, v=v):
            if progress:
                progress((vi + (i / max(n, 1)) * 0.9) / nsteps,
                         f"{VARIANTS[v]}: pezzo {i + 1} di {n}")
        L, pieces, rep = build(P, prog)
        reports[v] = rep
        folder = {"whole": "tavola_intera", "split_keys": "sezionata_con_incastri",
                  "split_plain": "sezionata_senza_incastri"}[v]
        if progress:
            progress((vi + 0.92) / nsteps, f"{VARIANTS[v]}: scrittura file")
        if len(pieces) == 1:
            m = bed_oriented(pieces[0]) if bed_layout else pieces[0]["mesh"]
            write(f"{folder}/tavola", [("tavola", m)])
        else:
            if bed_layout:
                bm = [bed_oriented(p) for p in pieces]
                for p, m in zip(pieces, bm):
                    write(f"{folder}/piatto/parte_{p['label']}", [(f"parte_{p['label']}", m)])
                if combined:
                    write(f"{folder}/piatto/tutti_i_pezzi",
                          [(f"parte_{p['label']}", m) for p, m in zip(pieces, _arrange(bm))])
            if assembled or not bed_layout:
                if not bed_layout:
                    for p in pieces:
                        write(f"{folder}/assemblata/parte_{p['label']}",
                              [(f"parte_{p['label']}", p["mesh"])])
                write(f"{folder}/assemblata/tavola_assemblata",
                      [(f"parte_{p['label']}", p["mesh"]) for p in pieces])
    if opts.get("include_json", True):
        zf.writestr("parametri.json", json.dumps(asdict(Params.from_dict(params)), indent=1))
        zf.writestr("report.json", json.dumps(reports, indent=1))
        zf.writestr("LEGGIMI.txt", readme_text(P0, reports))
    zf.close()
    if progress:
        progress(1.0, "Pronto")
    return buf.getvalue(), reports


# =============================================================================
# API PER IL SITO (JSON in / JSON + buffer out)
# =============================================================================

def api_defaults():
    return json.dumps(dict(params=asdict(Params()), version=VERSION, triangulator=_TRI))


def api_preview(params_json, view="split"):
    """Anteprima veloce. Restituisce (json_info, [ (pos_f32_bytes, idx_u32_bytes) ... ])."""
    t0 = time.time()
    P = Params.from_dict(json.loads(params_json))
    P = with_quality(P, "preview")
    P = variant_params(P, "whole" if view == "whole" else
                       ("split_keys" if P.key_type != "none" else "split_plain"))
    L, pieces, rep = build(P)
    bufs = []
    for p in pieces:
        m = p["mesh"]
        bufs.append((m.V.astype(np.float32).tobytes(), m.F.astype(np.uint32).tobytes()))
    joints = [dict(x=float(j["p"][0]), y=float(j["p"][1]), male=int(j["male"]),
                   female=int(j["female"]), kind=j["kind"]) for j in L["joints"]]
    centers = [list(np.mean(p["mesh"].V, axis=0).round(2)) for p in pieces]
    info = dict(report=rep, joints=joints, centers=centers, ms=round((time.time() - t0) * 1000),
                fits_whole=bool(fits_bed(P, L["board"])[0]))
    return json.dumps(info), bufs


def api_export(params_json, opts_json, progress=None):
    data, reports = export_bundle(json.loads(params_json), json.loads(opts_json), progress)
    return data, json.dumps(reports)


# =============================================================================
# RIGA DI COMANDO
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description="Tavola strutturale per zaino")
    ap.add_argument("--params", help="file JSON con i parametri")
    ap.add_argument("--dump-params", help="salva i parametri di default in JSON ed esce")
    ap.add_argument("--out", default="tavola_zaino.zip", help="file ZIP di output")
    ap.add_argument("--variants", default="whole,split_keys",
                    help="whole,split_keys,split_plain")
    ap.add_argument("--formats", default="stl", help="stl,stl_ascii,obj,3mf")
    ap.add_argument("--quality", default="standard", choices=list(QUALITY))
    a = ap.parse_args()
    if a.dump_params:
        with open(a.dump_params, "w") as f:
            json.dump(asdict(Params()), f, indent=1)
        return
    params = {}
    if a.params:
        with open(a.params) as f:
            params = json.load(f)
    opts = dict(variants={v: True for v in a.variants.split(",")},
                formats={f: True for f in a.formats.split(",")},
                quality=a.quality, bed_layout=True, assembled=True, combined=True,
                include_json=True)
    data, reports = export_bundle(
        params, opts, lambda f, msg: print(f"[{f * 100:5.1f}%] {msg}", flush=True))
    with open(a.out, "wb") as f:
        f.write(data)
    print(f"Scritto {a.out} ({len(data) / 1e6:.1f} MB)  triangolatore: {_TRI}")
    for v, rep in reports.items():
        print(f"  {VARIANTS[v]}: {rep['n_pieces']} pezzi, {rep['joints']} incastri, "
              f"~{rep['mass_total_g']:.0f} g")


if __name__ == "__main__":
    main()
