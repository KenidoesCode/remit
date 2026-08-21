"""Deterministic merchant catalog.

Fictional brands. Not a real Razorpay customer, and it does not pretend to
be one. Seeded with a fixed RNG so the same catalog appears on every machine
-- an evaluation that cannot be reproduced is an anecdote.
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime

MERCHANTS = [
    # id, name, rating, free-ship-over (paise), base ship (paise), risk tier
    ("mch_strideworks", "Strideworks", 4.5, 300000, 9900, "low"),
    ("mch_kinetic",     "Kinetic Supply Co.", 4.3, 250000, 7900, "low"),
    ("mch_northbeam",   "Northbeam Electronics", 4.4, 200000, 5900, "low"),
    ("mch_wayfarer",    "Wayfarer Goods", 4.2, 350000, 11900, "medium"),
    ("mch_deskhaus",    "Deskhaus", 4.6, 500000, 14900, "low"),
    ("mch_lumenlab",    "Lumen Lab", 4.1, 150000, 4900, "low"),
]

# (category, merchant, [(name, price_rupees, mrp, margin_bps, premium, attrs)])
CATALOG: list[tuple[str, str, list[tuple]]] = [
    ("running shoes", "mch_strideworks", [
        ("Strideworks Velocity 4",        4599, 6499, 3200, True,  ["premium","cushioned","road","carbon-plate"]),
        ("Strideworks Velocity 4 Wide",   4799, 6699, 3200, True,  ["premium","cushioned","road","wide-fit"]),
        ("Strideworks Tempo Lite",        3299, 4499, 2900, False, ["lightweight","road","daily"]),
        ("Strideworks Trail Grip 2",      4899, 6899, 3400, True,  ["premium","trail","waterproof"]),
        ("Strideworks Daily 7",           2499, 3299, 2600, False, ["daily","road","beginner"]),
        ("Strideworks Marathon Elite",    8999, 11999, 3800, True, ["premium","carbon-plate","race"]),
        ("Strideworks Velocity 3",        3899, 6499, 2400, False, ["cushioned","road","previous-gen"]),
    ]),
    ("running shoes", "mch_kinetic", [
        ("Kinetic Pace Pro",              4299, 5999, 3000, True,  ["premium","cushioned","road"]),
        ("Kinetic Pace Pro Knit",         4999, 6999, 3300, True,  ["premium","knit","road"]),
        ("Kinetic Base Runner",           2199, 2999, 2500, False, ["daily","road","beginner"]),
        ("Kinetic Trail Scout",           3799, 4999, 2900, False, ["trail","grip"]),
        ("Kinetic Featherweight",         5499, 7499, 3500, True,  ["premium","lightweight","race"]),
    ]),
    ("fitness accessories", "mch_strideworks", [
        # A recovery slide is not a running shoe. It sat in the running-shoes
        # category and got selected for "buy running shoes under 5000".
        # FAILURES.md 2026-08-21 18:35.
        ("Strideworks Recover Slide",      999, 1499, 4200, False, ["recovery","slides"]),
    ]),
    ("fitness accessories", "mch_kinetic", [
        ("Kinetic Grip Socks (3 pack)",    299,  499, 5200, False, ["socks","running","anti-blister"]),
        ("Kinetic Performance Socks",      449,  699, 5000, True,  ["premium","socks","running"]),
        ("Kinetic Yoga Mat 6mm",          1299, 1899, 4400, False, ["yoga-mat","non-slip"]),
        ("Kinetic Yoga Mat Pro 8mm",      2199, 2999, 4600, True,  ["premium","yoga-mat","extra-thick"]),
        ("Kinetic Resistance Band Set",    799, 1199, 4800, False, ["bands","home-gym"]),
        ("Kinetic Foam Roller",            999, 1499, 4500, False, ["recovery","foam-roller"]),
        ("Kinetic Shaker 700ml",           399,  599, 5500, False, ["shaker","bpa-free"]),
        ("Kinetic Adjustable Dumbbell",   5999, 7999, 3600, True,  ["premium","dumbbell","adjustable"]),
        ("Kinetic Skipping Rope",          349,  549, 5300, False, ["rope","cardio"]),
        ("Kinetic Insole Support",         699,  999, 5000, False, ["insole","arch-support"]),
        ("Kinetic Compression Sleeve",     599,  899, 4900, False, ["compression","calf"]),
        ("Kinetic Gym Towel",              249,  399, 5600, False, ["towel","microfibre"]),
    ]),
    ("electronics accessories", "mch_northbeam", [
        ("Northbeam Pulse Buds",          2499, 3499, 3800, False, ["earbuds","bluetooth","anc"]),
        ("Northbeam Pulse Buds Pro",      4299, 5999, 4000, True,  ["premium","earbuds","anc","ldac"]),
        ("Northbeam Sprint Buds",         1799, 2499, 3600, False, ["earbuds","sport","sweatproof"]),
        ("Northbeam 65W GaN Charger",     1899, 2699, 3400, False, ["charger","gan","65w"]),
        ("Northbeam 100W GaN Charger",    2899, 3999, 3500, True,  ["premium","charger","gan","100w"]),
        ("Northbeam 20000mAh Power Bank", 2299, 3199, 3300, False, ["power-bank","fast-charge"]),
        ("Northbeam USB-C Cable 2m",       499,  799, 5000, False, ["cable","usb-c","braided"]),
        ("Northbeam Ergo Mouse",          1699, 2399, 3700, False, ["mouse","wireless","ergonomic"]),
        ("Northbeam Mech Keyboard 75",    4999, 6999, 3600, True,  ["premium","keyboard","mechanical"]),
        ("Northbeam 1080p Webcam",        2199, 2999, 3400, False, ["webcam","1080p"]),
        ("Northbeam Buds Case",            399,  599, 5400, False, ["case","earbuds-accessory"]),
        ("Northbeam Cable Organiser",      299,  499, 5600, False, ["organiser","desk"]),
    ]),
    ("travel accessories", "mch_wayfarer", [
        ("Wayfarer Transit 30L",          3499, 4999, 3200, False, ["backpack","30l","laptop-sleeve"]),
        ("Wayfarer Transit Pro 35L",      5299, 7299, 3500, True,  ["premium","backpack","35l","water-resistant"]),
        ("Wayfarer Cabin Roller",         6999, 9499, 3300, True,  ["premium","luggage","cabin"]),
        ("Wayfarer Duffel 45L",           2899, 3999, 3100, False, ["duffel","45l"]),
        ("Wayfarer Packing Cubes (4)",     999, 1499, 4700, False, ["packing-cubes","organiser"]),
        ("Wayfarer Neck Pillow",           899, 1299, 4800, False, ["neck-pillow","memory-foam"]),
        ("Wayfarer Toiletry Kit",         1199, 1699, 4500, False, ["toiletry","travel"]),
        ("Wayfarer Rain Cover",            599,  899, 5100, False, ["rain-cover","backpack-accessory"]),
    ]),
    ("home office", "mch_deskhaus", [
        ("Deskhaus Riser Solid Oak",      3999, 5499, 3600, True,  ["premium","monitor-stand","oak"]),
        ("Deskhaus Riser Basic",          1799, 2499, 3200, False, ["monitor-stand","steel"]),
        ("Deskhaus Task Lamp",            2499, 3499, 3800, False, ["lamp","adjustable"]),
        ("Deskhaus Task Lamp Pro",        4199, 5799, 4000, True,  ["premium","lamp","cri-95"]),
        ("Deskhaus Footrest",             1499, 1999, 4200, False, ["footrest","ergonomic"]),
        ("Deskhaus Cable Tray",            999, 1399, 4600, False, ["cable-tray","under-desk"]),
        ("Deskhaus Desk Mat XL",          1299, 1799, 4400, False, ["desk-mat","xl"]),
        ("Deskhaus Laptop Stand",         1999, 2699, 3900, False, ["laptop-stand","aluminium"]),
    ]),
    ("personal care", "mch_lumenlab", [
        ("Lumen Lab SPF50 Sunscreen",      699,  999, 5200, False, ["sunscreen","spf50","non-greasy"]),
        ("Lumen Lab Sport Sunscreen",      849, 1199, 5300, True,  ["premium","sunscreen","sweat-resistant"]),
        ("Lumen Lab Recovery Balm",        599,  899, 5400, False, ["balm","muscle-recovery"]),
        ("Lumen Lab Foot Cream",           449,  649, 5500, False, ["foot-cream","runners"]),
        ("Lumen Lab Body Wash",            399,  599, 5600, False, ["body-wash"]),
        ("Lumen Lab Trimmer T2",          1899, 2599, 3800, False, ["trimmer","cordless"]),
        ("Lumen Lab Anti-Chafe Stick",     549,  799, 5400, False, ["anti-chafe","running"]),
    ]),
]

# (from, to, kind, reason, strength)
RELATIONS = [
    ("Strideworks Velocity 4", "Kinetic Grip Socks (3 pack)", "cross_sell",
     "anti-blister socks sized for the Velocity's narrow toe box", 0.86),
    ("Strideworks Velocity 4", "Kinetic Performance Socks", "cross_sell",
     "cushioned running socks that pair with the Velocity's fit", 0.78),
    ("Strideworks Velocity 4", "Kinetic Insole Support", "cross_sell",
     "arch support for the Velocity's neutral footbed", 0.62),
    ("Strideworks Velocity 4", "Lumen Lab Anti-Chafe Stick", "cross_sell",
     "commonly bought with new running shoes for long runs", 0.48),
    ("Strideworks Velocity 4", "Strideworks Marathon Elite", "upsell",
     "carbon-plated race version of the same last", 0.55),
    ("Strideworks Velocity 4", "Kinetic Foam Roller", "cross_sell",
     "recovery roller for higher weekly mileage", 0.41),
    ("Kinetic Pace Pro", "Kinetic Grip Socks (3 pack)", "cross_sell",
     "anti-blister socks that match the Pace Pro fit", 0.83),
    ("Kinetic Pace Pro", "Kinetic Pace Pro Knit", "upsell",
     "knit upper version, more breathable for summer running", 0.58),
    ("Northbeam Pulse Buds", "Northbeam Buds Case", "cross_sell",
     "protective case sized for the Pulse Buds", 0.80),
    ("Northbeam Pulse Buds", "Northbeam Pulse Buds Pro", "upsell",
     "adds LDAC and stronger ANC on the same driver", 0.66),
    ("Northbeam Pulse Buds", "Northbeam USB-C Cable 2m", "cross_sell",
     "braided charging cable for the buds case", 0.52),
    ("Wayfarer Transit 30L", "Wayfarer Packing Cubes (4)", "cross_sell",
     "cubes cut to the Transit's main compartment", 0.79),
    ("Wayfarer Transit 30L", "Wayfarer Rain Cover", "cross_sell",
     "rain cover sized for a 30L pack", 0.71),
    ("Wayfarer Transit 30L", "Wayfarer Transit Pro 35L", "upsell",
     "water-resistant shell and 5L more capacity", 0.60),
    ("Deskhaus Riser Basic", "Deskhaus Riser Solid Oak", "upsell",
     "solid oak version, same height, better finish", 0.57),
    ("Deskhaus Riser Basic", "Deskhaus Cable Tray", "cross_sell",
     "keeps the desk clear under the riser", 0.68),
    ("Deskhaus Task Lamp", "Deskhaus Task Lamp Pro", "upsell",
     "CRI-95 version for accurate colour on screen work", 0.54),
    ("Kinetic Yoga Mat 6mm", "Kinetic Yoga Mat Pro 8mm", "upsell",
     "8mm version for joint comfort on hard floors", 0.59),
    ("Kinetic Yoga Mat 6mm", "Kinetic Gym Towel", "cross_sell",
     "microfibre towel for hot sessions", 0.64),
]


def seed(db: sqlite3.Connection, now: datetime, rng_seed: int = 20260821) -> dict:
    rng = random.Random(rng_seed)
    db.execute("INSERT INTO catalog_versions (created_at, note) VALUES (?,?)",
               (now.isoformat(), "initial seed"))
    v = db.execute("SELECT MAX(version) v FROM catalog_versions").fetchone()["v"]

    for m in MERCHANTS:
        db.execute("INSERT OR REPLACE INTO merchants (merchant_id, name, rating,"
                   " free_ship_over_paise, base_ship_paise, risk_tier)"
                   " VALUES (?,?,?,?,?,?)", m)

    by_name: dict[str, str] = {}
    n = 0
    for category, merchant_id, items in CATALOG:
        for name, price, mrp, margin, premium, attrs in items:
            pid = "prd_%04d" % n
            by_name[name] = pid
            rating = round(rng.uniform(3.8, 4.9), 1)
            reviews = rng.randint(40, 3200)
            inv = rng.randint(3, 90)
            ship_days = rng.choice([1, 2, 2, 3, 3, 4, 5])
            db.execute(
                "INSERT OR REPLACE INTO products (product_id, merchant_id, name,"
                " category, subcategory, price_paise, mrp_paise, margin_bps, rating,"
                " reviews, inventory, attributes, premium, ship_days, catalog_version,"
                " active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (pid, merchant_id, name, category, None, price * 100, mrp * 100,
                 margin, rating, reviews, inv, json.dumps(attrs), int(premium),
                 ship_days, v))
            n += 1

    # Colourways. Real commerce catalogs are mostly variants, and a search that
    # returns eight near-identical rows is a different ranking problem from one
    # that returns eight distinct products -- the eval should face the real shape.
    COLOURS = ["Slate", "Ember", "Mist", "Ink"]
    variant_of = [nm for _, _, items in CATALOG for nm, *_ in items
                  if any(k in nm for k in ("Velocity 4", "Pace Pro", "Transit",
                                           "Pulse Buds", "Yoga Mat", "Riser"))]
    for base in variant_of:
        row = db.execute("SELECT * FROM products WHERE name=?", (base,)).fetchone()
        if row is None:
            continue
        for c in COLOURS[:rng.randint(3, 4)]:
            pid = "prd_%04d" % n
            db.execute(
                "INSERT OR REPLACE INTO products (product_id, merchant_id, name,"
                " category, subcategory, price_paise, mrp_paise, margin_bps, rating,"
                " reviews, inventory, attributes, premium, ship_days, catalog_version,"
                " active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (pid, row["merchant_id"], f"{base} - {c}", row["category"], c,
                 row["price_paise"], row["mrp_paise"], row["margin_bps"],
                 round(max(3.6, row["rating"] - rng.uniform(0, 0.4)), 1),
                 rng.randint(20, 900), rng.randint(0, 40), row["attributes"],
                 row["premium"], rng.choice([2, 3, 4]), v))
            n += 1

    for src, dst, kind, reason, strength in RELATIONS:
        a, b = by_name.get(src), by_name.get(dst)
        if a and b:
            db.execute("INSERT OR REPLACE INTO relations (product_id, related_id,"
                       " kind, reason, strength) VALUES (?,?,?,?,?)",
                       (a, b, kind, reason, strength))
    # Category-level merchandising. Hand-written pairs cover the hero products,
    # but a real catalog attaches accessories to a whole category -- every
    # running shoe takes socks, every backpack takes a rain cover. Without this
    # the revenue engine simply does nothing for 80% of the catalog, which
    # showed up as a 0.10 attach rate in the first experiment run.
    # (name-fragment of accessory, kind, reason template, strength)
    CATEGORY_ATTACH: dict[str, list[tuple[str, str, str, float]]] = {
        "running shoes": [
            ("Kinetic Grip Socks", "cross_sell",
             "anti-blister socks, the most common pairing with new running shoes", 0.74),
            ("Kinetic Insole Support", "cross_sell",
             "arch support for higher weekly mileage", 0.55),
            ("Lumen Lab Anti-Chafe Stick", "cross_sell",
             "prevents chafing on long runs in new shoes", 0.44),
            ("Lumen Lab Foot Cream", "cross_sell",
             "recovery cream for runners breaking in a new pair", 0.38),
        ],
        "fitness accessories": [
            ("Kinetic Gym Towel", "cross_sell", "microfibre towel for the session", 0.58),
            ("Kinetic Shaker 700ml", "cross_sell", "hydration for the same workout", 0.49),
            ("Lumen Lab Recovery Balm", "cross_sell", "post-session muscle recovery", 0.41),
        ],
        "electronics accessories": [
            ("Northbeam USB-C Cable 2m", "cross_sell",
             "braided charging cable that fits this device", 0.56),
            ("Northbeam Cable Organiser", "cross_sell",
             "keeps the desk tidy around it", 0.40),
        ],
        "travel accessories": [
            ("Wayfarer Packing Cubes", "cross_sell",
             "organises the main compartment", 0.62),
            ("Wayfarer Neck Pillow", "cross_sell", "for the same trip", 0.45),
            ("Lumen Lab Sport Sunscreen", "cross_sell",
             "travel-sized sunscreen for the trip", 0.36),
        ],
        "home office": [
            ("Deskhaus Cable Tray", "cross_sell", "hides the cables underneath", 0.57),
            ("Deskhaus Desk Mat XL", "cross_sell", "finishes the desk surface", 0.48),
        ],
        "personal care": [
            ("Lumen Lab Body Wash", "cross_sell", "commonly bought together", 0.37),
        ],
    }
    name_to_id = {r["name"]: r["product_id"] for r in db.execute(
        "SELECT name, product_id FROM products")}
    for row in db.execute("SELECT product_id, name, category, price_paise FROM products"):
        for frag, kind, reason, strength in CATEGORY_ATTACH.get(row["category"], []):
            match = next((pid for nm, pid in name_to_id.items()
                          if nm.startswith(frag)), None)
            if not match or match == row["product_id"]:
                continue
            acc = db.execute("SELECT price_paise FROM products WHERE product_id=?",
                             (match,)).fetchone()
            # Never merchandise an "accessory" that costs more than the anchor.
            if acc and acc["price_paise"] >= row["price_paise"]:
                continue
            db.execute("INSERT OR IGNORE INTO relations (product_id, related_id,"
                       " kind, reason, strength) VALUES (?,?,?,?,?)",
                       (row["product_id"], match, kind, reason, strength))

    # Variants inherit their base product's accessory relations. A colourway of
    # the Velocity 4 takes the same socks. Without this, ranking picking a
    # variant silently kills every cross-sell -- which is exactly what happened
    # on the first end-to-end run (see FAILURES.md).
    bases = {r["name"]: r["product_id"] for r in db.execute(
        "SELECT name, product_id FROM products")}
    for name, pid in list(bases.items()):
        root = name.split(" - ")[0]
        if root == name:
            continue
        rid = bases.get(root)
        if not rid:
            continue
        for rel in db.execute("SELECT * FROM relations WHERE product_id=?", (rid,)):
            db.execute("INSERT OR REPLACE INTO relations (product_id, related_id,"
                       " kind, reason, strength) VALUES (?,?,?,?,?)",
                       (pid, rel["related_id"], rel["kind"], rel["reason"],
                        rel["strength"]))
    # "Velocity 4 Wide" is a distinct SKU, not a colourway, but takes the same
    # accessories. Same for the Pro/Knit siblings.
    SIBLINGS = [("Strideworks Velocity 4 Wide", "Strideworks Velocity 4"),
                ("Kinetic Pace Pro Knit", "Kinetic Pace Pro"),
                ("Wayfarer Transit Pro 35L", "Wayfarer Transit 30L"),
                ("Northbeam Pulse Buds Pro", "Northbeam Pulse Buds"),
                ("Kinetic Yoga Mat Pro 8mm", "Kinetic Yoga Mat 6mm"),
                ("Deskhaus Riser Solid Oak", "Deskhaus Riser Basic")]
    for child, parent in SIBLINGS:
        cpid, ppid = bases.get(child), bases.get(parent)
        if not (cpid and ppid):
            continue
        for rel in db.execute(
                "SELECT * FROM relations WHERE product_id=? AND kind='cross_sell'",
                (ppid,)):
            db.execute("INSERT OR REPLACE INTO relations (product_id, related_id,"
                       " kind, reason, strength) VALUES (?,?,?,?,?)",
                       (cpid, rel["related_id"], rel["kind"], rel["reason"],
                        rel["strength"]))

    return {"products": n, "merchants": len(MERCHANTS),
            "relations": len(RELATIONS), "catalog_version": v,
            "by_name": by_name}
