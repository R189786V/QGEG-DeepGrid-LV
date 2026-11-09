"""Build a SYNTHETIC, representative Zimbabwean rural LV feeder for OpenDSS.

This is NOT a real ZETDC network. Equipment and conductor values are typical
of 11/0.42 kV pole-mounted rural distribution in southern Africa and are
approximate. All customers, loads and daily profiles are synthetic.
"""
import math
import random
from pathlib import Path

random.seed(42)  # same network every time
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "opendss" / "zimbabwe_rural"
OUT.mkdir(parents=True, exist_ok=True)

SPAN = 40.0  # metres between poles
coords = {"sourcebus": (0.0, 30.0), "lv_board": (0.0, 0.0)}
lines, loads = [], []
count = {"line": 0, "house": 0}


def add_line(bus1, bus2, code, length, phases=3):
    count["line"] += 1
    lines.append(f"New Line.L{count['line']:03d} bus1={bus1} bus2={bus2} "
                 f"phases={phases} linecode={code} length={length:.1f} units=m")


def backbone(prefix, start, n_poles, angle_deg, code):
    x0, y0 = coords[start]
    a = math.radians(angle_deg)
    prev, poles = start, []
    for k in range(1, n_poles + 1):
        bus = f"{prefix}_{k:02d}"
        coords[bus] = (x0 + k * SPAN * math.cos(a), y0 + k * SPAN * math.sin(a))
        add_line(prev, bus, code, SPAN)
        poles.append(bus)
        prev = bus
    return poles


def add_1ph(pole, name, kw, shape):
    ph = random.choices(["1", "2", "3"], weights=[0.40, 0.35, 0.25])[0]
    px, py = coords[pole]
    a, d = random.uniform(0, 2 * math.pi), random.uniform(15, 35)
    coords[name] = (px + d * math.cos(a), py + d * math.sin(a))
    add_line(f"{pole}.{ph}", f"{name}.{ph}", "svc_16", d, phases=1)
    loads.append(f"New Load.{name} bus1={name}.{ph} phases=1 kv=0.23 kw={kw} "
                 f"pf=0.95 model=1 daily={shape}")


def add_house(pole):
    count["house"] += 1
    add_1ph(pole, f"h{count['house']:03d}", round(random.uniform(0.6, 1.6), 2),
            random.choice(["house_a", "house_b", "house_c"]))


def add_3ph(pole, name, kw, pf, shape):
    px, py = coords[pole]
    coords[name] = (px + 20.0, py)
    add_line(pole, name, "abc_50", 20.0)
    loads.append(f"New Load.{name} bus1={name} phases=3 kv=0.4 kw={kw} "
                 f"pf={pf} model=1 daily={shape}")


# Feeder 1: village (east), 95 mm2 ABC backbone with two 50 mm2 laterals
f1 = backbone("f1", "lv_board", 14, 0, "abc_95")
for p in f1[1:]:
    add_house(p); add_house(p)
for p in backbone("f1a", "f1_06", 6, 90, "abc_50"):
    add_house(p)
for p in backbone("f1b", "f1_11", 5, -90, "abc_50"):
    add_house(p)

# Feeder 2: farm (south), long 95 mm2 ABC run to borehole and irrigation pumps
f2 = backbone("f2", "lv_board", 20, -90, "abc_95")
add_3ph("f2_10", "borehole_pump", 7.5, 0.85, "borehole")
for p in f2[16:]:
    add_house(p); add_house(p)
add_3ph("f2_20", "irrigation_pump", 22.0, 0.85, "irrigation")

# Feeder 3: business centre (north-west), 50 mm2 ABC
f3 = backbone("f3", "lv_board", 8, 135, "abc_50")
for i, p in enumerate(["f3_03", "f3_04", "f3_05"], start=1):
    add_1ph(p, f"shop{i}", 2.0, "shop")
add_1ph("f3_04", "clinic", 2.5, "clinic")
add_3ph("f3_04", "grinding_mill", 15.0, 0.80, "mill")
for p in f3[5:]:
    add_house(p); add_house(p)

# Synthetic hourly daily profiles (multipliers of each load's peak kW)
shapes = {
    "house_a": [0.25, 0.2, 0.2, 0.2, 0.25, 0.45, 0.7, 0.55, 0.35, 0.3, 0.3, 0.35,
                0.4, 0.35, 0.3, 0.35, 0.5, 0.8, 1.0, 0.95, 0.8, 0.6, 0.4, 0.3],
    "house_b": [0.3, 0.25, 0.2, 0.2, 0.2, 0.3, 0.55, 0.6, 0.4, 0.3, 0.25, 0.3,
                0.35, 0.3, 0.3, 0.3, 0.4, 0.6, 0.85, 1.0, 0.95, 0.75, 0.5, 0.35],
    "house_c": [0.2, 0.2, 0.2, 0.2, 0.2, 0.35, 0.5, 0.5, 0.55, 0.6, 0.65, 0.7,
                0.75, 0.7, 0.6, 0.55, 0.6, 0.75, 0.9, 1.0, 0.85, 0.6, 0.4, 0.3],
    "shop": [0.15] * 6 + [0.3, 0.6, 0.85, 0.9, 0.95, 1.0, 1.0, 0.95, 0.9, 0.9,
                          0.85, 0.8, 0.6, 0.3] + [0.15] * 4,
    "clinic": [0.4] * 8 + [0.9, 1, 1, 1, 1, 1, 1, 1, 0.9] + [0.5] * 7,
    "mill": [0] * 7 + [0.6, 1, 1, 0.9, 1, 0.7, 0.9, 1, 1, 0.8] + [0] * 7,
    "irrigation": [1] * 5 + [0.5] + [0] * 16 + [0.5, 1],
    "borehole": [0] * 5 + [1, 1, 1] + [0] * 8 + [1, 1] + [0] * 6,
}

linecodes = """! Approximate values for aluminium ABC (aerial bundled conductor)
New LineCode.abc_95 nphases=3 r1=0.41 x1=0.08 r0=1.64 x0=0.32 units=km normamps=200
New LineCode.abc_50 nphases=3 r1=0.82 x1=0.09 r0=3.28 x0=0.36 units=km normamps=140
New LineCode.svc_16 nphases=1 rmatrix=[3.82] xmatrix=[0.20] cmatrix=[0] units=km normamps=75
"""

master = """! SYNTHETIC representative Zimbabwean rural LV feeder.
! NOT a real ZETDC network. See README.md in this folder.
Clear
New Circuit.zim_rural basekv=11 pu=1.0 phases=3 bus1=sourcebus MVAsc3=50 MVAsc1=40
Redirect LineCodes.dss
Redirect LoadShapes.dss
New Transformer.TX1 phases=3 windings=2 buses=[sourcebus lv_board] conns=[delta wye] kvs=[11 0.42] kvas=[100 100] %r=1.2 xhl=4.0
Redirect Lines.dss
Redirect Loads.dss
Set voltagebases=[11 0.4]
Calcvoltagebases
Buscoords Buscoords.txt
Set mode=snapshot
Solve
"""

readme = """# Zimbabwe rural LV feeder (synthetic)

This is a SYNTHETIC, representative network. It is not a real ZETDC feeder.
It was generated by src/build_zim_feeder.py with a fixed random seed.

- Supply: 11 kV, 100 kVA 11/0.42 kV Dyn pole-mounted transformer
- F1 Village: 95 mm2 ABC backbone, two 50 mm2 laterals, 37 households
- F2 Farm: 800 m 95 mm2 ABC, 7.5 kW borehole pump, 8 houses, 22 kW irrigation pump
- F3 Business centre: 15 kW grinding mill, 3 shops, clinic, 6 houses
- Conductor and transformer parameters are approximate typical values
- Daily load profiles are synthetic hourly shapes, not measured data
"""


def write(name, text):
    (OUT / name).write_text(text, encoding="utf-8")


write("Master.dss", master)
write("LineCodes.dss", linecodes)
write("Lines.dss", "\n".join(lines) + "\n")
write("Loads.dss", "\n".join(loads) + "\n")
write("LoadShapes.dss", "\n".join(
    f"New Loadshape.{k} npts=24 interval=1 mult=({' '.join(str(v) for v in vals)})"
    for k, vals in shapes.items()) + "\n")
write("Buscoords.txt", "\n".join(f"{b}, {x:.1f}, {y:.1f}" for b, (x, y) in coords.items()) + "\n")
write("README.md", readme)

print(f"Built {len(lines)} lines, {len(loads)} loads, {count['house']} houses")
print(f"Files written to {OUT}")
