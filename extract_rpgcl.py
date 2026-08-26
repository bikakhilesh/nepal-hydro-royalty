"""Georeference RPGCL's 'Nepal Power Transmission Network Map 2040' and extract
the corridors, substations and pipeline projects OpenStreetMap does not record.

The PDF prints its projection in a corner block and carries a regular coordinate
graticule as text, so the page georeferences exactly: PDF point -> MUTM Everest
1830 metres -> WGS84. Fitting the graticule gives 141.11 m/pt with a worst
residual under 25 m, which is sub-pixel on a 1:400,000 sheet.

The sheet is an ArcMap export, and every drawn path and every text/marker glyph
carries the name of the ArcMap layer it came from -- PyMuPDF exposes it as
`d["layer"]` on a drawing and `sp["layer"]` on a get_texttrace() span. That is
authoritative: a corridor's kV class and build status are read directly off the
layer name, not guessed from stroke colour and width. This matters because the
132 kV family (existing / under construction / proposed) draws in the same red
at the same width across all three states, and the ONLY thing that tells them
apart is which layer they were exported from. An earlier version of this script
matched on colour and width and could not tell 132 kV classes apart at all, so
it left the whole family out.

Within a layer, a corridor is still drawn twice at slightly different (colour,
width) -- ArcMap's usual halo-plus-centreline symbol -- so only the dominant
pair per layer is kept, exactly as the colour-matching version did; the smaller
pair is a duplicate trace of the same route, not a second corridor.

Substation and hydropower-project markers are read the same way, but via
get_texttrace() rather than get_text("dict") spans: the latter merges adjacent
glyphs into shared text spans and can drop a lone marker glyph entirely when
that happens -- confirmed against this sheet, where get_text("dict") silently
dropped one future substation near the eastern border that get_texttrace()
(one entry per glyph, always) still carried. Marker status still comes from
colour, since NEPAL HYDROPOWER PLANTS carries every pipeline tier in one layer
and only colour tells them apart -- but colour is now read within a single,
confirmed layer rather than searched for across the whole page.

Dashed corridors stay as their constituent segments rather than being stitched:
they then render as dashes, which is what 'proposed' should look like anyway.

    python extract_rpgcl.py [pdf]      -> np_grid_plan.json
"""
import json, os, re, sys
from collections import Counter

import pymupdf
import pyproj

CRS = ("+proj=tmerc +lat_0=0 +lon_0=84 +k=0.9999 +x_0=500000 +y_0=0 "
       "+a=6377276.345 +rf=300.8017 +units=m +no_defs")
TO_WGS = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
BBOX = (79.9, 26.0, 88.4, 30.8)

# ArcMap layer name -> (class id, human label). Two raw layers can share one
# class: 'HTLS' (High Temperature Low Sag) is a reconductored existing line,
# not a different build status, so it merges into the plain 'existing' class
# at its kV. 'EXISTING/UNDERCONSTRUCTION 400 kV' is RPGCL's own single layer
# for both statuses at 400 kV -- the source does not split it further, so
# neither do we.
LINE_LAYERS = {
    "EXISTING/UNDERCONSTRUCTION 400 kV": ("existing_400", "Existing / under construction 400 kV"),
    "PROPOSED 400kV":                    ("proposed_400", "Proposed 400 kV"),
    "cross_border_line_400kv":           ("cross_border_400", "Cross-border 400 kV (India interconnection)"),
    "EXISTING 220 kV":                   ("existing_220", "Existing 220 kV"),
    "220 kV HTLS":                       ("existing_220", "Existing 220 kV"),
    "Underconstruction_220kV":           ("uc_220", "Under construction 220 kV"),
    "PROPOSED 220kV":                    ("proposed_220", "Proposed 220 kV"),
    "EXISTING 132 kV":                   ("existing_132", "Existing 132 kV"),
    "132 kV HTLS":                       ("existing_132", "Existing 132 kV"),
    "Underconstruction_132kV":           ("uc_132", "Under construction 132 kV"),
    "PROPOSED 132 kV":                   ("proposed_132", "Proposed 132 kV"),
}

SUB_LAYERS = {"EXISTING_S/S": ("existing", "Existing substation"),
              "UNDER_CONSTRUCTION_S/S": ("under_construction", "Substation under construction"),
              "FUTURE_S/S": ("future", "Future substation")}

# Hydropower projects are '!' marker glyphs on the NEPAL HYDROPOWER PLANTS
# layer, coloured by licence status exactly as the legend shows. Only the
# PIPELINE is exported - IN OPERATION (red) is deliberately excluded because the
# DoED royalty register already covers operating plants, is three years newer
# than this 2022 sheet, and locates them better. These classes are the ones RMS
# cannot contain: projects that do not yet generate and so pay no royalty.
PLANT_LAYER = "NEPAL HYDROPOWER PLANTS"
PLANT_CLASSES = {
    0x267300: ("under_construction", "Under construction", "built"),
    0x55FF00: ("construction_licence", "Construction licence", "licensed"),
    0x73B2FF: ("survey_licence", "Survey licence", "survey"),
    0xE8BEFF: ("construction_licence_application", "Construction licence application", "applied"),
    0xBEE8FF: ("survey_licence_application", "Survey licence application", "applied"),
    0xDF73FF: ("government_reserved", "Government reserved project", "applied"),
}
PLANT_EXCLUDE = {0xFF0000: "in_operation"}      # RMS is the better source for these

# Small-hydro clusters: below-20-MW projects too close together at this scale to
# plot individually, so ArcMap plots one 'd' marker per cluster instead. One
# colour, no status split - the label's trailing number is the cluster's
# combined capacity, not one project's.
CLUSTER_LAYER = "HPP_CLUSTER_BELOW_20_MW"


def graticule_affine(pg):
    words = pg.get_text("words")
    nums = [w for w in words if re.fullmatch(r"\d{5,7}", w[4])]
    xs = [(float(w[4]), (w[0] + w[2]) / 2) for w in nums if w[1] < 60]
    ys = [(float(w[4]), (w[1] + w[3]) / 2) for w in nums if w[0] < 60]
    if len(xs) < 2 or len(ys) < 2:
        raise SystemExit("graticule labels not found - is this the RPGCL sheet?")

    def fit(pairs):
        n = len(pairs)
        sv, sp = sum(v for v, _ in pairs), sum(p for _, p in pairs)
        svp = sum(v * p for v, p in pairs); spp = sum(p * p for _, p in pairs)
        a = (n * svp - sv * sp) / (n * spp - sp * sp)
        return a, (sv - a * sp) / n

    ax, bx = fit(xs); ay, by = fit(ys)
    worst = max(max(abs(v - (ax * p + bx)) for v, p in xs),
                max(abs(v - (ay * p + by)) for v, p in ys))
    print(f"  graticule fit: {ax:.3f} m/pt E, {ay:.3f} m/pt N, worst residual {worst:.1f} m")
    if worst > 200:
        raise SystemExit("graticule fit too loose to trust")
    return (lambda x: ax * x + bx), (lambda y: ay * y + by)


def _rgb_key(color):
    """A get_texttrace() colour is a float triple 0..1; PLANT_CLASSES/SUB_LAYERS
    style dicts elsewhere in this codebase key on the 0xRRGGBB int, so match
    that convention here too."""
    r, g, b = (round(max(0, min(1, c)) * 255) for c in color)
    return (r << 16) | (g << 8) | b


def _in_bbox(lo, la):
    return BBOX[0] <= lo <= BBOX[2] and BBOX[1] <= la <= BBOX[3]


def corridors(pg, fx, fy):
    """Every transmission-line drawing, grouped by its own ArcMap layer name and
    filtered to that layer's dominant (colour, width) pair to drop the halo /
    duplicate-trace stroke ArcMap's line symbols draw alongside the real one."""
    draws = pg.get_drawings()
    by_layer = {}
    for d in draws:
        hit = LINE_LAYERS.get(d.get("layer"))
        if hit: by_layer.setdefault(d["layer"], []).append(d)

    feats = []
    report = []
    for raw_layer, items in by_layer.items():
        cid, label = LINE_LAYERS[raw_layer]
        style = Counter((tuple(round(c, 3) for c in (d["color"] or (0, 0, 0))),
                          round(d["width"] or 0, 2)) for d in items)
        dom, dom_n = style.most_common(1)[0]
        kept = 0
        for d in items:
            key = (tuple(round(c, 3) for c in (d["color"] or (0, 0, 0))), round(d["width"] or 0, 2))
            if key != dom: continue
            pts = []
            for it in d["items"]:
                if it[0] == "l":
                    pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
                elif it[0] == "c":
                    pts += [(it[1].x, it[1].y), (it[4].x, it[4].y)]
            ll = []
            for x, y in pts:
                lo, la = TO_WGS.transform(fx(x), fy(y))
                if _in_bbox(lo, la): ll.append([round(lo, 5), round(la, 5)])
            ded = [p for i, p in enumerate(ll) if i == 0 or p != ll[i - 1]]
            if len(ded) >= 2:
                feats.append({"c": cid, "pts": ded}); kept += 1
        report.append((raw_layer, cid, len(items), kept, dom_n, len(items) - dom_n))
    return feats, report


def substations(pg, fx, fy, labels):
    """Marker glyph -> class by its own layer name, position georeferenced, and
    the nearest plausible text label attached as a name where one sits close
    enough. Layer name replaces the old colour + legend-box-position heuristic
    entirely: a legend swatch lives on its own decorative layer, never on
    EXISTING_S/S / UNDER_CONSTRUCTION_S/S / FUTURE_S/S, so nothing needs to be
    excluded by position any more."""
    tt = pg.get_texttrace()
    out = []
    for sp in tt:
        if sp["font"] != "ESRIDefaultMarker": continue
        if "".join(chr(c[0]) for c in sp["chars"]) != '"': continue
        hit = SUB_LAYERS.get(sp.get("layer"))
        if not hit: continue
        b = sp["bbox"]; x, y = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        lo, la = TO_WGS.transform(fx(x), fy(y))
        if not _in_bbox(lo, la): continue
        best, bd = None, 1e9
        for txt, lx, ly in labels:
            d = ((lx - x) ** 2 + (ly - y) ** 2) ** 0.5
            if d < bd: bd, best = d, txt
        nm = None
        if best and bd < 46:
            nm = re.sub(r"\s+", " ", best).strip(" ()!\"/")
            nm = re.sub(r"\s*\((?:[\d/.,\s]+|[A-Za-z]{0,3})\)\s*$", "", nm).strip()
        out.append({"c": hit[0], "lo": round(lo, 5), "la": round(la, 5), "n": nm or None, "_d": bd})
    # in dense areas several markers grab the same nearest label; keep it only for
    # the closest one rather than repeating a name across distinct substations
    best_for = {}
    for i, o in enumerate(out):
        if not o["n"]: continue
        j = best_for.get(o["n"])
        if j is None or o["_d"] < out[j]["_d"]: best_for[o["n"]] = i
    keep = set(best_for.values())
    for i, o in enumerate(out):
        if o["n"] and i not in keep: o["n"] = None
        o.pop("_d", None)
    return out


def _label_index(dd):
    out = []
    for b in dd["blocks"]:
        for l in b.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in l.get("spans", [])).strip()
            if len(txt) < 3 or not re.search(r"[A-Za-z]{3}", txt):
                continue
            if txt.upper() == txt and len(txt) > 24:
                continue
            bb = l["bbox"]
            out.append((txt, (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2))
    return out


def _attach_names(items, labels, max_pt):
    """Nearest label line, then keep each name only for its closest claimant so a
    single label is not repeated across distinct markers in dense areas."""
    for it in items:
        best, bd = None, 1e9
        for txt, lx, ly in labels:
            d = ((lx - it["_x"]) ** 2 + (ly - it["_y"]) ** 2) ** 0.5
            if d < bd:
                bd, best = d, txt
        it["_d"] = bd
        it["n"] = None
        if best and bd < max_pt:
            nm = re.sub(r"\s+", " ", best).strip()
            # capacity first: stripping brackets before this would eat the ")"
            mm = re.search(r"\(\s*([\d.]+)\s*\)\s*$", nm)      # trailing "(MW)"
            if mm:
                try: it["mw"] = float(mm.group(1))
                except ValueError: pass
                nm = nm[:mm.start()].strip()
            nm = nm.strip(" ()!\"/")
            it["n"] = nm or None
    best_for = {}
    for i, it in enumerate(items):
        if not it["n"]: continue
        j = best_for.get(it["n"])
        if j is None or it["_d"] < items[j]["_d"]:
            best_for[it["n"]] = i
    keep = set(best_for.values())
    for i, it in enumerate(items):
        if it["n"] and i not in keep: it["n"] = None
        for k in ("_x", "_y", "_d"): it.pop(k, None)
    return items


def pipeline_plants(pg, fx, fy, labels):
    """Hydropower projects that are not yet operating - the pipeline RMS has no
    record of, because a project that does not generate pays no royalty."""
    tt = pg.get_texttrace()
    out = []
    for sp in tt:
        if sp["font"] != "ESRIDefaultMarker": continue
        if sp.get("layer") != PLANT_LAYER: continue
        if "".join(chr(c[0]) for c in sp["chars"]) != "!": continue
        hit = PLANT_CLASSES.get(_rgb_key(sp["color"]))
        if not hit: continue
        b = sp["bbox"]; x, y = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        lo, la = TO_WGS.transform(fx(x), fy(y))
        if not _in_bbox(lo, la): continue
        out.append({"c": hit[0], "tier": hit[2], "lo": round(lo, 5),
                    "la": round(la, 5), "mw": None, "_x": x, "_y": y})
    return _attach_names(out, labels, 40)


def clusters(pg, fx, fy, labels):
    """Below-20-MW projects plotted as one combined point per cluster, because at
    this map's scale several of them sit too close together to place separately.
    A cluster's number is the group's combined capacity, not one project's."""
    tt = pg.get_texttrace()
    out = []
    for sp in tt:
        if sp["font"] != "ESRIDefaultMarker": continue
        if sp.get("layer") != CLUSTER_LAYER: continue
        if "".join(chr(c[0]) for c in sp["chars"]) != "d": continue
        b = sp["bbox"]; x, y = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        lo, la = TO_WGS.transform(fx(x), fy(y))
        if not _in_bbox(lo, la): continue
        out.append({"lo": round(lo, 5), "la": round(la, 5), "mw": None, "_x": x, "_y": y})
    return _attach_names(out, labels, 40)


def main(path, out_path="np_grid_plan.json"):
    pg = pymupdf.open(path)[0]
    print(f"page {pg.rect.width:.0f} x {pg.rect.height:.0f} pt")
    fx, fy = graticule_affine(pg)

    dd = pg.get_text("dict")
    labels = _label_index(dd)

    feats, report = corridors(pg, fx, fy)
    print("\ncorridors, by their own ArcMap layer:")
    for raw_layer, cid, n, kept, dom_n, dropped in sorted(report, key=lambda r: -r[3]):
        note = f" (dropped {dropped} halo/duplicate-trace segments)" if dropped else ""
        print(f"  {raw_layer:38s} -> {cid:18s} {kept:5d} segments{note}")
    by = Counter(f["c"] for f in feats)
    labels_out = {cid: label for cid, label in LINE_LAYERS.values()}
    print("\nby class (both raw layers merged where one exists, e.g. HTLS):")
    for cid in dict.fromkeys(labels_out):     # preserves first-seen order, de-duplicated
        print(f"  {labels_out[cid]:44s} {by.get(cid, 0):5d} segments")

    plants = pipeline_plants(pg, fx, fy, labels)
    pby = Counter(x["c"] for x in plants)
    print("\npipeline projects (operating plants excluded - RMS covers those):")
    for cid, label, tier in PLANT_CLASSES.values():
        print(f"  {label:36s} {pby.get(cid, 0):4d}")
    print(f"  with a capacity read off the label: {sum(1 for x in plants if x['mw'])}"
          f" | named: {sum(1 for x in plants if x['n'])} of {len(plants)}")

    clu = clusters(pg, fx, fy, labels)
    print(f"\nsmall-hydro clusters (<20 MW each, combined capacity per point): {len(clu)}"
          f" | named: {sum(1 for x in clu if x['n'])} | with a combined MW read off the label:"
          f" {sum(1 for x in clu if x['mw'])}")

    subs = substations(pg, fx, fy, labels)
    sby = Counter(x["c"] for x in subs)
    print("\nsubstations:")
    for cid, label in SUB_LAYERS.values():
        print(f"  {label:34s} {sby.get(cid, 0):4d}")
    named = sum(1 for x in subs if x["n"])
    print(f"  named (nearest label within 34 pt) {named} of {len(subs)}")

    json.dump({"lines": feats, "subs": subs, "plants": plants, "clusters": clu,
               "plant_labels": {cid: label for cid, label, _ in PLANT_CLASSES.values()},
               "labels": labels_out,
               "sub_labels": {cid: label for cid, label in SUB_LAYERS.values()},
               "cluster_label": "Small-hydro cluster (several <20 MW projects at one point)",
               "source": "RPGCL Nepal Power Transmission Network Map 2040, printed 4 Sep 2022",
               "crs": "MUTM Everest 1830, georeferenced from the printed graticule"},
              open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\nwrote {out_path} ({os.path.getsize(out_path):,} bytes)")


DEFAULT_PDF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "nepal-tranmission-network-map-sep-4-2022-new.pdf")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF)
