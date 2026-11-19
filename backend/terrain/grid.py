"""A square world grid in metres: n x n cells, flat row-major array('d') maps (idx = j*n + i)."""
import math
from array import array


class Grid:
    """n cells a side over size_m metres from the (x0, y0) corner. xs / ys are the cell centres."""

    def __init__(self, n: int, x0: float, y0: float, size_m: float):
        self.n = int(n)
        self.x0, self.y0, self.size = float(x0), float(y0), float(size_m)
        self.cell = self.size / self.n
        self.xs = [self.x0 + self.cell * (i + 0.5) for i in range(self.n)]
        self.ys = [self.y0 + self.cell * (j + 0.5) for j in range(self.n)]

    def new(self, fill: float = 0.0) -> array:
        return array('d', [fill]) * (self.n * self.n)

    def idx(self, i: int, j: int) -> int:
        return j * self.n + i

    def points(self):
        """(k, x, y) for every cell, row by row."""
        k = 0
        for y in self.ys:
            for x in self.xs:
                yield k, x, y
                k += 1

    def sample(self, arr, x: float, y: float) -> float:
        """Bilinear between cell centres, clamped at the edge."""
        n = self.n
        fx = min(max((x - self.x0) / self.cell - 0.5, 0.0), n - 1.0)
        fy = min(max((y - self.y0) / self.cell - 0.5, 0.0), n - 1.0)
        i0, j0 = int(fx), int(fy)
        i1, j1 = min(i0 + 1, n - 1), min(j0 + 1, n - 1)
        tx, ty = fx - i0, fy - j0
        a = arr[j0 * n + i0] + (arr[j0 * n + i1] - arr[j0 * n + i0]) * tx
        b = arr[j1 * n + i0] + (arr[j1 * n + i1] - arr[j1 * n + i0]) * tx
        return a + (b - a) * ty

    def blur(self, arr, r: int) -> array:
        """Box blur of radius r cells, separable, run twice (close to a gaussian). Edges clamp."""
        r = int(r)
        out = array('d', arr)
        if r <= 0:
            return out
        n, w = self.n, 2 * r + 1
        for _ in range(2):
            for axis in (0, 1):
                for line in range(n):
                    sl = slice(line * n, line * n + n) if axis == 0 else slice(line, n * n, n)
                    row = out[sl]
                    pad = [row[0]] * r + list(row) + [row[-1]] * r
                    acc, sums = 0.0, [0.0]
                    for v in pad:
                        acc += v
                        sums.append(acc)
                    out[sl] = array('d', ((sums[i + w] - sums[i]) / w for i in range(n)))
        return out

    def slope(self, arr) -> array:
        """|gradient| as rise / run (0.05 = 5 %), central differences, one-sided at the edge."""
        n, c = self.n, self.cell
        out = self.new()
        for j in range(n):
            jm, jp = max(j - 1, 0), min(j + 1, n - 1)
            for i in range(n):
                im, ip = max(i - 1, 0), min(i + 1, n - 1)
                dx = (arr[j * n + ip] - arr[j * n + im]) / ((ip - im) * c)
                dy = (arr[jp * n + i] - arr[jm * n + i]) / ((jp - jm) * c)
                out[j * n + i] = math.sqrt(dx * dx + dy * dy)
        return out

    def dist_to(self, mask, on: float = 0.5) -> array:
        """Metres from each cell to the nearest cell with mask >= on. Two-pass chamfer sweep that carries each
        cell's nearest source and measures the true distance to it (a plain 1 / sqrt 2 chamfer is up to 8 % long).
        No such cell: every distance is inf."""
        n = self.n
        inf = float('inf')
        src = [k if v >= on else -1 for k, v in enumerate(mask)]
        d2 = [0.0 if s >= 0 else inf for s in src]
        fwd = ((-1, 0), (0, -1), (-1, -1), (1, -1))
        for passes in (fwd, tuple((-a, -b) for a, b in fwd)):
            rng = range(n) if passes is fwd else range(n - 1, -1, -1)
            for j in rng:
                for i in (range(n) if passes is fwd else range(n - 1, -1, -1)):
                    k = j * n + i
                    best, bs = d2[k], src[k]
                    for di, dj in passes:
                        ii, jj = i + di, j + dj
                        if 0 <= ii < n and 0 <= jj < n:
                            s = src[jj * n + ii]
                            if s >= 0 and s != bs:
                                ex, ey = s % n - i, s // n - j
                                e = ex * ex + ey * ey
                                if e < best:
                                    best, bs = e, s
                    d2[k], src[k] = best, bs
        c = self.cell
        return array('d', (math.sqrt(v) * c if v != inf else inf for v in d2))


def smoothstep(e0: float, e1: float, x: float) -> float:
    if e1 == e0:
        return 1.0 if x >= e1 else 0.0
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def quantile(values, q: float) -> float:
    """The q-th share (0..1) of the sorted values (nearest rank)."""
    s = sorted(values)
    if not s:
        return 0.0
    return s[min(len(s) - 1, max(0, int(q * len(s))))]
