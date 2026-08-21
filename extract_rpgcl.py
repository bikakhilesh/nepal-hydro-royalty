"""Georeference RPGCL's 'Nepal Power Transmission Network Map 2040' and extract
the PLANNED transmission corridors that OpenStreetMap does not record.

The PDF prints its projection in a corner block and carries a regular coordinate
graticule as text, so the page georeferences exactly: PDF point -> MUTM Everest
1830 metres -> WGS84. Fitting the graticule gives 141.11 m/pt with a worst
residual under 25 m, which is sub-pixel on a 1:400,000 sheet.

Classification is by (stroke colour, stroke width), read from the legend's own
swatches by row position. Only classes whose pair is UNIQUE are exported:

    PROPOSED 400 kV        magenta (0.659,0,0.518) @ 2.88
    PROPOSED 220 kV        blue    (0,0.302,0.659) @ 1.92
    EXISTING/UC 400 kV     purple  (0.518,0,0.659) @ 3.12

The 132 kV family is deliberately NOT exported: '132 kV HTLS', 'PROPOSED 132 kV'
and 'Underconstruction_132kV' are all pure red at the same width, separated only
by dash pattern - and the PDF draws dashes as many short solid segments, so the
distinction is unrecoverable from the vector data. OSM already covers existing
132 kV. Guessing there would mislabel built infrastructure as planned.

Dashed corridors stay as their constituent segments rather than being stitched:
they then render as dashes, which is what 'proposed' should look like anyway.

    python extract_rpgcl.py [pdf]      -> np_rpgcl.json
"""
import json, os, re, sys
from collections import Counter

import pymupdf
import pyproj

CRS = ("+proj=tmerc +lat_0=0 +lon_0=84 +k=0.9999 +x_0=500000 +y_0=0 "
       "+a=6377276.345 +rf=300.8017 +units=m +no_defs")
TO_WGS = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)

# (rgb rounded to 3dp, stroke width rounded to 2dp) -> class
CLASSES = {
    ((0.659, 0.0, 0.518), 2.88): ("proposed_400", "Proposed 400 kV"),
    ((0.0, 0.302, 0.659), 1.92): ("proposed_220", "Proposed 220 kV"),
    ((0.518, 0.0, 0.659), 3.12): ("existing_400", "Existing / under construction 400 kV"),
}
BBOX = (79.9, 26.0, 88.4, 30.8)

# Substations are ESRIDefaultMarker '"' glyphs - small filled squares - coloured
# by build status exactly as the legend shows.
SUB_COLOURS = {0xFF0000: ("existing", "Existing substation"),
               0x00FF00: ("under_construction", "Substation under construction"),
               0x005CE6: ("future", "Future substation")}
LEGEND_BOX = (0, 1700, 900, 3456)          # x0,y0,x1,y1 - swatches live here

# Hydropower projects are '!' marker glyphs coloured by licence status. Only the
# PIPELINE is exported - IN OPERATION (red) is deliberately excluded because the
# DoED royalty register already covers operating plants, is three years newer than
# this 2022 sheet, and locates them better. These classes are the ones RMS cannot
# contain: projects that do not yet generate and so pay no royalty.
PLANT_CLASSES = {
    0x267300: ("under_construction", "Under construction", "built"),
    0x55FF00: ("construction_licence", "Construction licence", "licensed"),
    0x73B2FF: ("survey_licence", "Survey licence", "survey"),
    0xE8BEFF: ("construction_licence_application", "Construction licence application", "applied"),
    0xBEE8FF: ("survey_licence_application", "Survey licence application", "applied"),
    0xDF73FF: ("government_reserved", "Government reserved project", "applied"),
}
PLANT_EXCLUDE = {0xFF0000: "in_operation"}      # RMS is the better source for these


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


def substations(pg, fx, fy):
    """Marker glyph -> class by colour, position georeferenced, and the nearest
    plausible text label attached as a name where one sits close enough."""
    dd = pg.get_text("dict")
    # match whole label lines, not single words: a nearest-word search returns
    # fragments like "New" or "(New" out of "New Chabel (132/11)"
    labels = []
    for b in dd["blocks"]:
        for l in b.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in l.get("spans", [])).strip()
            if len(txt) < 3 or not re.search(r"[A-Za-z]{3}", txt):
                continue
            if txt.upper() == txt and len(txt) > 24:      # legend/heading text
                continue
            bb = l["bbox"]
            labels.append((txt, (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2))
    out = []
    for b in dd["blocks"]:
        for l in b.get("lines", []):
            for sp in l.get("spans", []):
                if (sp.get("text") or "").strip() != '"':
                    continue
                if "ESRIDefaultMarker" not in sp.get("font", ""):
                    continue
                cls = SUB_COLOURS.get(sp["color"])
                if not cls:
                    continue
                x = (sp["bbox"][0] + sp["bbox"][2]) / 2
                y = (sp["bbox"][1] + sp["bbox"][3]) / 2
                if LEGEND_BOX[0] <= x <= LEGEND_BOX[2] and LEGEND_BOX[1] <= y <= LEGEND_BOX[3]:
                    continue                      # a legend swatch, not a substation
                lo, la = TO_WGS.transform(fx(x), fy(y))
                if not (BBOX[0] <= lo <= BBOX[2] and BBOX[1] <= la <= BBOX[3]):
                    continue
                best, bd = None, 1e9
                for txt, lx, ly in labels:
                    d = ((lx - x) ** 2 + (ly - y) ** 2) ** 0.5
                    if d < bd:
                        bd, best = d, txt
                nm = None
                if best and bd < 46:
                    nm = re.sub(r"\s+", " ", best).strip(" ()!\"/")
                    nm = re.sub(r"\s*\((?:[\d/.,\s]+|[A-Za-z]{0,3})\)\s*$", "", nm).strip()
                out.append({"c": cls[0], "lo": round(lo, 5), "la": round(la, 5),
                            "n": nm or None, "_d": bd})
    # in dense areas several markers grab the same nearest label; keep it only for
    # the closest one rather than repeating a name across distinct substations
    best_for = {}
    for i, o in enumerate(out):
        if not o["n"]:
            continue
        j = best_for.get(o["n"])
        if j is None or o["_d"] < out[j]["_d"]:
            best_for[o["n"]] = i
    keep = set(best_for.values())
    for i, o in enumerate(out):
        if o["n"] and i not in keep:
            o["n"] = None
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


def pipeline_plants(pg, fx, fy):
    """Hydropower projects that are not yet operating - the pipeline RMS has no
    record of, because a project that does not generate pays no royalty."""
    dd = pg.get_text("dict")
    labels = _label_index(dd)
    out = []
    for b in dd["blocks"]:
        for l in b.get("lines", []):
            for sp in l.get("spans", []):
                if (sp.get("text") or "").strip() != "!":
                    continue
                if "ESRIDefaultMarker" not in sp.get("font", ""):
                    continue
                hit = PLANT_CLASSES.get(sp["color"])
                if not hit:
                    continue
                x = (sp["bbox"][0] + sp["bbox"][2]) / 2
                y = (sp["bbox"][1] + sp["bbox"][3]) / 2
                if x < 900 and y > 2400:
                    continue                       # legend swatch
                lo, la = TO_WGS.transform(fx(x), fy(y))
                if not (BBOX[0] <= lo <= BBOX[2] and BBOX[1] <= la <= BBOX[3]):
                    continue
                out.append({"c": hit[0], "tier": hit[2], "lo": round(lo, 5),
                            "la": round(la, 5), "mw": None, "_x": x, "_y": y})
    return _attach_names(out, labels, 40)


def main(path, out_path="np_grid_plan.json"):
    pg = pymupdf.open(path)[0]
    print(f"page {pg.rect.width:.0f} x {pg.rect.height:.0f} pt")
    fx, fy = graticule_affine(pg)

    feats, seen_keys = [], Counter()
    for d in pg.get_drawings():
        col = d.get("color")
        if not col:
            continue
        key = (tuple(round(c, 3) for c in col), round(d.get("width") or 0, 2))
        seen_keys[key] += 1
        hit = CLASSES.get(key)
        if not hit:
            continue
        pts = []
        for it in d["items"]:
            if it[0] == "l":
                pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
            elif it[0] == "c":
                pts += [(it[1].x, it[1].y), (it[4].x, it[4].y)]
        ll = []
        for x, y in pts:
            lo, la = TO_WGS.transform(fx(x), fy(y))
            if BBOX[0] <= lo <= BBOX[2] and BBOX[1] <= la <= BBOX[3]:
                ll.append([round(lo, 5), round(la, 5)])
        ded = [p for i, p in enumerate(ll) if i == 0 or p != ll[i - 1]]
        if len(ded) >= 2:
            feats.append({"c": hit[0], "pts": ded})

    plants = pipeline_plants(pg, fx, fy)
    pby = Counter(x["c"] for x in plants)
    print("\npipeline projects (operating plants excluded - RMS covers those):")
    for cid, label, tier in PLANT_CLASSES.values():
        print(f"  {label:36s} {pby.get(cid, 0):4d}")
    print(f"  with a capacity read off the label: {sum(1 for x in plants if x['mw'])}"
          f" | named: {sum(1 for x in plants if x['n'])} of {len(plants)}")

    subs = substations(pg, fx, fy)
    sby = Counter(x["c"] for x in subs)
    print("\nsubstations:")
    for cid, label in SUB_COLOURS.values():
        print(f"  {label:34s} {sby.get(cid, 0):4d}")
    named = sum(1 for x in subs if x["n"])
    print(f"  named (nearest label within 34 pt) {named} of {len(subs)}")

    by = Counter(f["c"] for f in feats)
    print("\nexported corridors:")
    for k, (cid, label) in CLASSES.items():
        print(f"  {label:38s} {by.get(cid, 0):5d} segments")
    print(f"\n(132 kV family deliberately skipped - red at one width across three legend classes)")
    json.dump({"lines": feats, "subs": subs, "plants": plants,
               "plant_labels": {cid: label for cid, label, _ in PLANT_CLASSES.values()},
               "labels": {cid: label for cid, label in CLASSES.values()},
               "sub_labels": {cid: label for cid, label in SUB_COLOURS.values()},
               "source": "RPGCL Nepal Power Transmission Network Map 2040",
               "crs": "MUTM Everest 1830, georeferenced from the printed graticule"},
              open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"wrote {out_path} ({os.path.getsize(out_path):,} bytes)")


DEFAULT_PDF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "nepal-tranmission-network-map-sep-4-2022-new.pdf")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF)
