"""Build the site visit's 3D scheme in Blender and export it as site-visit-3d.glb.

    blender --background --factory-startup --python site_visit_3d.py
    blender -b --factory-startup -P site_visit_3d.py -- --preview shot.png

Every dimension is read out of site_visit.tpl.html, so the model cannot drift from
the profile it extrudes: x runs downstream in the profile's own units, elevation is
the profile's y turned the right way up, and the third axis is an invented valley --
the river along y = 0, the headrace holding its level on the far hillside, the
penstock dropping back to the bank. The page's own tour, labels and calculator then
drive the model through named nodes:

    <name>__canal   run-of-river and peaking run-of-river (they share a headrace)
    <name>__ror     run-of-river only          <name>__pror   peaking only
    <name>__res     reservoir only             anything else  every plant type
    anchor__<hotspot>__<variant>    where that hotspot's label sits
    head_{from,top,bot}__<variant>  the gross-head dimension
    pond_{lo,hi}__pror              the daily pond's drawdown range

Blender is only needed when the geometry changes. The .glb is committed and
hydro.py inlines it into site-visit.html, so CI never runs this.
"""
import bpy, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = open(os.path.join(HERE, "site_visit.tpl.html"), encoding="utf-8").read()
OUT = os.path.join(HERE, "site-visit-3d.glb")
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = ARGS[ARGS.index("--preview") + 1] if "--preview" in ARGS else None
VARIANT = ARGS[ARGS.index("--variant") + 1] if "--variant" in ARGS else "ror"


def const(name):
    m = re.search(r"\b%s\s*=\s*(-?\d+(?:\.\d+)?)\b" % name, TPL)
    if not m: raise SystemExit(f"site_visit.tpl.html no longer defines {name}")
    return float(m.group(1))


def const_list(name):
    m = re.search(r"\b%s\s*=\s*(\[(?:\[[^\]]*\],?\s*)+\]|\[[^\]]*\])" % name, TPL)
    if not m: raise SystemExit(f"site_visit.tpl.html no longer defines {name}")
    return json.loads(m.group(1))


BED = const_list("BED")
WEIR_X, INTAKE_X, DESANDER_X, FOREBAY_X = (const(n) for n in ("WEIR_X", "INTAKE_X", "DESANDER_X", "FOREBAY_X"))
PH_X, PH_W, SWITCH_X, METER_X = (const(n) for n in ("PH_X", "PH_W", "SWITCH_X", "METER_X"))
TOWER_X = const_list("TOWER_X")
HR_Y0, HR_Y1 = const("HR_Y0"), const("HR_Y1")
DAM_X, DAM_CREST = const("DAM_X"), const("DAM_CREST")
RES_PH_X, RES_PH_W = const("RES_PH_X"), const("RES_PH_W")
FSL, POND_X0 = const("FSL"), const("POND_X0")

# the profile draws the reach above the weir as a literal path; it is the same river
RIVER = [[-120, 346], [0, 356], [120, 372]] + BED + [[1760, 762]]
X0, X1, YN, YF = -60.0, 1660.0, -240.0, 760.0   # diorama footprint; y > 0 is the far bank
STEP, BASE, SLOPE = 10.0, -900.0, 0.9


# ── shared shape functions: every structure below is placed with these ───────────
def clamp(v, a, b): return a if v < a else b if v > b else v
def lerp(a, b, t): return a + (b - a) * t
def ss(a, b, x):
    t = clamp((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _hash(i, j):
    s = math.sin(i * 127.1 + j * 311.7) * 43758.5453
    return s - math.floor(s)


def vn(x, y):
    i, j = math.floor(x), math.floor(y)
    u, v = ss(0, 1, x - i), ss(0, 1, y - j)
    return lerp(lerp(_hash(i, j), _hash(i + 1, j), u), lerp(_hash(i, j + 1), _hash(i + 1, j + 1), u), v)


def fbm(x, y): return vn(x, y) * .55 + vn(x * 2.1 + 5, y * 2.1) * .3 + vn(x * 4.3, y * 4.3 + 7) * .15


def river_y(x):
    for (x0, y0), (x1, y1) in zip(RIVER, RIVER[1:]):
        if x <= x1: return lerp(y0, y1, clamp((x - x0) / (x1 - x0), 0, 1))
    return RIVER[-1][1]


def bed(x): return -river_y(x)
def canal(x): return -lerp(HR_Y0, HR_Y1, clamp((x - INTAKE_X) / (FOREBAY_X - INTAKE_X), 0, 1))
def canal_d(x): return 12 + (canal(x) - bed(x) + 3) / SLOPE     # where the far bank reaches canal level


def ground(x, y, variant="canal"):
    b = bed(x)
    if y <= 0:                                   # the near bank, low so it never hides the river
        n = -y
        return b - 3 + .42 * max(0, n - 12) + 7 * fbm(x / 70 + 9, n / 70) * ss(14, 60, n)
    d = y
    e = b - 3 + SLOPE * clamp(d - 12, 0, 290) + .3 * max(0, d - 302)
    e += 9 * fbm(x / 70, d / 70) * ss(14, 60, d)
    ridge = 1 - abs(2 * fbm(x / 210 + 3, d / 170) - 1)          # sharp crests, soft saddles
    e += ss(330, 700, d) * (40 + 420 * ridge ** 2.2)
    if variant == "canal":
        # a cut-and-fill bench for the headrace, and a terrace where it ends
        cd, c = canal_d(x), canal(x)
        kx = ss(INTAKE_X - 30, INTAKE_X - 6, x) * (1 - ss(FOREBAY_X + 34, FOREBAY_X + 64, x))
        k = kx * (1 - ss(14, 52, abs(d - cd)))    # wide enough that the grid cannot staircase it
        # the last reach widens into a headpond (peaking) or the forebay yard (run-of-river)
        kp = (ss(POND_X0 + 70, POND_X0 + 94, x) * (1 - ss(FOREBAY_X + 30, FOREBAY_X + 54, x))
              * (1 - ss(40, 86, abs(d - cd))))
        e = lerp(e, c - 1, max(k, kp))
    return e


def heading(x):   # the headrace's direction at x, as a rotation about z
    return math.atan2(canal_d(x + 5) - canal_d(x - 5), 10)


# ── materials ────────────────────────────────────────────────────────────────────
def lin(h):
    c = [((h >> s) & 255) / 255 for s in (16, 8, 0)]
    return [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in c] + [1]


MATS = {}


def mat(name, hexcol, rough=.9, metal=0.0, alpha=1.0, vcol=None):
    if name in MATS: return MATS[name]
    m = bpy.data.materials.new(name)
    if m.node_tree is None: m.use_nodes = True     # always on from Blender 5
    p = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    p.inputs["Base Color"].default_value = lin(hexcol)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    if alpha < 1:
        p.inputs["Alpha"].default_value = alpha
        for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            try: setattr(m, attr, val)
            except Exception: pass
    if vcol:
        a = m.node_tree.nodes.new("ShaderNodeVertexColor")
        a.layer_name = vcol
        m.node_tree.links.new(a.outputs["Color"], p.inputs["Base Color"])
    MATS[name] = m
    return m


CONCRETE = lambda: mat("concrete", 0xB9B4A8)
WALL = lambda: mat("wall", 0xE4DDCF)
ROOF = lambda: mat("roof", 0x3E4A52, .7)
STEEL = lambda: mat("steel", 0x5C6770, .5, .4)
PIPE = lambda: mat("penstock", 0xA7ACAA, .45, .5)
OCHRE = lambda: mat("ochre", 0xC8923A, .6)
HUT = lambda: mat("hut", 0x8A6A4C)
WIRE = lambda: mat("conductor", 0xB8923A, .5, .3)
GLASS = lambda: mat("window", 0x2C3A44, .3)


def water(kind, alpha=1.0):   # the page animates every material whose name starts water_
    return mat("water_" + kind, 0x4E88A6, .15, 0.0, alpha)


# ── mesh helpers ─────────────────────────────────────────────────────────────────
COLL = bpy.data.collections.new("scheme")


def obj(name, verts, faces, material, uvs=None, mats=None, face_mats=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    if uvs:
        uv = me.uv_layers.new(name="UVMap")
        k = 0
        for f in me.polygons:
            for li in f.loop_indices:
                uv.data[li].uv = uvs[me.loops[li].vertex_index]
                k += 1
    for m in (mats or [material]): me.materials.append(m)
    if face_mats:
        for f, i in zip(me.polygons, face_mats): f.material_index = i
    me.update()
    o = bpy.data.objects.new(name, me)
    COLL.objects.link(o)
    return o


def bevel(o, w=.7):
    m = o.modifiers.new("bevel", "BEVEL")
    m.width, m.segments, m.limit_method = w, 1, "ANGLE"
    return o


def box(name, sx, sy, sz, material, x, y, zbase, rot=0.0, bev=.7):
    c, s = math.cos(rot), math.sin(rot)
    v = []
    for dz in (0, sz):
        for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            px, py = dx * sx / 2, dy * sy / 2
            v.append((x + px * c - py * s, y + px * s + py * c, zbase + dz))
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    o = obj(name, v, f, material)
    return bevel(o, bev) if bev else o


def gable(name, length, width, h, material, x, y, zbase, rot=0.0):
    c, s = math.cos(rot), math.sin(rot)
    pts = []
    for dx in (-length / 2, length / 2):
        for py, pz in ((-width / 2, 0), (width / 2, 0), (0, h)):
            pts.append((x + dx * c - py * s, y + dx * s + py * c, zbase + pz))
    f = [(0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)]
    return obj(name, pts, f, material)


def sweep(name, pts, profile, material, ulen=40.0):
    """Extrude a cross-section (across, up) along a path of (x, y, z) points."""
    v, uv, f = [], [], []
    s, n = 0.0, len(profile)
    for i, (x, y, z) in enumerate(pts):
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1
        nx, ny = -dy / L, dx / L
        if i: s += math.dist(pts[i - 1], pts[i])
        for j, (u, w) in enumerate(profile):
            v.append((x + nx * u, y + ny * u, z + w))
            uv.append((s / ulen, j / (n - 1)))
        if i:
            o0, o1 = (i - 1) * n, i * n
            f += [(o0 + j, o0 + j + 1, o1 + j + 1, o1 + j) for j in range(n - 1)]
    return obj(name, v, f, material, uvs=uv)


def tube(name, pts, r, material, seg=10):
    v, f = [], []
    for i, p in enumerate(pts):
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        t = [b[k] - a[k] for k in range(3)]
        L = math.sqrt(sum(c * c for c in t)) or 1
        t = [c / L for c in t]
        up = (0, 0, 1) if abs(t[2]) < .95 else (1, 0, 0)
        n1 = [up[1] * t[2] - up[2] * t[1], up[2] * t[0] - up[0] * t[2], up[0] * t[1] - up[1] * t[0]]
        L1 = math.sqrt(sum(c * c for c in n1))
        n1 = [c / L1 for c in n1]
        n2 = [t[1] * n1[2] - t[2] * n1[1], t[2] * n1[0] - t[0] * n1[2], t[0] * n1[1] - t[1] * n1[0]]
        for k in range(seg):
            q = 2 * math.pi * k / seg
            v.append(tuple(p[m] + r * (math.cos(q) * n1[m] + math.sin(q) * n2[m]) for m in range(3)))
        if i:
            o0, o1 = (i - 1) * seg, i * seg
            f += [(o0 + k, o0 + (k + 1) % seg, o1 + (k + 1) % seg, o1 + k) for k in range(seg)]
    return obj(name, v, f, material)


def anchor(name, x, y, z):
    e = bpy.data.objects.new(name, None)
    e.location = (x, y, z)
    COLL.objects.link(e)
    return e


# ── terrain ──────────────────────────────────────────────────────────────────────
NX, NY = int((X1 - X0) / STEP), int((YF - YN) / STEP)
GRASS, MEADOW, ROCK, SNOW, BANK, CUT = (lin(h)[:3] for h in (0x5C8A4E, 0x7C9A5A, 0x8C8778,
                                                         0xEEF3F5, 0xA8987A, 0x9A8466))


def terrain(variant):
    verts, cols = [], []
    for j in range(NY + 1):
        y = YN + j * STEP
        for i in range(NX + 1):
            x = X0 + i * STEP
            verts.append((x, y, ground(x, y, variant)))
    faces = [(j * (NX + 1) + i, j * (NX + 1) + i + 1, (j + 1) * (NX + 1) + i + 1, (j + 1) * (NX + 1) + i)
             for j in range(NY) for i in range(NX)]
    for x, y, z in verts:
        # slope from the two neighbours one step away, for where grass gives way to rock
        gx = (ground(x + STEP, y, variant) - ground(x - STEP, y, variant)) / (2 * STEP)
        gy = (ground(x, y + STEP, variant) - ground(x, y - STEP, variant)) / (2 * STEP)
        steep = math.hypot(gx, gy)
        c = [lerp(a, b, fbm(x / 120, y / 120)) for a, b in zip(GRASS, MEADOW)]
        if abs(y) < 18: c = list(BANK)
        if variant == "canal" and y > 0 and INTAKE_X - 12 < x < FOREBAY_X + 44 and abs(y - canal_d(x)) < 17:
            c = list(CUT)
        snow = 60 + 70 * fbm(x / 90 + 2, y / 90)
        if z > snow - 150 or steep > 1.05:
            c = [lerp(a, b, .85 if z > snow - 150 else .6) for a, b in zip(c, ROCK)]
        if z > snow: c = list(SNOW)
        cols.append(c + [1])
    o = obj("terrain__" + variant, verts, faces, mat("terrain", 0xFFFFFF, 1.0, vcol="Col"))
    ca = o.data.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    for k, c in enumerate(cols): ca.data[k].color = c
    for p in o.data.polygons: p.use_smooth = True
    return o


def skirt():
    """The cut faces: a soil band over bedrock, as the profile draws its cross-section."""
    v, f, fm = [], [], []
    def wall(p0, p1):
        g0, g1 = ground(*p0), ground(*p1)
        for top0, top1, bot0, bot1, m in ((g0, g1, g0 - 54, g1 - 54, 0), (g0 - 54, g1 - 54, BASE, BASE, 1)):
            k = len(v)
            v.extend([(p0[0], p0[1], top0), (p0[0], p0[1], bot0), (p1[0], p1[1], bot1), (p1[0], p1[1], top1)])
            f.append((k, k + 1, k + 2, k + 3)); fm.append(m)
    xs = [X0 + i * STEP for i in range(NX + 1)]
    ys = [YN + j * STEP for j in range(NY + 1)]
    for a, b in zip(xs, xs[1:]): wall((a, YN), (b, YN)); wall((b, YF), (a, YF))
    for a, b in zip(ys, ys[1:]): wall((X0, b), (X0, a)); wall((X1, a), (X1, b))
    k = len(v)
    v += [(X0, YN, BASE), (X1, YN, BASE), (X1, YF, BASE), (X0, YF, BASE)]
    f.append((k + 3, k + 2, k + 1, k)); fm.append(1)
    o = obj("skirt", v, f, None, mats=[mat("soil", 0x6B4F35), mat("bedrock", 0x4A3326)], face_mats=fm)
    import bmesh                        # weld the strips: unshared edges leave hairline cracks
    bm = bmesh.new(); bm.from_mesh(o.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=.01)
    bm.to_mesh(o.data); bm.free()
    return o


# ── the scheme ───────────────────────────────────────────────────────────────────
def build():
    for o in list(bpy.data.objects): bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.scene.collection.children.link(COLL)
    terrain("canal"); terrain("res"); skirt()

    # river, full length; the page widens it through the monsoon by scaling y
    sweep("river", [(x, 0, bed(x) + .6) for x in frange(X0, X1, 8)], [(-11, 0), (11, 0)], water("river"), 60)

    # headworks: weir across the river, gate piers, intake on the far bank
    wb = bed(WEIR_X)
    box("weir__canal", 12, 100, 24, CONCRETE(), WEIR_X, 0, wb - 8)
    for py in (-26, 0, 26): box(f"weir_pier{py}__canal", 6, 6, 12, CONCRETE(), WEIR_X, py, wb + 16)
    box("intake__canal", 22, 24, 22, CONCRETE(), INTAKE_X - 4, canal_d(INTAKE_X) - 16, canal(INTAKE_X) - 16)

    # headrace: a lined channel holding its level, in two runs so the peaking plant's
    # pond can take over the last reach where run-of-river keeps its forebay
    POND_IN = POND_X0 + 100
    U = [(-13, 5), (-13, 0), (13, 0), (13, 5)]
    for nm, a, b in (("canal_a__canal", INTAKE_X, POND_IN), ("canal_b__ror", POND_IN, FOREBAY_X)):
        pts = [(x, canal_d(x), canal(x) + .4) for x in frange(a, b, 8)]
        sweep(nm, pts, U, CONCRETE(), 40)
        sweep(nm.replace("canal_", "canal_water_"), [(x, y, z + 2.2) for x, y, z in pts],
              [(-11, 0), (11, 0)], water("canal"), 40)

    dsx = DESANDER_X + 50
    rot = heading(dsx)
    box("desander__canal", 86, 36, 22, CONCRETE(), dsx, canal_d(dsx), canal(dsx) - 18, rot)
    box("desander_water__canal", 78, 28, .6, water("still"), dsx, canal_d(dsx), canal(dsx) + 3.2, rot, 0)
    for k in (-1, 1):
        box(f"desander_wall{k}__canal", 2.4, 30, 5, CONCRETE(), dsx + k * 14 * math.cos(rot),
            canal_d(dsx) + k * 14 * math.sin(rot), canal(dsx) + 2, rot, 0)

    # forebay (run-of-river) or the daily pond (peaking), where the penstock begins
    fx = FOREBAY_X + 18
    box("forebay__ror", 40, 54, 24, CONCRETE(), fx, canal_d(fx) + 2, canal(FOREBAY_X) - 20, heading(fx))
    box("forebay_water__ror", 32, 46, .6, water("still"), fx, canal_d(fx) + 2, canal(FOREBAY_X) + 3.2, heading(fx), 0)
    px = (POND_IN + FOREBAY_X + 24) / 2
    pl, pw, prot, pc = FOREBAY_X + 24 - POND_IN, 64, heading(px), canal(FOREBAY_X)
    pcy = canal_d(px)
    for k, (sx, sy, ox, oy) in enumerate(((pl, 8, 0, -pw / 2), (pl, 8, 0, pw / 2),
                                          (8, pw, -pl / 2, 0), (8, pw, pl / 2, 0))):
        box(f"pond_wall{k}__pror", sx, sy, 28, CONCRETE(), px + ox * math.cos(prot) - oy * math.sin(prot),
            pcy + ox * math.sin(prot) + oy * math.cos(prot), pc - 2, prot)
    box("pond_water__pror", pl - 10, pw - 10, .6, water("pond"), px, pcy, 0, prot, 0)
    bpy.data.objects["pond_water__pror"].location.z = pc + 20
    anchor("pond_lo__pror", px, pcy, pc + 3)
    anchor("pond_hi__pror", px, pcy, pc + 23)

    # penstock: laid on the slope from the forebay to the powerhouse, on anchor blocks
    PHX, PHY = PH_X + PH_W * .3, 58.0
    phg = ground(PHX, PHY)
    sx0, sy0 = FOREBAY_X + 40, canal_d(FOREBAY_X + 40) - 6
    ex, ey = PHX - 62, PHY
    pen = []
    for i in range(29):
        t = i / 28
        x, y = lerp(sx0, ex, t), lerp(sy0, ey, t)
        z = lerp(canal(FOREBAY_X) - 4, phg + 14, t) if i in (0, 28) else ground(x, y) + 6
        pen.append((x, y, z))
    tube("penstock__canal", pen, 4.6, PIPE(), 12)
    for i in (5, 10, 15, 20, 24):
        x, y, z = pen[i]
        box(f"anchor_block{i}__canal", 16, 16, 12, CONCRETE(), x, y, z - 12, math.atan2(ey - sy0, ex - sx0))

    # powerhouse on the far bank, facing the viewer
    house("ph__canal", PHX, PHY, phg, 118, 64, 42)
    sweep("tail__canal", [(PHX + 52, PHY - 14, phg + 1), (PHX + 78, 26, bed(PHX + 78) + 1.2),
                          (PHX + 94, 0, bed(PHX + 94) + .7)], [(-7, 0), (7, 0)], water("tail"), 30)

    # switchyard, two towers, the line, and the meter the whole scheme exists to turn
    SYX, SYY = SWITCH_X + 48, 104.0
    syg = ground(SYX, SYY)
    box("yard_pad", 80, 58, 30, CONCRETE(), SYX, SYY, syg - 26)
    for k in (-1, 0, 1):
        box(f"yard_tx{k}", 12, 14, 14, STEEL(), SYX + k * 22, SYY + 10, syg + 4)
        box(f"yard_bush{k}", 2, 2, 8, STEEL(), SYX + k * 22, SYY + 10, syg + 18, 0, 0)
    for k in (-1, 1): box(f"yard_post{k}", 3, 3, 44, STEEL(), SYX + 24, SYY + k * 28, syg + 4, 0, 0)
    box("yard_beam", 3, 62, 3, STEEL(), SYX + 24, SYY, syg + 46, 0, 0)
    tops = [pylon("pylon_a", TOWER_X[0] + 20, SYY, 130), pylon("pylon_b", TOWER_X[1] + 46, SYY, 130)]
    MX = METER_X + 52
    mg = ground(MX, SYY)
    box("meter_post", 3, 3, 26, STEEL(), MX, SYY, mg, 0, 0)
    box("meter", 20, 14, 16, OCHRE(), MX, SYY, mg + 26)
    gable("meter_roof", 24, 18, 5, ROOF(), MX, SYY, mg + 42)
    supports = [(SYX + 24, syg + 46, 0)] + [(TOWER_X[0] + 20, tops[0], 1), (TOWER_X[1] + 46, tops[1], 1)]
    for dy, dz in ((-28, -14), (28, -14), (-22, -36), (22, -36)):
        pts = [(x, SYY + (dy * .9 if k == 0 else dy), (z + (dz + 14) * .3) if k == 0 else z + dz)
               for x, z, k in supports] + [(X1, SYY + dy, tops[1] + dz - 12)]
        wire = []
        for (xa, ya, za), (xb, yb, zb) in zip(pts, pts[1:]):
            for i in range(14):
                t = i / 14
                wire.append((lerp(xa, xb, t), lerp(ya, yb, t), lerp(za, zb, t) - 9 * (1 - (2 * t - 1) ** 2)))
        wire.append(pts[-1])
        tube(f"wire{dy}{dz}", wire, .8, WIRE(), 5)

    # gauging station: on the near bank for the river schemes, above the lake otherwise
    for nm, gx, gy in (("gauge__canal", 165, -44), ("gauge__res", 130, 214)):
        g = ground(gx, gy, "res" if nm.endswith("res") else "canal")
        box(nm, 26, 22, 22, HUT(), gx, gy, g - 4)
        gable(nm.replace("gauge", "gauge_roof"), 30, 26, 10, ROOF(), gx, gy, g + 18)
    box("staff_gauge__canal", 1.6, 1.6, 44, STEEL(), 196, -12, bed(196) - 6, 0, 0)

    # reservoir: dam across the valley, the lake it holds, a powerhouse at its toe
    crest = -DAM_CREST + 6
    sec = [(DAM_X - 74, -520), (DAM_X - 12, crest), (DAM_X + 12, crest), (DAM_X + 88, -520)]
    ya, yb = YN + 4, 340.0          # stops just inside the cut face, so the skirt covers what is underground
    v = [(x, ya, z) for x, z in sec] + [(x, yb, z) for x, z in sec]
    obj("dam__res", v, [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)], CONCRETE())
    box("dam_parapet__res", 4, yb - ya, 5, CONCRETE(), DAM_X - 10, (ya + yb) / 2, crest, 0, 0)
    lx1 = DAM_X - 30
    lake = obj("lake_top__res", [(X0, YN, 0), (lx1, YN, 0), (lx1, YF, 0), (X0, YF, 0)], [(0, 1, 2, 3)], water("lake", .9))
    lake.location.z = -FSL
    face_v, face_f = [], []
    def cut(p0, p1):
        g0, g1 = min(-FSL, ground(*p0, "res")), min(-FSL, ground(*p1, "res"))
        if g0 >= -FSL and g1 >= -FSL: return
        k = len(face_v)
        face_v.extend([(p0[0], p0[1], -FSL), (p0[0], p0[1], g0), (p1[0], p1[1], g1), (p1[0], p1[1], -FSL)])
        face_f.append((k, k + 1, k + 2, k + 3))
    for a in frange(X0, lx1, STEP): cut((a, YN), (min(a + STEP, lx1), YN))
    for a in frange(YN, YF, STEP): cut((X0, a), (X0, min(a + STEP, YF)))
    obj("lake_face__res", face_v, face_f, water("lakeface", .92))
    RPX, RPY = RES_PH_X + RES_PH_W * .55, 52.0
    rpg = ground(RPX, RPY, "res")
    house("ph__res", RPX, RPY, rpg, 90, 54, 34)
    tube("pipe__res", [(DAM_X + 50, RPY, -440), (lerp(DAM_X + 50, RPX - 48, .5), RPY, lerp(-440, rpg + 12, .6)),
                       (RPX - 48, RPY, rpg + 12)], 5.2, PIPE(), 12)
    sweep("tail__res", [(RPX + 46, RPY - 12, rpg + 1), (RPX + 72, 22, bed(RPX + 72) + 1.2),
                        (RPX + 88, 0, bed(RPX + 88) + .7)], [(-7, 0), (7, 0)], water("tail"), 30)

    # label anchors, one per hotspot and plant type
    mid = pen[14]
    A = {
        "basin": {"all": (150, 560, ground(150, 560) + 30)},
        "gauging_station": {"canal": (165, -44, ground(165, -44) + 44), "res": (130, 214, ground(130, 214, "res") + 44)},
        "headworks": {"canal": (WEIR_X, 0, wb + 36), "res": (DAM_X, 0, crest + 26)},
        "desander": {"canal": (dsx, canal_d(dsx), canal(dsx) + 16)},
        "headrace": {"canal": (660, canal_d(660), canal(660) + 12)},
        "forebay_surge": {"ror": (fx, canal_d(fx) + 2, canal(FOREBAY_X) + 18),
                          "pror": (px, pcy, pc + 40), "res": (DAM_X - 60, 40, -FSL + 16)},
        "penstock": {"canal": (mid[0], mid[1], mid[2] + 16), "res": (DAM_X + 24, RPY, -470)},
        "powerhouse": {"canal": (PHX, PHY, phg + 78), "res": (RPX + 46, RPY, rpg + 96)},
        "tailrace": {"canal": (PHX + 86, 14, bed(PHX + 86) + 12), "res": (RPX + 80, 12, bed(RPX + 80) + 12)},
        "switchyard": {"all": (SYX, SYY, syg + 60)},
        "transmission": {"all": (TOWER_X[0] + 20, SYY, tops[0] + 8)},
        "metering_point": {"all": (MX, SYY, mg + 52)},
    }
    for hid, per in A.items():
        for variant, p in per.items(): anchor(f"anchor__{hid}__{variant}", *p)

    # gross head: forebay water level carried across to a dimension over the tailwater,
    # upstream of the powerhouse where the valley is open (downstream is the switchyard)
    hx = PHX - 104
    top = canal(FOREBAY_X) + 3
    anchor("head_from__canal", fx, canal_d(fx) + 2, top)
    anchor("head_top__canal", hx, 0, top)
    anchor("head_bot__canal", hx, 0, bed(hx) + .7)
    rx = RPX + 92
    anchor("head_from__res", DAM_X - 40, 0, -FSL)
    anchor("head_top__res", rx, 0, -FSL)
    anchor("head_bot__res", rx, 0, bed(rx) + .7)


def frange(a, b, s):
    out, x = [], a
    while x < b - 1e-6:
        out.append(x); x += s
    return out + [b]


def house(name, x, y, g, L, W, H):
    base = name.split("__")[0]
    suf = "__" + name.split("__")[1]
    box(name, L + 8, W + 8, 48, CONCRETE(), x, y, g - 38)                     # plinth into the bank
    box(base + "_hall" + suf, L, W, H, WALL(), x, y, g + 10)
    gable(base + "_roof" + suf, L + 6, W + 8, 16, ROOF(), x, y, g + 10 + H)
    for k in range(3):                                                        # windows on the valley side
        box(f"{base}_win{k}{suf}", L / 8, 1.2, H * .42, GLASS(), x - L * .36 + k * L * .22, y - W / 2 - .4, g + 10 + H * .38, 0, 0)
    box(base + "_door" + suf, L / 7, 1.2, H * .55, ROOF(), x + L * .33, y - W / 2 - .4, g + 10, 0, 0)


def pylon(name, x, y, h):
    """A lattice tower: a tapering square frame, poked so the wireframe gets its bracing."""
    g = ground(x, y)
    rings = 7
    v, f = [], []
    for r in range(rings + 1):
        t = r / rings
        half = lerp(15, 4.5, t ** .8)
        z = g + h * t
        v += [(x - half, y - half, z), (x + half, y - half, z), (x + half, y + half, z), (x - half, y + half, z)]
        if r:
            o0, o1 = (r - 1) * 4, r * 4
            f += [(o0 + k, o0 + (k + 1) % 4, o1 + (k + 1) % 4, o1 + k) for k in range(4)]
    o = obj(name, v, f, STEEL())
    import bmesh
    bm = bmesh.new(); bm.from_mesh(o.data)
    bmesh.ops.poke(bm, faces=bm.faces[:])
    bm.to_mesh(o.data); bm.free()
    w = o.modifiers.new("lattice", "WIREFRAME")
    w.thickness, w.use_replace = 1.3, True
    for dz, span in ((-14, 64), (-36, 52)):
        box(name + f"_arm{dz}", 3, span, 2.6, STEEL(), x, y, g + h + dz, 0, 0)
    return g + h


# ── ambient occlusion baked into the terrain colours ──────────────────────────────
def bake_ao():
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 48
    if not sc.world: sc.world = bpy.data.worlds.new("world")
    sc.world.light_settings.distance = 70
    sc.render.bake.target = "VERTEX_COLORS"
    for variant, hide in (("canal", "__res"), ("res", "__canal")):
        for o in COLL.objects:
            o.hide_render = (o.name.endswith(hide) or o.name.endswith("__ror" if variant == "res" else "~")
                             or o.name.endswith("__pror" if variant == "res" else "~")
                             or (o.name.startswith("terrain__") and o.name != "terrain__" + variant))
        t = bpy.data.objects["terrain__" + variant]
        me = t.data
        ao = me.color_attributes.new("ao", "FLOAT_COLOR", "POINT")
        me.color_attributes.active_color = ao
        bpy.context.view_layer.update()
        for o in bpy.context.scene.objects: o.select_set(False)
        t.select_set(True)
        bpy.context.view_layer.objects.active = t
        bpy.ops.object.bake(type="AO", target="VERTEX_COLORS")
        col = me.color_attributes["Col"]
        for k in range(len(col.data)):
            a = me.color_attributes["ao"].data[k].color[0]
            f = .42 + .58 * a
            c = col.data[k].color
            col.data[k].color = (c[0] * f, c[1] * f, c[2] * f, 1)
        me.color_attributes.remove(me.color_attributes["ao"])
        me.color_attributes.active_color = me.color_attributes["Col"]
    for o in COLL.objects: o.hide_render = False


def export():
    want = dict(filepath=OUT, export_format="GLB", use_selection=False, export_apply=True,
                export_yup=True, export_normals=False, export_texcoords=True, export_materials="EXPORT",
                export_vertex_color="MATERIAL", export_cameras=False, export_lights=False,
                export_animations=False, export_extras=False, export_draco_mesh_compression_enable=False)
    have = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
    bpy.ops.export_scene.gltf(**{k: v for k, v in want.items() if k in have})
    print(f"wrote {OUT} ({os.path.getsize(OUT):,} bytes)")


def preview(path):
    """A Cycles still from the page's own opening camera, to look at the model before shipping it."""
    sc = bpy.context.scene
    keep = {"ror": ("__canal", "__ror"), "pror": ("__canal", "__pror"), "res": ("__res",)}[VARIANT]
    for o in COLL.objects:
        tagged = "__" in o.name and not o.name.startswith(("anchor__", "head_", "pond_lo", "pond_hi"))
        o.hide_render = tagged and not o.name.endswith(keep)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    sc.collection.objects.link(cam)
    cam.data.lens = 34
    cam.data.clip_end = 9000
    cam.location = (520, -1480, 640)
    tgt = bpy.data.objects.new("tgt", None); sc.collection.objects.link(tgt)
    tgt.location = (820, 250, -450)
    c = cam.constraints.new("TRACK_TO"); c.target, c.track_axis, c.up_axis = tgt, "TRACK_NEGATIVE_Z", "UP_Y"
    sc.camera = cam
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.2
    sun.rotation_euler = (math.radians(50), 0, math.radians(-35))
    sc.collection.objects.link(sun)
    sc.world.use_nodes = True
    sc.world.node_tree.nodes["Background"].inputs[0].default_value = lin(0xDCE7EA)
    sc.world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
    sc.render.resolution_x, sc.render.resolution_y = 1400, 820
    sc.cycles.samples = 24
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print("preview", path)


if __name__ == "__main__":
    build()
    bake_ao()
    export()
    if PREVIEW: preview(PREVIEW)
