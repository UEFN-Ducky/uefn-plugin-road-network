"""UEFN export of an engine world: 8-bit material weightmaps beside the heightmap, and debug PNGs of any engine map.
Both read citygen's terrain dict (cm): heights rows plus `maps` (flat j*n+i or rows, at their own n, which can
differ from the heights grid after a refine)."""
import math
import struct
import zlib
from array import array

from .climate import BIOMES, LAYERS, material_masks
from .grid import Grid

# a debug view of the biome map: one RGB per climate.BIOMES id (backend only: the panel themes its own)
BIOME_RGB = [(40, 80, 150), (220, 205, 150), (70, 120, 110), (150, 180, 90), (130, 200, 100), (60, 130, 60),
             (40, 95, 60), (30, 140, 80), (190, 175, 90), (230, 200, 130), (150, 150, 90), (130, 120, 110),
             (150, 180, 130), (170, 175, 170), (245, 245, 250), (95, 90, 85)]


def _png(rows: list, w: int, h: int, rgb: bool) -> bytes:
    """8-bit PNG (greyscale or RGB) of `h` rows of bytes, row 0 first."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2 if rgb else 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _flat(m) -> tuple:
    """(flat list, n) of a map given flat or as rows."""
    flat = [v for r in m for v in r] if m and isinstance(m[0], (list, tuple)) else list(m)
    return flat, math.isqrt(len(flat))


def _pick(n: int, res: int) -> list:
    """For each of res pixels from edge to edge: the map cell (0..n-1) it lies in. ponytail: nearest, not
    bilinear: a 2017 px mask from a 160 grid is blocky at 12 px; smooth it here if the seams show in UEFN."""
    return [min(n - 1, int(p * n / res)) for p in range(res)]


def _heights_m(ter: dict, n: int) -> array:
    """The terrain's heights (m) at an n x n map's cells (nearest), whatever grid the heights are on."""
    H, g = ter["heights"], len(ter["heights"])
    pick = [min(g - 1, int((k + 0.5) * g / n)) for k in range(n)]
    return array("d", ((H[pick[j]][pick[i]] or 0.0) / 100.0 for j in range(n) for i in range(n)))


def material_masks_u8(ter: dict) -> tuple:
    """({layer: bytearray n*n}, n): grass/rock/sand/snow/dirt weights 0..255 that sum to exactly 255 per cell
    (the largest weight takes the rounding). Empty without an engine biome map (a Classic world)."""
    if not (ter.get("maps") or {}).get("biome"):
        return {}, 0
    biome, n = _flat(ter["maps"]["biome"])
    size = (ter["max"][0] - ter["min"][0]) / 100.0
    slope = Grid(n, 0.0, 0.0, size).slope(_heights_m(ter, n))
    w = material_masks({"biome": biome, "slope": slope})
    out = {name: bytearray(n * n) for name in LAYERS}
    cols = [w[name] for name in LAYERS]
    dst = [out[name] for name in LAYERS]
    for k in range(n * n):
        q = [int(c[k] * 255.0) for c in cols]
        top = max(range(len(q)), key=lambda a: cols[a][k])
        q[top] += 255 - sum(q)
        for d, v in zip(dst, q):
            d[k] = v
    return out, n


def material_pngs(ter: dict, res: int) -> dict:
    """{layer: 8-bit greyscale PNG, res x res}, the same pixels as the heightmap (row 0 = the map's min y), for
    Landscape > Import's layers. Empty for a world with no biome map."""
    masks, n = material_masks_u8(ter)
    if not masks:
        return {}
    cols, rows = _pick(n, res), _pick(n, res)
    out = {}
    for name, m in masks.items():
        wide = [bytes(m[j * n + c] for c in cols) for j in range(n)]  # n distinct rows, reused down the image
        out[name] = _png([wide[j] for j in rows], res, res, False)
    return out


def debug_map(ter: dict, name: str, res: int = 256) -> bytes:
    """An RGB PNG (res x res, row 0 = j 0) of one engine map: biome in its colours, any other map as grey from its
    lowest to its highest finite value. 'height' falls back to the heights rows. KeyError if the world lacks it."""
    m = (ter.get("maps") or {}).get(name)
    if m is None and name == "height":
        m = [[(v or 0.0) / 100.0 for v in r] for r in ter["heights"]]
    if m is None:
        raise KeyError(name)
    flat, n = _flat(m)
    if name == "biome":
        lut = [BIOME_RGB[min(len(BIOME_RGB) - 1, max(0, int(v)))] for v in flat]
    else:
        fin = [v for v in flat if math.isfinite(v)] or [0.0]
        lo, span = min(fin), (max(fin) - min(fin)) or 1.0
        lut = [(g, g, g) for g in (round(255 * (min(max(v, lo), lo + span) - lo) / span) if math.isfinite(v) else 255
                                   for v in flat)]
    pick = _pick(n, res)
    wide = [bytes(c for i in pick for c in lut[j * n + i]) for j in range(n)]
    return _png([wide[j] for j in pick], res, res, True)


assert len(BIOME_RGB) == len(BIOMES)
