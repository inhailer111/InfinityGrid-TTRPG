"""Procedural Washington-style state generator for the battle map (pure Python, no Qt).

Coordinates are in feet: x grows east, y grows south, (0, 0) is the middle of the state.
Everything is a pure function of the seed, so the same seed always rebuilds the same world,
and tiles beyond the edge of the state are generated on demand in exactly the same way.
"""
import heapq
import math
import random

MILE = 5280.0
TILE_FT = 12 * MILE      # settlements are generated tile by tile, so the world never runs out
CHUNK_FT = 800           # street-level detail is generated in chunks of this size
CELL_FT = 2 * MILE       # resolution of the stored elevation grid
ROUTE_FT = 6 * MILE      # resolution of the highway router
SEA = 0.20

BIOMES = ["Water", "Sand", "Grass", "Forest", "Farmland", "Scrub", "Rock", "Snow"]
WATER, SAND, GRASS, FOREST, FARM, SCRUB, ROCK, SNOW = range(8)
RANK = {"city": 3, "town": 2, "village": 1, "farm": 0}

# prop sizes in feet (width, height)
PROP_FT = {"car": (15.0, 6.5), "wreck": (15.0, 7.0), "barricade": (12.0, 3.5),
           "rubble": (12.0, 10.0), "overgrowth": (20.0, 16.0)}
ZOMBIE_KINDS = [("Walker", 0.70), ("Runner", 0.22), ("Brute", 0.08)]

# the eight ways the macro layout can be turned (orthogonal matrices), used by the "varied" layout
MATS = [(1, 0, 0, 1), (-1, 0, 0, 1), (1, 0, 0, -1), (-1, 0, 0, -1),
        (0, 1, 1, 0), (0, -1, 1, 0), (0, 1, -1, 0), (0, -1, -1, 0)]
NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

NAME_A = ["Cedar", "Alder", "Spruce", "Eagle", "Salmon", "Cascade", "Harbor", "Rainier", "Olympic", "Mill",
          "Rock", "Fir", "Orca", "Bluff", "Summit", "Prairie", "Willow", "Sunset", "Lake", "River", "Stone",
          "Iron", "Cold", "Elk", "Bear", "Heron", "Wolf", "Crow", "Maple", "Pine"]
NAME_B = ["ton", "ville", "burg", "port", "falls", "ridge", "dale", "view", "haven", "ford", "wood",
          "field", "bay", "creek", "springs", " Junction", " Heights", " Landing", " Crossing"]
FARM_NAMES = ["Miller", "Hansen", "Olsen", "Baker", "Reyes", "Nguyen", "Carter", "Lindqvist", "Wagner", "Holt"]
LABELS = {"Shop": ["Diner", "Pharmacy", "Hardware", "Market", "Bank", "Gas", "Clinic", "Bar", "Pawn"],
          "Warehouse": ["Depot", "Storage", "Freight", "Garage"], "Inn": ["Motel", "Inn", "Hotel"]}


# ----------------------------------------------------------------------------- noise helpers
def mix(*vals):
    h = 1469598103934665603
    for v in vals:
        h ^= int(v) & 0xFFFFFFFFFFFF
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def hash2(ix, iy, seed):
    h = (ix * 374761393 + iy * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def vnoise(x, y, seed):
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = hash2(ix, iy, seed), hash2(ix + 1, iy, seed)
    c, d = hash2(ix, iy + 1, seed), hash2(ix + 1, iy + 1, seed)
    return a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy


def fbm(x, y, seed, octaves=3):
    total, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        total += amp * vnoise(x, y, seed + o * 101)
        norm += amp
        x, y, amp = x * 2.03, y * 2.03, amp * 0.5
    return total / norm


def smooth(a, b, x):
    if b == a:
        return 1.0 if x >= a else 0.0
    t = (x - a) / (b - a)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3 - 2 * t)


def chaikin(pts, iterations=3):
    for _ in range(iterations):
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        out.append(pts[-1])
        pts = out
    return pts


def bbox_of(pts, pad=0.0):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def overlaps(bb, x0, y0, x1, y1):
    return not (bb[2] < x0 or bb[0] > x1 or bb[3] < y0 or bb[1] > y1)


def make_name(rng, kind):
    if kind == "farm":
        return rng.choice(FARM_NAMES) + " Farm"
    return rng.choice(NAME_A) + rng.choice(NAME_B)


def make_params(rng, layout):
    """The macro layout, in unit-square coordinates (u runs west to east, v north to south)."""
    if layout == "washington":
        p = {"coast": 0.12 + rng.uniform(-0.015, 0.015), "ridge": 0.50 + rng.uniform(-0.03, 0.03),
             "ridge_w": 0.075 + rng.uniform(-0.01, 0.01),
             "passes": [0.27 + rng.uniform(-0.04, 0.04), 0.70 + rng.uniform(-0.04, 0.04)],
             "sound": True, "oly": True, "m": 0}
    else:   # varied: shuffle the same ingredients and turn the whole picture
        p = {"coast": rng.uniform(0.06, 0.22), "ridge": rng.uniform(0.34, 0.66), "ridge_w": rng.uniform(0.05, 0.10),
             "passes": sorted(rng.uniform(0.12, 0.88) for _ in range(rng.randint(1, 3))),
             "sound": rng.random() < 0.7, "oly": rng.random() < 0.5, "m": rng.randrange(8)}
    p["sound_u"] = p["coast"] + 0.11 + rng.uniform(-0.01, 0.015)
    p["sound_v0"] = 0.07 + rng.uniform(0, 0.03)
    p["sound_v1"] = 0.48 + rng.uniform(-0.04, 0.06)
    p["oly_u"], p["oly_v"] = p["coast"] + 0.05, 0.22 + rng.uniform(-0.03, 0.05)
    return p


class Settlement:
    __slots__ = ("kind", "x", "y", "radius", "name", "uid")

    def __init__(self, kind, x, y, radius, name, uid):
        self.kind, self.x, self.y, self.radius, self.name, self.uid = kind, x, y, radius, name, uid


class World:
    def __init__(self, seed=1, layout="washington", width_mi=240, height_mi=160, density=1, zombies=None):
        self.seed = int(seed)
        self.layout = layout
        self.W, self.H = width_mi * MILE, height_mi * MILE
        self.x0, self.y0 = -self.W / 2, -self.H / 2
        self.density = max(0, min(2, int(density)))
        self.zombies = dict(cars=True, barricades=True, rubble=True, overgrowth=True, zombies=True)
        if zombies:
            self.zombies.update(zombies)
        rng = random.Random(mix(self.seed, 1))
        self.P = make_params(rng, layout)
        self.M = MATS[self.P["m"]]
        self.done = set()           # detail chunks that have been generated
        self._tiles, self._troads = {}, {}
        self._build_grid()
        self._build_rivers(rng)
        self._build_state_settlements()
        self._build_highways()

    # ------------------------------------------------------------------ the shape of the land
    def macro(self, mu, mv):
        """Elevation (0..1) and moisture (0..1) for unit-square coordinates in the macro layout."""
        P, s = self.P, self.seed
        coast = P["coast"] + 0.05 * (fbm(mv * 4, 3.7, s + 1, 3) - 0.5)
        land = smooth(coast - 0.025, coast + 0.025, mu)
        ridge_c = P["ridge"] + 0.07 * (fbm(mv * 2.5, 9.1, s + 2, 2) - 0.5)
        ridge = math.exp(-((mu - ridge_c) / P["ridge_w"]) ** 2)
        for pv in P["passes"]:
            ridge *= 1 - 0.75 * math.exp(-((mv - pv) / 0.035) ** 2)
        rough = fbm(mu * 9, mv * 9, s + 3, 3)
        e = 0.06 + 0.30 * land + 0.50 * ridge * land + 0.22 * (rough - 0.5) * land
        if P["oly"]:
            e += 0.40 * land * math.exp(-(((mu - P["oly_u"]) / 0.05) ** 2 + ((mv - P["oly_v"]) / 0.11) ** 2))
        if P["sound"]:   # a long inland sea with islands, opening to the ocean through a strait in the north
            su = P["sound_u"] + 0.025 * (fbm(mv * 6, 1.3, s + 4, 2) - 0.5)
            along = (smooth(P["sound_v0"] - 0.03, P["sound_v0"] + 0.02, mv)
                     * (1 - smooth(P["sound_v1"] - 0.04, P["sound_v1"] + 0.03, mv)))
            width = 0.022 + 0.02 * fbm(mv * 9, 5.5, s + 5, 2)
            trough = along * math.exp(-((mu - su) / width) ** 2)
            trough *= 1 - min(1.0, max(0.0, fbm(mu * 40, mv * 40, s + 6, 2) - 0.62) * 5)   # islands
            e = e * (1 - trough) + (SEA - 0.06) * trough
            strait = (math.exp(-((mv - P["sound_v0"]) / 0.022) ** 2)
                      * smooth(coast - 0.03, coast, mu) * (1 - smooth(su, su + 0.03, mu)))
            e = e * (1 - strait) + (SEA - 0.06) * strait
        lake = max(0.0, fbm(mu * 7, mv * 7, s + 7, 2) - 0.70)
        if e < 0.5:
            e -= 2.2 * lake * land
        maritime = 1 - smooth(coast, coast + 0.3, mu)
        rain = smooth(ridge_c - 0.25, ridge_c - 0.05, mu)
        shadow = smooth(ridge_c + 0.02, ridge_c + 0.22, mu)
        moist = 0.45 + 0.30 * maritime + 0.35 * rain - 0.55 * shadow + 0.25 * (fbm(mu * 6, mv * 6, s + 8, 2) - 0.5)
        moist += 0.25 * max(0.0, e - 0.45)
        return max(0.0, e), max(0.0, min(1.0, moist))

    def _to_macro(self, x, y):
        cu, cv = (x - self.x0) / self.W - 0.5, (y - self.y0) / self.H - 0.5
        a, b, c, d = self.M
        return a * cu + b * cv + 0.5, c * cu + d * cv + 0.5

    def _from_macro(self, mu, mv):
        cu, cv = mu - 0.5, mv - 0.5
        a, b, c, d = self.M
        return self.x0 + (a * cu + c * cv + 0.5) * self.W, self.y0 + (b * cu + d * cv + 0.5) * self.H

    def eval_macro(self, x, y):
        return self.macro(*self._to_macro(x, y))

    def terrain(self, x, y):
        """Elevation and moisture at a point: the stored grid inside the state, the formula outside it."""
        gx, gy = (x - self.x0) / CELL_FT, (y - self.y0) / CELL_FT
        if 0 <= gx < self.gw - 1 and 0 <= gy < self.gh - 1:
            i, j = int(gx), int(gy)
            fx, fy = gx - i, gy - j
            g, k = self.gw, int(gy) * self.gw + int(gx)
            E, Mo = self.E, self.Mo
            e = E[k] + (E[k + 1] - E[k]) * fx + (E[k + g] - E[k]) * fy + (E[k] - E[k + 1] - E[k + g] + E[k + g + 1]) * fx * fy
            m = Mo[k] + (Mo[k + 1] - Mo[k]) * fx + (Mo[k + g] - Mo[k]) * fy + (Mo[k] - Mo[k + 1] - Mo[k + g] + Mo[k + g + 1]) * fx * fy
        else:
            e, m = self.eval_macro(x, y)
        s = self.seed
        e += (vnoise(x / 2400.0, y / 2400.0, s + 11) - 0.5) * 0.07 + (vnoise(x / 500.0, y / 500.0, s + 12) - 0.5) * 0.025
        return e, m

    def biome(self, e, m, x, y):
        if e < SEA:
            return WATER
        if e < SEA + 0.022:
            return SAND
        if e > 0.80:
            return SNOW
        if e > 0.66:
            return ROCK if (m < 0.62 or e > 0.74) else FOREST
        if m > 0.58:
            return FOREST
        if m > 0.40:
            return GRASS
        if m > 0.24 and e < 0.44 and vnoise(x / 30000.0, y / 30000.0, self.seed + 13) > 0.45:
            return FARM
        return SCRUB

    def is_water(self, x, y):
        return self.terrain(x, y)[0] < SEA

    def inside(self, x, y):
        return self.x0 <= x <= self.x0 + self.W and self.y0 <= y <= self.y0 + self.H

    def _build_grid(self):
        self.gw, self.gh = int(self.W / CELL_FT) + 2, int(self.H / CELL_FT) + 2
        self.E, self.Mo = [], []
        for j in range(self.gh):
            y = self.y0 + j * CELL_FT
            for i in range(self.gw):
                e, m = self.eval_macro(self.x0 + i * CELL_FT, y)
                self.E.append(e)
                self.Mo.append(m)
        self.rgw, self.rgh = int(self.W / ROUTE_FT) + 1, int(self.H / ROUTE_FT) + 1
        self.RE = [self.terrain(self.x0 + (i + 0.5) * ROUTE_FT, self.y0 + (j + 0.5) * ROUTE_FT)[0]
                   for j in range(self.rgh) for i in range(self.rgw)]

    # ------------------------------------------------------------------ rivers
    def _build_rivers(self, rng):
        P = self.P
        pv = P["passes"][-1]   # the big river runs through the lowest gap in the mountains, like the Columbia
        waypoints = [(0.92, -0.04), (0.90, 0.26), (0.80, 0.46), (0.64, 0.58), (P["ridge"], pv),
                     (P["ridge"] - 0.16, pv + 0.04), (P["coast"] + 0.05, pv + 0.07), (P["coast"] - 0.03, pv + 0.08)]
        pts = chaikin([self._from_macro(u, v) for u, v in waypoints], 4)
        wiggled = []
        for i, (x, y) in enumerate(pts):
            a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            n = math.hypot(dx, dy) or 1.0
            off = (fbm(i * 0.12, 0.3, self.seed + 20, 2) - 0.5) * 9000.0
            wiggled.append((x - dy / n * off, y + dx / n * off))
        self.rivers = [{"pts": wiggled, "width": 1300.0, "bbox": bbox_of(wiggled, 2000)}]
        gw, gh, RE = self.rgw, self.rgh, self.RE
        high = [(i, j) for j in range(gh) for i in range(gw) if RE[j * gw + i] > 0.62]
        rng.shuffle(high)
        for i, j in high[:8 + 4 * self.density]:
            path, seen = [(i, j)], {(i, j)}
            for _ in range(300):
                ci, cj = path[-1]
                best, low = None, RE[cj * gw + ci]
                for di, dj in NB8:
                    ni, nj = ci + di, cj + dj
                    if 0 <= ni < gw and 0 <= nj < gh and (ni, nj) not in seen:
                        e = RE[nj * gw + ni] + rng.random() * 0.01
                        if e < low:
                            best, low = (ni, nj), e
                if best is None:
                    break
                path.append(best)
                seen.add(best)
                if RE[best[1] * gw + best[0]] < SEA:
                    break
            if len(path) < 6:
                continue
            pts = [(self.x0 + (a + 0.5 + rng.uniform(-0.3, 0.3)) * ROUTE_FT,
                    self.y0 + (b + 0.5 + rng.uniform(-0.3, 0.3)) * ROUTE_FT) for a, b in path]
            pts = chaikin(pts, 3)
            self.rivers.append({"pts": pts, "width": min(650.0, 140.0 + len(path) * 22.0), "bbox": bbox_of(pts, 1000)})

    # ------------------------------------------------------------------ settlements and roads
    def tile_settlements(self, tx, ty):
        key = (tx, ty)
        got = self._tiles.get(key)
        if got is not None:
            return got
        rng = random.Random(mix(self.seed, tx, ty, 5))
        dens = (0.55, 1.0, 1.8)[self.density]
        plan = (("city", 0.040 * dens, 3500, 6000, 30000.0), ("town", 0.30 * dens, 1500, 2600, 9000.0),
                ("village", 1.4 * dens, 600, 1000, 4500.0), ("farm", 3.5 * dens, 260, 380, 2500.0))
        out, made = [], 0
        for kind, lam, r0, r1, spacing in plan:
            n = int(lam) + (1 if rng.random() < lam - int(lam) else 0)
            for _ in range(n):
                for _try in range(14):
                    x, y = (tx + rng.random()) * TILE_FT, (ty + rng.random()) * TILE_FT
                    e, m = self.terrain(x, y)
                    if not (SEA + 0.04 < e < 0.58) or self.biome(e, m, x, y) in (WATER, SAND, ROCK, SNOW):
                        continue
                    r = rng.uniform(r0, r1)
                    if any(math.hypot(x - o.x, y - o.y) < max(spacing, o.radius + r + 150) for o in out):
                        continue
                    out.append(Settlement(kind, x, y, r, make_name(rng, kind), mix(tx, ty, made) & 0x7FFFFFFF))
                    made += 1
                    break
        self._tiles[key] = out
        return out

    def _tile_keys(self, x0, y0, x1, y1, margin=0):
        return [(tx, ty) for tx in range(math.floor(x0 / TILE_FT) - margin, math.floor(x1 / TILE_FT) + 1 + margin)
                for ty in range(math.floor(y0 / TILE_FT) - margin, math.floor(y1 / TILE_FT) + 1 + margin)]

    def settlements_near(self, x, y, r):
        out = []
        for key in self._tile_keys(x - r, y - r, x + r, y + r):
            out.extend(s for s in self.tile_settlements(*key) if math.hypot(s.x - x, s.y - y) < r)
        return out

    def settlements_in_rect(self, x0, y0, x1, y1):
        """Settlements to show on the map. Huge views only use tiles that are already known."""
        keys = self._tile_keys(x0, y0, x1, y1)
        if len(keys) > 150:
            keys = [k for k in keys if k in self._tiles]
        return [s for k in keys for s in self.tile_settlements(*k) if x0 - 6000 <= s.x <= x1 + 6000 and y0 - 6000 <= s.y <= y1 + 6000]

    def _crosses_water(self, ax, ay, bx, by):
        d = math.hypot(bx - ax, by - ay)
        n = max(2, int(d / MILE))
        return any(self.is_water(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(1, n))

    def tile_roads(self, tx, ty):
        """County roads and spurs owned by the settlements of one tile."""
        key = (tx, ty)
        got = self._troads.get(key)
        if got is not None:
            return got
        around = [s for dx in (-1, 0, 1) for dy in (-1, 0, 1) for s in self.tile_settlements(tx + dx, ty + dy)]
        out = []
        for s in self.tile_settlements(tx, ty):
            if s.kind == "city":
                continue
            cand = [o for o in around if RANK[o.kind] > RANK[s.kind] or (RANK[o.kind] == RANK[s.kind] and o.uid < s.uid)]
            cand.sort(key=lambda o: (o.x - s.x) ** 2 + (o.y - s.y) ** 2)
            for o in cand[:3]:
                d = math.hypot(o.x - s.x, o.y - s.y)
                if d > 20 * MILE:
                    break
                if self._crosses_water(s.x, s.y, o.x, o.y):
                    continue
                r = random.Random(mix(self.seed, s.uid, o.uid))
                off = (r.random() - 0.5) * 0.15 * d
                mx, my = (s.x + o.x) / 2, (s.y + o.y) / 2
                nx, ny = (o.y - s.y) / (d or 1.0), -(o.x - s.x) / (d or 1.0)
                pts = chaikin([(s.x, s.y), (mx + nx * off, my + ny * off), (o.x, o.y)], 2)
                kind = "county" if s.kind == "town" else "spur"
                out.append({"kind": kind, "pts": pts, "width": 24.0 if kind == "county" else 14.0, "bbox": bbox_of(pts, 40)})
                break
        self._troads[key] = out
        return out

    def roads_in_rect(self, x0, y0, x1, y1, minor=True):
        out = [h for h in self.highways if overlaps(h["bbox"], x0, y0, x1, y1)]
        if minor:
            keys = self._tile_keys(x0, y0, x1, y1, margin=2)
            if len(keys) > 150:
                keys = [k for k in keys if k in self._troads]
            for key in keys:
                out.extend(r for r in self.tile_roads(*key) if overlaps(r["bbox"], x0, y0, x1, y1))
        return out

    def _build_state_settlements(self):
        tx0, tx1 = math.floor(self.x0 / TILE_FT), math.floor((self.x0 + self.W) / TILE_FT)
        ty0, ty1 = math.floor(self.y0 / TILE_FT), math.floor((self.y0 + self.H) / TILE_FT)
        self.state_tiles = [(tx, ty) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)]
        everyone = [s for key in self.state_tiles for s in self.tile_settlements(*key) if self.inside(s.x, s.y)]
        self.cities = [s for s in everyone if s.kind == "city"]
        self.towns = [s for s in everyone if s.kind == "town"]
        want = 3 if self.W * self.H > 15000 * MILE * MILE else 2
        while len(self.cities) < want and self.towns:   # tiny states still get a couple of cities
            t = max(self.towns, key=lambda s: s.radius)
            self.towns.remove(t)
            t.kind, t.radius = "city", max(t.radius * 2.0, 3500.0)
            self.cities.append(t)
        for key in self.state_tiles:
            self.tile_roads(*key)

    # ------------------------------------------------------------------ highways between the cities
    def _route(self, a, b):
        gw, gh, RE = self.rgw, self.rgh, self.RE

        def node(s):
            return (min(gw - 1, max(0, int((s.x - self.x0) / ROUTE_FT))),
                    min(gh - 1, max(0, int((s.y - self.y0) / ROUTE_FT))))

        start, goal = node(a), node(b)
        if start == goal:
            return None
        heap, g, came = [(0.0, start)], {start: 0.0}, {}
        while heap:
            _f, cur = heapq.heappop(heap)
            if cur == goal:
                break
            ci, cj = cur
            e0 = RE[cj * gw + ci]
            for di, dj in NB8:
                ni, nj = ci + di, cj + dj
                if not (0 <= ni < gw and 0 <= nj < gh):
                    continue
                e = RE[nj * gw + ni]
                if e < SEA and (ni, nj) != goal:
                    continue
                step = ROUTE_FT * (1.4142 if di and dj else 1.0)
                ng = g[cur] + step * (1.0 + 30.0 * abs(e - e0) + 40.0 * max(0.0, e - 0.5) ** 2)
                if ng < g.get((ni, nj), 1e30):
                    g[(ni, nj)], came[(ni, nj)] = ng, cur
                    heapq.heappush(heap, (ng + math.hypot(goal[0] - ni, goal[1] - nj) * ROUTE_FT, (ni, nj)))
        if goal not in came:
            return None
        path = [goal]
        while path[-1] != start:
            path.append(came[path[-1]])
        path.reverse()
        pts = [(a.x, a.y)] + [(self.x0 + (i + 0.5) * ROUTE_FT, self.y0 + (j + 0.5) * ROUTE_FT) for i, j in path[1:-1]] + [(b.x, b.y)]
        return chaikin(pts, 2)

    def _build_highways(self):
        self.highways = []
        cities = self.cities
        if len(cities) < 2:
            return
        linked, edges = {0}, []
        while len(linked) < len(cities):
            best = None
            for i in linked:
                for j in range(len(cities)):
                    if j not in linked:
                        d = math.hypot(cities[i].x - cities[j].x, cities[i].y - cities[j].y)
                        if best is None or d < best[0]:
                            best = (d, i, j)
            edges.append((best[1], best[2]))
            linked.add(best[2])
        for i, c in enumerate(cities):   # a few extra links so the network has loops
            near = sorted((j for j in range(len(cities)) if j != i and (i, j) not in edges and (j, i) not in edges),
                          key=lambda j: math.hypot(c.x - cities[j].x, c.y - cities[j].y))
            if near and math.hypot(c.x - cities[near[0]].x, c.y - cities[near[0]].y) < 70 * MILE and len(edges) < len(cities) + 5:
                edges.append((i, near[0]))
        for i, j in edges:
            pts = self._route(cities[i], cities[j])
            if pts:
                self.highways.append({"kind": "highway", "pts": pts, "width": 60.0, "bbox": bbox_of(pts, 100)})

    def start_point(self):
        if self.cities:
            c = max(self.cities, key=lambda s: s.radius)
            return c.x, c.y
        return 0.0, 0.0

    def stats(self):
        return {"cities": len(self.cities), "towns": len(self.towns), "highways": len(self.highways),
                "rivers": len(self.rivers)}

    # ------------------------------------------------------------------ street-level detail
    def plan_chunk(self, cx, cy):
        """Streets, buildings, props and zombies for one 800 ft chunk, as plain dictionaries (feet)."""
        x0, y0 = cx * CHUNK_FT, cy * CHUNK_FT
        x1, y1 = x0 + CHUNK_FT, y0 + CHUNK_FT
        rng = random.Random(mix(self.seed, cx, cy, 91))
        specs = []
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        near = [s for s in self.settlements_near(mx, my, 7000.0) if math.hypot(s.x - mx, s.y - my) < s.radius + CHUNK_FT * 0.71]
        near.sort(key=lambda s: -RANK[s.kind])
        for s in near[:2]:
            self._plan_settlement(s, x0, y0, x1, y1, rng, specs)
        self._plan_roadside(x0, y0, x1, y1, rng, specs)
        return specs

    def _building(self, specs, built, rng, x, y, w, h, zone, frac):
        if zone == "downtown":
            kind = rng.choice(("Shop", "Shop", "Warehouse", "Inn"))
        elif frac < 0.45 and rng.random() < (0.5 if zone == "city" else 0.3):
            kind = rng.choice(("Shop", "Shop", "Warehouse", "Inn"))
        elif zone == "village" and rng.random() < 0.12:
            kind = "Barn"
        else:
            kind = "House"
        label = rng.choice(LABELS[kind]) if kind in LABELS and rng.random() < 0.6 else ""
        specs.append({"t": "building", "x": x, "y": y, "w": w, "h": h, "kind": kind, "label": label})
        built.append((x, y, w, h))

    def _zombie(self, specs, built, rng, x0, y0, x1, y1, s=None):
        for _ in range(8):
            x, y = round(rng.uniform(x0, x1) / 5) * 5, round(rng.uniform(y0, y1) / 5) * 5
            if s is not None and math.hypot(x - s.x, y - s.y) > s.radius:
                continue
            if any(bx - 3 < x < bx + bw + 3 and by - 3 < y < by + bh + 3 for bx, by, bw, bh in built):
                continue
            if self.is_water(x, y):
                continue
            r, acc, kind = rng.random(), 0.0, ZOMBIE_KINDS[-1][0]
            for name, weight in ZOMBIE_KINDS:
                acc += weight
                if r < acc:
                    kind = name
                    break
            specs.append({"t": "token", "kind": kind, "x": x, "y": y})
            return

    def _plan_settlement(self, s, x0, y0, x1, y1, rng, specs):
        if s.kind == "farm":
            self._plan_farm(s, x0, y0, x1, y1, rng, specs)
            return
        pitch, sw = {"city": (300.0, 32.0), "town": (260.0, 28.0), "village": (220.0, 24.0)}[s.kind]
        R, zf = s.radius, self.zombies
        dz = (0.6, 1.0, 1.6)[self.density]
        streets, built = [], []
        for axis in (0, 1):   # streets: every grid line that falls inside this chunk
            lo, hi = (x0, x1) if axis == 0 else (y0, y1)
            c0, cc = (s.x, s.y) if axis == 0 else (s.y, s.x)
            a_lo, a_hi = (y0, y1) if axis == 0 else (x0, x1)
            for k in range(math.ceil((lo - c0) / pitch), math.ceil((hi - c0) / pitch)):
                pos = c0 + k * pitch
                off = abs(pos - c0)
                if off >= R:
                    continue
                half = math.sqrt(R * R - off * off)
                a, b = max(a_lo, cc - half), min(a_hi, cc + half)
                if b - a < 20:
                    continue
                pts = [(pos, a), (pos, b)] if axis == 0 else [(a, pos), (b, pos)]
                if self.is_water((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2):
                    continue
                width = sw + (18.0 if s.kind == "city" and k % 4 == 0 else 0.0)
                specs.append({"t": "road", "pts": pts, "width": width, "style": "Paved"})
                streets.append((pts[0], pts[1], width))
        for bi in range(math.ceil((x0 - s.x) / pitch - 0.5), math.ceil((x1 - s.x) / pitch - 0.5)):
            for bj in range(math.ceil((y0 - s.y) / pitch - 0.5), math.ceil((y1 - s.y) / pitch - 0.5)):
                bx, by = s.x + bi * pitch, s.y + bj * pitch
                cxm, cym = bx + pitch / 2, by + pitch / 2
                d = math.hypot(cxm - s.x, cym - s.y)
                if d > R * 0.97 or self.is_water(cxm, cym):
                    continue
                brng = random.Random(mix(self.seed, s.uid, bi, bj))
                frac = d / R
                p_fill = (0.92 - 0.55 * frac) if s.kind == "city" else (0.78 - 0.45 * frac)
                ix, iy, iw = bx + sw / 2 + 5, by + sw / 2 + 5, pitch - sw - 10
                if s.kind == "city" and frac < 0.28:   # downtown: one big building per block
                    w, h = iw * brng.uniform(0.7, 0.95), iw * brng.uniform(0.6, 0.9)
                    self._building(specs, built, brng, ix + (iw - w) / 2, iy + (iw - h) / 2, w, h, "downtown", frac)
                    continue
                for row in (0, 1):   # otherwise two rows of lots facing the streets
                    depth = brng.uniform(34, 50)
                    y = iy + 2 if row == 0 else iy + iw - depth - 2
                    x = ix
                    while x < ix + iw - 30:
                        lw = min(brng.uniform(38, 68), ix + iw - x)
                        if lw < 28:
                            break
                        if brng.random() < p_fill:
                            bw = lw - brng.uniform(6, 12)
                            self._building(specs, built, brng, x + (lw - bw) / 2, y, bw,
                                           depth * brng.uniform(0.8, 1.0), s.kind, frac)
                        x += lw
        if zf.get("cars"):
            cd = {"city": 0.35, "town": 0.25, "village": 0.18}[s.kind] * dz
            for a, b, width in streets:
                ex = math.hypot(b[0] - a[0], b[1] - a[1]) / 260.0 * cd
                horizontal = abs(a[1] - b[1]) < 1e-6
                for _ in range(int(ex) + (1 if rng.random() < ex - int(ex) else 0)):
                    t = rng.random()
                    x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
                    lane = rng.choice((-1, 1)) * width * 0.25
                    x, y = (x, y + lane) if horizontal else (x + lane, y)
                    wreck = rng.random() < 0.3
                    rot = (0 if horizontal else 90) + rng.choice((0, 180)) + (rng.uniform(-40, 40) if wreck else rng.uniform(-6, 6))
                    specs.append({"t": "prop", "kind": "wreck" if wreck else "car", "x": x, "y": y, "rot": rot})
        if zf.get("barricades") and streets and rng.random() < (0.28 if s.kind == "city" else 0.35) * dz:
            a, b, width = rng.choice(streets)
            t = rng.uniform(0.2, 0.8)
            specs.append({"t": "prop", "kind": "barricade", "x": a[0] + (b[0] - a[0]) * t, "y": a[1] + (b[1] - a[1]) * t,
                          "rot": 90 if abs(a[1] - b[1]) < 1e-6 else 0, "w": width + 4, "h": 3.5})
        for x, y, w, h in built:
            if zf.get("rubble") and rng.random() < 0.07 * dz:
                specs.append({"t": "prop", "kind": "rubble", "x": x + rng.uniform(0, w), "y": y + h + rng.uniform(2, 8),
                              "rot": rng.uniform(0, 360)})
            if zf.get("overgrowth") and math.hypot(x - s.x, y - s.y) > 0.55 * R and rng.random() < 0.12 * dz:
                specs.append({"t": "prop", "kind": "overgrowth", "x": x + rng.uniform(0, w), "y": y + rng.uniform(0, h),
                              "rot": rng.uniform(0, 360)})
        if zf.get("zombies"):
            base = {"city": 5.5, "town": 2.5, "village": 1.0}[s.kind] * dz
            if math.hypot((x0 + x1) / 2 - s.x, (y0 + y1) / 2 - s.y) > R * 0.9:
                base *= 0.4
            for _ in range(int(base) + (1 if rng.random() < base - int(base) else 0)):
                self._zombie(specs, built, rng, x0, y0, x1, y1, s)

    def _plan_farm(self, s, x0, y0, x1, y1, rng, specs):
        if not (x0 <= s.x < x1 and y0 <= s.y < y1):
            return
        f, zf = random.Random(mix(self.seed, s.uid, 7)), self.zombies
        dz = (0.6, 1.0, 1.6)[self.density]
        built = [(s.x - 22, s.y - 16, 44, 32), (s.x + 40, s.y - 22, 60, 42)]
        fx0, fy0, fx1, fy1 = s.x - 260, s.y + 70, s.x + 260, s.y + 330
        specs.append({"t": "terrain", "name": "Farmland", "pts": [(fx0, fy0), (fx1, fy0), (fx1, fy1), (fx0, fy1), (fx0, fy0)]})
        specs.append({"t": "building", "x": built[0][0], "y": built[0][1], "w": 44, "h": 32, "kind": "House", "label": ""})
        specs.append({"t": "building", "x": built[1][0], "y": built[1][1], "w": 60, "h": 42, "kind": "Barn", "label": ""})
        c = [(s.x - 34, s.y - 36), (s.x + 112, s.y - 36), (s.x + 112, s.y + 40), (s.x - 34, s.y + 40)]
        for i in range(4):
            specs.append({"t": "fence", "pts": [c[i], c[(i + 1) % 4]], "style": "Wood"})
        specs.append({"t": "road", "pts": [(s.x + 112, s.y + 10), (s.x + 330, s.y + 10)], "width": 12.0, "style": "Dirt"})
        if zf.get("cars") and f.random() < 0.6:
            specs.append({"t": "prop", "kind": f.choice(("car", "wreck")), "x": s.x + 60, "y": s.y + 56, "rot": f.uniform(0, 360)})
        if zf.get("overgrowth") and f.random() < 0.5:
            specs.append({"t": "prop", "kind": "overgrowth", "x": s.x - 50, "y": s.y + 20, "rot": f.uniform(0, 360)})
        if zf.get("zombies"):
            for _ in range(int(f.choice((0, 0, 1, 2)) * dz + 0.5)):
                self._zombie(specs, built, f, s.x - 60, s.y - 60, s.x + 140, s.y + 80)

    def _plan_roadside(self, x0, y0, x1, y1, rng, specs):
        """Abandoned cars and the odd walker along the roads that cross this chunk."""
        zf = self.zombies
        if not (zf.get("cars") or zf.get("zombies")):
            return
        dz = (0.6, 1.0, 1.6)[self.density]
        for road in self.roads_in_rect(x0 - 60, y0 - 60, x1 + 60, y1 + 60):
            width = road["width"]
            for (ax, ay), (bx, by) in zip(road["pts"], road["pts"][1:]):
                seg = math.hypot(bx - ax, by - ay)
                if seg < 1:
                    continue
                ux, uy = (bx - ax) / seg, (by - ay) / seg
                for k in range(int(seg // 90)):
                    t = (k + rng.random()) * 90.0
                    x, y, r = ax + ux * t, ay + uy * t, rng.random()
                    if not (x0 <= x < x1 and y0 <= y < y1):
                        continue
                    if zf.get("cars") and r < 0.045 * dz:
                        lane = rng.choice((-1, 1)) * width * 0.25
                        wreck = rng.random() < 0.3
                        ang = math.degrees(math.atan2(uy, ux)) + rng.choice((0, 180))
                        specs.append({"t": "prop", "kind": "wreck" if wreck else "car", "x": x - uy * lane, "y": y + ux * lane,
                                      "rot": ang + (rng.uniform(-40, 40) if wreck else rng.uniform(-5, 5))})
                    elif zf.get("zombies") and r < 0.06 * dz:
                        self._zombie(specs, [], rng, x - 40, y - 40, x + 40, y + 40)

    # ------------------------------------------------------------------ saving
    def to_dict(self):
        return {"seed": self.seed, "layout": self.layout, "width_mi": self.W / MILE, "height_mi": self.H / MILE,
                "density": self.density, "zombies": self.zombies, "done": sorted([list(k) for k in self.done])}

    @classmethod
    def from_dict(cls, d):
        w = cls(d["seed"], d["layout"], d["width_mi"], d["height_mi"], d["density"], d.get("zombies"))
        w.done = {tuple(k) for k in d.get("done", [])}
        return w
