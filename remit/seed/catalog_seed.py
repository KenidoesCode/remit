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
    ("mch_dailymart",   "Daily Mart", 4.0, 49900, 3900, "low"),
    ("mch_freshcart",   "Freshcart Grocers", 4.2, 39900, 2900, "low"),
    ("mch_medipoint",   "Medipoint Pharmacy", 4.5, 29900, 3900, "medium"),
    ("mch_cellar",      "The Cellar", 4.3, 250000, 14900, "high"),
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
    # ---------------------------------------------------------------- everyday
    # A catalog with six categories abstains on almost anything a real person
    # types. Breadth here is not decoration: an agent that answers "I do not
    # stock that" to "buy chips" has not been tested against its actual input
    # distribution. Prices are Indian retail, roughly, in rupees.
    ("groceries", "mch_freshcart", [
        ("Freshcart Salted Potato Chips 150g",  60,   80, 3400, False, ["chips","snack","salted"]),
        ("Freshcart Masala Chips 150g",         65,   85, 3400, False, ["chips","snack","masala"]),
        ("Freshcart Cream & Onion Chips 150g",  65,   85, 3400, False, ["chips","snack"]),
        ("Freshcart Nachos 200g",              120,  150, 3600, False, ["chips","nachos","snack"]),
        ("Freshcart Salted Peanuts 500g",      140,  180, 3200, False, ["namkeen","snack"]),
        ("Freshcart Mixture Namkeen 400g",     110,  140, 3300, False, ["namkeen","snack"]),
        ("Freshcart Digestive Biscuits 250g",   85,  110, 3000, False, ["biscuit","snack"]),
        ("Freshcart Choco Cream Biscuits",      45,   60, 3100, False, ["biscuit","snack"]),
        ("Freshcart Instant Noodles (8 pack)",  120, 160, 2800, False, ["noodles","instant"]),
        ("Freshcart Dark Chocolate 90g",       180,  220, 3800, True,  ["premium","chocolate"]),
        ("Freshcart Milk Chocolate 90g",       110,  140, 3400, False, ["chocolate"]),
        ("Freshcart Basmati Rice 5kg",         690,  850, 2200, False, ["rice","staple"]),
        ("Freshcart Atta Whole Wheat 5kg",     280,  340, 2000, False, ["atta","flour","staple"]),
        ("Freshcart Toor Dal 1kg",             180,  220, 2100, False, ["dal","staple"]),
        ("Freshcart Sunflower Cooking Oil 1L", 165,  199, 1900, False, ["cooking oil","staple"]),
        ("Freshcart Cold Pressed Groundnut Oil 1L", 420, 520, 2900, True, ["premium","cooking oil"]),
        ("Freshcart Sugar 1kg",                 55,   70, 1600, False, ["sugar","staple"]),
        ("Freshcart Iodised Salt 1kg",          28,   35, 1500, False, ["salt","staple"]),
        ("Freshcart Penne Pasta 500g",         120,  150, 3000, False, ["pasta"]),
        ("Freshcart Corn Flakes 475g",         290,  360, 3200, False, ["cereal","breakfast"]),
    ]),
    ("beverages", "mch_freshcart", [
        ("Freshcart Orange Juice 1L",          130,  160, 3000, False, ["juice","orange"]),
        ("Freshcart Mixed Fruit Juice 1L",     120,  150, 3000, False, ["juice"]),
        ("Freshcart Apple Juice 1L",           140,  170, 3100, False, ["juice","apple"]),
        ("Freshcart Cold Pressed Juice 500ml", 220,  260, 3900, True,  ["premium","juice"]),
        ("Freshcart Tender Coconut Water 200ml",45,   60, 3400, False, ["coconut water"]),
        ("Daily Mart Cola 750ml",               45,   55, 2700, False, ["cola","soft drink"]),
        ("Daily Mart Lemon Soda 750ml",         45,   55, 2700, False, ["soda","soft drink"]),
        ("Daily Mart Mineral Water 1L (6 pack)",120, 150, 2400, False, ["drinking water"]),
        ("Daily Mart Energy Drink 250ml",       125, 150, 3600, False, ["energy drink"]),
        ("Freshcart Filter Coffee 500g",       420,  520, 3500, True,  ["premium","coffee"]),
        ("Freshcart Instant Coffee 100g",      280,  340, 3300, False, ["coffee"]),
        ("Freshcart Assam Tea 500g",           260,  320, 3100, False, ["tea"]),
        ("Freshcart Green Tea (50 bags)",      240,  300, 3400, False, ["tea","green tea"]),
    ]),
    ("household", "mch_dailymart", [
        ("Daily Mart Detergent Powder 2kg",    290,  360, 2600, False, ["detergent","laundry"]),
        ("Daily Mart Liquid Detergent 1L",     240,  300, 2900, False, ["detergent","laundry"]),
        ("Daily Mart Dishwash Gel 750ml",      145,  180, 2800, False, ["dishwash"]),
        ("Daily Mart Floor Cleaner 1L",        185,  230, 2700, False, ["floor cleaner"]),
        ("Daily Mart Toilet Cleaner 500ml",    110,  140, 3000, False, ["toilet cleaner"]),
        ("Daily Mart Garbage Bags (90 pcs)",   190,  240, 3300, False, ["garbage bag"]),
        ("Daily Mart Facial Tissue (200 pulls)",95, 120, 3100, False, ["tissue"]),
        ("Daily Mart Toilet Paper (8 rolls)",  260,  320, 3000, False, ["toilet paper"]),
        ("Daily Mart Microfibre Mop",          540,  680, 3700, False, ["mop","cleaning"]),
        ("Daily Mart Scrub Pads (6 pcs)",       70,   90, 3400, False, ["scrub","cleaning"]),
    ]),
    ("personal care", "mch_dailymart", [
        ("Daily Mart Condoms (10 pack)",       210,  260, 4200, False, ["condom","contraceptive"]),
        ("Daily Mart Ultra Thin Condoms (12)", 320,  390, 4400, True,  ["premium","condom","contraceptive"]),
        ("Daily Mart Sanitary Pads XL (30)",   285,  350, 3600, False, ["sanitary pad","feminine hygiene"]),
        ("Daily Mart Tampons (16 pcs)",        330,  400, 3800, False, ["tampon","feminine hygiene"]),
        ("Daily Mart Twin Blade Razor (5)",    180,  220, 3900, False, ["razor","shaving"]),
        ("Daily Mart Shaving Foam 200ml",      190,  240, 3700, False, ["shaving"]),
        ("Daily Mart Toothpaste 200g",         115,  145, 3200, False, ["toothpaste","oral care"]),
        ("Daily Mart Toothbrush (4 pack)",     140,  180, 3800, False, ["toothbrush","oral care"]),
        ("Daily Mart Bath Soap (4 pack)",      160,  200, 3300, False, ["soap"]),
        ("Daily Mart Handwash Refill 750ml",   170,  210, 3400, False, ["handwash"]),
    ]),
    ("baby care", "mch_dailymart", [
        ("Daily Mart Baby Diapers M (56)",     780,  950, 2900, False, ["diaper","baby"]),
        ("Daily Mart Baby Diapers L (48)",     820, 1000, 2900, False, ["diaper","baby"]),
        ("Daily Mart Baby Wipes (72 x 3)",     380,  460, 3200, False, ["baby wipes","baby"]),
        ("Daily Mart Baby Lotion 400ml",       320,  390, 3500, False, ["baby lotion","baby"]),
        ("Daily Mart Baby Shampoo 400ml",      330,  400, 3500, False, ["baby shampoo","baby"]),
    ]),
    ("stationery", "mch_deskhaus", [
        ("Deskhaus Ruled Notebook A5 (200 pg)", 120, 150, 4000, False, ["notebook"]),
        ("Deskhaus Dotted Journal A5",          390, 480, 4600, True,  ["premium","notebook","journal"]),
        ("Deskhaus Gel Pens Black (10)",        180, 230, 4400, False, ["pen"]),
        ("Deskhaus Ballpoint Pens (20)",        160, 200, 4200, False, ["pen"]),
        ("Deskhaus Highlighters (6)",           190, 240, 4300, False, ["highlighter","marker"]),
        ("Deskhaus Sticky Notes (500)",         210, 260, 4500, False, ["sticky note"]),
        ("Deskhaus Document Files (10)",        240, 300, 4100, False, ["file","folder"]),
        ("Deskhaus Whiteboard Markers (8)",     260, 320, 4300, False, ["marker","whiteboard"]),
    ]),
    ("pet supplies", "mch_dailymart", [
        ("Daily Mart Adult Dog Food 3kg",      980, 1200, 3000, False, ["dog food","pet"]),
        ("Daily Mart Puppy Food 1.2kg",        560,  700, 3100, False, ["dog food","pet"]),
        ("Daily Mart Cat Food Tuna 1.2kg",     640,  800, 3200, False, ["cat food","pet"]),
        ("Daily Mart Cat Litter 5kg",          520,  650, 3300, False, ["cat litter","pet"]),
        ("Daily Mart Pet Shampoo 400ml",       290,  360, 3600, False, ["pet shampoo","pet"]),
    ]),
    # ---------------------------------------------------------- regulated
    # Stocked deliberately. The interesting behaviour is not that REMIT can buy
    # these; it is that it will not, on its own, at any price. RESTRICT-001.
    ("otc medicine", "mch_medipoint", [
        ("Medipoint Paracetamol 500mg (15)",    32,   40, 2600, False, ["medicine","fever","restricted:pharmacy"]),
        ("Medipoint Antacid Tablets (20)",      68,   85, 2800, False, ["medicine","acidity","restricted:pharmacy"]),
        ("Medipoint ORS Sachets (10)",          95,  120, 2700, False, ["medicine","hydration","restricted:pharmacy"]),
        ("Medipoint Cough Syrup 100ml",        128,  160, 3000, False, ["medicine","cough","restricted:pharmacy"]),
        ("Medipoint Digital Thermometer",      340,  420, 3600, False, ["thermometer","first aid"]),
        ("Medipoint First Aid Kit",            640,  800, 3800, False, ["first aid","bandage"]),
        ("Medipoint Adhesive Bandages (40)",    95,  120, 3400, False, ["bandage","first aid"]),
    ]),
    ("alcohol", "mch_cellar", [
        ("The Cellar Lager Beer 650ml",        160,  190, 2400, False, ["beer","liquor","restricted:age"]),
        ("The Cellar Wheat Beer 500ml",        220,  260, 2600, True,  ["premium","beer","liquor","restricted:age"]),
        ("The Cellar Blended Whisky 750ml",   1450, 1750, 2900, False, ["whisky","liquor","restricted:age"]),
        ("The Cellar Single Malt 700ml",      4900, 5900, 3400, True,  ["premium","whisky","liquor","restricted:age"]),
        ("The Cellar Red Wine 750ml",          950, 1150, 3100, False, ["wine","liquor","restricted:age"]),
        ("The Cellar White Wine 750ml",        890, 1090, 3100, False, ["wine","liquor","restricted:age"]),
        ("The Cellar Vodka 750ml",            1180, 1420, 3000, False, ["vodka","liquor","restricted:age"]),
        ("The Cellar Dark Rum 750ml",          980, 1180, 2900, False, ["rum","liquor","restricted:age"]),
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
    """Idempotent. Re-seeding an unchanged catalog does NOT create a version.

    It used to, unconditionally -- and `catalog_version` is an input to the
    idempotency key, which is what stops a retry from charging somebody twice.
    So restarting the process bumped the version, the key changed, and the same
    request after a crash created a SECOND payment. The exact double-charge the
    key exists to prevent, caused by the process coming back.

    Nobody would have found this by reading it. The recovery test found it by
    killing the app and asking the new one what it believed.

    A version is created when the catalog is empty (first boot) or when its
    content actually differs. Otherwise the existing version stands, which is
    what "version" is supposed to mean.
    """
    rng = random.Random(rng_seed)
    have = db.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    if have:
        v = db.execute(
            "SELECT MAX(version) v FROM catalog_versions").fetchone()["v"]
        if v:
            row = db.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(price_paise),0) s"
                " FROM products").fetchone()
            return {"products": row["n"], "merchants": len(MERCHANTS),
                    "relations": len(RELATIONS), "catalog_version": v,
                    "reused": True, "checksum_paise": row["s"]}
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
            # "restricted:age" / "restricted:pharmacy" ride in the attribute
            # list so the catalog literal keeps one shape. The column is what
            # the policy engine reads.
            restricted = next((a.split(":", 1)[1] for a in attrs
                               if a.startswith("restricted:")), None)
            db.execute(
                "INSERT OR REPLACE INTO products (product_id, merchant_id, name,"
                " category, subcategory, price_paise, mrp_paise, margin_bps, rating,"
                " reviews, inventory, attributes, premium, ship_days, restricted,"
                " catalog_version, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (pid, merchant_id, name, category, None, price * 100, mrp * 100,
                 margin, rating, reviews, inv, json.dumps(attrs), int(premium),
                 ship_days, restricted, v))
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
                " reviews, inventory, attributes, premium, ship_days, restricted,"
                " catalog_version, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (pid, row["merchant_id"], f"{base} - {c}", row["category"], c,
                 row["price_paise"], row["mrp_paise"], row["margin_bps"],
                 round(max(3.6, row["rating"] - rng.uniform(0, 0.4)), 1),
                 rng.randint(20, 900), rng.randint(0, 40), row["attributes"],
                 row["premium"], rng.choice([2, 3, 4]), row["restricted"], v))
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
        "groceries": [
            ("Daily Mart Cola 750ml", "cross_sell", "the usual pairing with a snack run", 0.52),
            ("Freshcart Salted Peanuts 500g", "cross_sell", "goes in the same basket", 0.41),
        ],
        "beverages": [
            ("Freshcart Salted Potato Chips 150g", "cross_sell", "bought together nine times in ten", 0.58),
            ("Freshcart Digestive Biscuits 250g", "cross_sell", "for the same shelf", 0.36),
        ],
        "household": [
            ("Daily Mart Scrub Pads (6 pcs)", "cross_sell", "replaced at the same rate", 0.47),
        ],
        "baby care": [
            ("Daily Mart Baby Wipes (72 x 3)", "cross_sell", "runs out alongside the nappies", 0.69),
        ],
        "stationery": [
            ("Deskhaus Gel Pens Black (10)", "cross_sell", "pairs with a new notebook", 0.55),
        ],
        "pet supplies": [
            ("Daily Mart Pet Shampoo 400ml", "cross_sell", "same aisle, same trip", 0.38),
        ],
        "otc medicine": [
            ("Medipoint ORS Sachets (10)", "cross_sell", "commonly needed alongside", 0.42),
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