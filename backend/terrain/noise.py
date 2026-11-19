"""Seeded 2D gradient noise. Inputs are world coordinates divided by the caller's feature size (or `scale`),
never grid indices, so one seed gives the same ground on any grid size or tile."""
import math
import random

_GX = (1.0, -1.0, 0.0, 0.0, 0.7071067811865476, -0.7071067811865476, 0.7071067811865476, -0.7071067811865476)
_GY = (0.0, 0.0, 1.0, -1.0, 0.7071067811865476, 0.7071067811865476, -0.7071067811865476, -0.7071067811865476)


class Noise:
    def __init__(self, seed: int):
        p = list(range(256))
        random.Random(seed).shuffle(p)
        self.seed = seed
        self.p = p + p  # 512 entries: no wrap needed on p[X + 1] + Y + 1

    def value(self, x: float, y: float) -> float:
        """Gradient (Perlin) noise, about -0.7..0.7, 0 on integer lattice points."""
        p = self.p
        xf, yf = math.floor(x), math.floor(y)
        X, Y = int(xf) & 255, int(yf) & 255
        x -= xf
        y -= yf
        u = x * x * x * (x * (x * 6 - 15) + 10)
        v = y * y * y * (y * (y * 6 - 15) + 10)
        a, b = p[X] + Y, p[X + 1] + Y
        h = p[a] & 7
        n00 = _GX[h] * x + _GY[h] * y
        h = p[b] & 7
        n10 = _GX[h] * (x - 1) + _GY[h] * y
        h = p[a + 1] & 7
        n01 = _GX[h] * x + _GY[h] * (y - 1)
        h = p[b + 1] & 7
        n11 = _GX[h] * (x - 1) + _GY[h] * (y - 1)
        nx0 = n00 + (n10 - n00) * u
        nx1 = n01 + (n11 - n01) * u
        return nx0 + (nx1 - nx0) * v

    def fbm(self, x: float, y: float, oct: int = 5, lac: float = 2.0, gain: float = 0.5, scale: float = 1.0) -> float:
        """Octaves of value(), normalised by their amplitudes: always within -1..1."""
        x, y = x / scale, y / scale
        amp, tot, norm = 1.0, 0.0, 0.0
        for o in range(int(oct)):
            # each octave shifted so the lattice zeros never line up
            tot += amp * self.value(x + o * 17.31, y - o * 9.73)
            norm += amp
            amp *= gain
            x *= lac
            y *= lac
        return tot / norm if norm else 0.0

    def ridged(self, x: float, y: float, oct: int = 5, lac: float = 2.0, gain: float = 0.5, scale: float = 1.0) -> float:
        """Ridged multifractal 0..1: (1 - |n|)^2 per octave, each weighted by the octave before (sharp crests)."""
        x, y = x / scale, y / scale
        amp, tot, norm, w = 1.0, 0.0, 0.0, 1.0
        for o in range(int(oct)):
            s = 1.0 - abs(self.value(x + o * 17.31, y - o * 9.73)) * 1.4142135623730951
            s = max(s, 0.0) ** 2 * w
            w = min(max(s * 2.0, 0.0), 1.0)
            tot += s * amp
            norm += amp
            amp *= gain
            x *= lac
            y *= lac
        return tot / norm if norm else 0.0

    def warp(self, x: float, y: float, amt: float, scale: float, oct: int = 3) -> tuple:
        """(x, y) moved up to ~amt metres by two fbm offsets at `scale` metres: bays, headlands, bent ranges."""
        u, v = x / scale, y / scale
        return (x + amt * self.fbm(u + 5.2, v + 1.3, oct),
                y + amt * self.fbm(u - 8.3, v + 2.8, oct))
