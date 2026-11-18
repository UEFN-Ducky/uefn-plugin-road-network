// Where a view ray meets the terrain, walked over the heightfield instead of testing every triangle of the
// terrain mesh (that brute-force raycast ran on every hover, 16 a second, over hundreds of thousands of triangles).
// o, d: the ray in three.js metres (x = UE y / 100, y = UE z * zx / 100, z = -UE x / 100).
// t: {grid, min, max, heights[j][i]} in UE cm, a vertex at each cell centre (terrainMesh). Returns s or null.
export function marchHeightfield(o, d, t, zx = 1) {
  const n = t?.grid, H = t?.heights;
  if (!n || !H?.length) return null;
  const cell = (j, i) => H[Math.max(0, Math.min(n - 1, j))]?.[Math.max(0, Math.min(n - 1, i))];
  const zAt = (x, y) => {  // bilinear between the cell-centre vertices
    const fx = (x - t.min[0]) / (t.max[0] - t.min[0]) * n - 0.5, fy = (y - t.min[1]) / (t.max[1] - t.min[1]) * n - 0.5;
    const i = Math.floor(fx), j = Math.floor(fy), u = fx - i, v = fy - j;
    const a = cell(j, i), b = cell(j, i + 1), c = cell(j + 1, i), e = cell(j + 1, i + 1);
    if ([a, b, c, e].some((h) => h === null || h === undefined)) return null;
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + e * u) * v;
  };
  const above = (s) => {  // metres over the ground at s along the ray (null: no ground there)
    const z = zAt(-(o[2] + d[2] * s) * 100, (o[0] + d[0] * s) * 100);
    return z === null ? null : o[1] + d[1] * s - z * zx / 100;
  };
  let s0 = 0, h0 = above(0) ?? 1;
  for (let s = 1, k = 0; k < 4000 && s < 400000; k++, s += Math.max(1, s * 0.01)) {
    const h = above(s);
    if (h === null) { s0 = s; h0 = 1; continue; }
    if (h <= 0 && h0 > 0) {  // crossed between s0 and s: halve the gap down to centimetres
      let a = s0, b = s;
      for (let m = 0; m < 16; m++) { const mid = (a + b) / 2; if ((above(mid) ?? 1) > 0) a = mid; else b = mid; }
      return b;
    }
    s0 = s; h0 = h;
  }
  return null;
}
