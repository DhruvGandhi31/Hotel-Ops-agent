"""Fictional suppliers and their price lists.

Prices are AUD cents, ex GST. GST treatment follows the broad Australian rules (fresh food
GST-free; alcohol, chemicals, amenities, services and snacks taxable) but is simplified
for a demo and is not tax advice.

Item tuple: (code, description, aliases, unit, price_cents, gst_free, (qty_min, qty_max)).
Unit "kg" means a weighed quantity with decimals.
"""

import random

from .abn import generate_abn
from .model import Item, Supplier

T = True  # GST-free
F = False  # taxable

# fmt: off
ITEMS: dict[str, list[tuple]] = {
    "produce": [
        ("P-TOM10", "Tomatoes Roma 10kg Box", ("Roma Toms 10kg", "Tomato - Roma (10 kg carton)"), "box", 3200, T, (1, 6)),
        ("P-LET01", "Lettuce Iceberg Each", ("Iceberg Lettuce", "Lettuce, iceberg (ea)"), "each", 280, T, (6, 30)),
        ("P-POT20", "Potatoes Washed 20kg Bag", ("Spuds washed 20kg", "Potato - washed bag 20kg"), "bag", 2600, T, (1, 5)),
        ("P-ONI10", "Onions Brown 10kg Bag", ("Brown Onion 10kg", "Onion brown sack 10 kg"), "bag", 1450, T, (1, 4)),
        ("P-BAN", "Bananas Cavendish", ("Cavendish Banana", "Banana - cav loose"), "kg", 450, T, (4, 20)),
        ("P-LEM", "Lemons Loose", ("Lemon", "Lemons - loose fruit"), "kg", 690, T, (2, 10)),
        ("P-BAS", "Basil Fresh Bunch", ("Basil bunch", "Herb - basil (bunch)"), "bunch", 350, T, (4, 20)),
        ("P-AVO20", "Avocado Hass Tray 20", ("Hass Avo x20", "Avocado hass 20ct tray"), "tray", 4800, T, (1, 4)),
        ("P-CAR10", "Carrots 10kg Bag", ("Carrot 10kg", "Carrots - bag 10 kg"), "bag", 1200, T, (1, 4)),
        ("P-MUS2", "Mushrooms Cup 2kg", ("Cup Mushroom 2kg", "Mushies cup 2 kg box"), "box", 2400, T, (1, 5)),
        ("P-STR", "Strawberries Punnet 250g", ("Strawberry punnet", "Berries - straw 250g"), "punnet", 399, T, (6, 30)),
        ("P-SPN1", "Spinach Baby Leaf 1kg", ("Baby Spinach 1kg", "Spinach baby 1 kg bag"), "bag", 1150, T, (1, 6)),
    ],
    "meat": [
        ("M-BEF", "Beef Eye Fillet", ("Eye Fillet Beef", "Beef - tenderloin (eye fillet)"), "kg", 5490, T, (3, 15)),
        ("M-CHB", "Chicken Breast Fillet", ("Chicken breast s/less", "Chkn breast fillet"), "kg", 1390, T, (5, 30)),
        ("M-LMR", "Lamb Rack Frenched", ("Frenched Lamb Rack", "Lamb rack (french trim)"), "kg", 4290, T, (2, 10)),
        ("M-PKB", "Pork Belly Skin On", ("Pork belly", "Belly pork rind on"), "kg", 1890, T, (3, 12)),
        ("M-BAC", "Bacon Short Cut 1kg", ("Short cut bacon 1kg", "Bacon S/C 1 kg pk"), "pack", 1650, T, (2, 12)),
        ("M-SAU", "Sausages Beef Thin 5kg", ("Thin Beef Snags 5kg", "Beef sausage thin 5 kg"), "box", 4200, T, (1, 3)),
        ("M-MIN", "Beef Mince Premium", ("Premium mince", "Mince - beef prem"), "kg", 1490, T, (5, 20)),
        ("M-TWN", "Butchers Twine 500m Roll", ("Twine roll 500m", "Butchers string 500 m"), "roll", 1890, F, (1, 2)),
        ("M-VAC", "Vacuum Bags 300x400 100pk", ("Vac bags 300x400", "Vacuum pouch 300x400 x100"), "pack", 3450, F, (1, 2)),
    ],
    "dairy": [
        ("D-MLK2", "Milk Full Cream 2L", ("Full cream milk 2 litre", "Milk FC 2L"), "bottle", 345, T, (6, 30)),
        ("D-LFM2", "Milk Lactose Free 2L", ("Lactose free milk 2L", "Milk LF 2 litre"), "bottle", 495, T, (4, 12)),
        ("D-BUT1", "Butter Unsalted 1kg", ("Unsalted butter 1kg block", "Butter U/S 1kg"), "block", 1190, T, (2, 10)),
        ("D-CRM2", "Cream Thickened 2L", ("Thickened cream 2 litre", "Cream thick 2L"), "bottle", 1590, T, (2, 8)),
        ("D-CHD1", "Cheddar Tasty 1kg Block", ("Tasty cheese 1kg", "Cheese cheddar tasty block"), "block", 1350, T, (1, 6)),
        ("D-PAR1", "Parmesan Grated 1kg", ("Grated parmesan 1kg", "Parmesan - grated bag"), "bag", 2690, T, (1, 4)),
        ("D-EGG15", "Eggs Free Range 15 Dozen Tray", ("FR eggs 15dz", "Eggs free range tray (15 doz)"), "tray", 7500, T, (1, 4)),
        ("D-YOG1", "Yoghurt Greek 1kg", ("Greek yoghurt 1kg tub", "Yogurt greek style 1kg"), "tub", 890, T, (2, 10)),
        ("D-ICE5", "Ice Cream Vanilla 5L", ("Vanilla ice cream 5 litre", "Ice-cream van 5L tub"), "tub", 2490, F, (1, 4)),
    ],
    "seafood": [
        ("S-SAL", "Salmon Fillet Skin On", ("Atlantic salmon fillet", "Salmon fillets s/on"), "kg", 3290, T, (3, 12)),
        ("S-PRW", "Prawns Green Tiger", ("Green tiger prawns", "Raw tiger prawns"), "kg", 3890, T, (2, 8)),
        ("S-BAR", "Barramundi Fillet", ("Barra fillet", "Barramundi fillets skinless"), "kg", 3190, T, (3, 10)),
        ("S-CAL", "Calamari Tubes Cleaned", ("Squid tubes", "Calamari tube clean"), "kg", 1890, T, (2, 8)),
        ("S-MUS", "Mussels Live 1kg Bag", ("Live mussels 1kg", "Mussels - pot ready 1kg"), "bag", 990, T, (2, 10)),
        ("S-OYS", "Oysters Sydney Rock Dozen", ("Sydney rock oysters doz", "Oysters SRO x12"), "dozen", 2400, T, (2, 10)),
        ("S-SCA", "Scallops Roe Off", ("Scallop meat roe-off", "Scallops - no roe"), "kg", 6990, T, (1, 4)),
        ("S-BOX", "Polystyrene Box Charge", ("Foam box", "Poly box deposit"), "each", 500, F, (1, 4)),
    ],
    "bakery": [
        ("B-SOU", "Sourdough Loaf 800g", ("Sourdough 800g", "Loaf - sourdough"), "each", 650, T, (5, 20)),
        ("B-WHT", "White Sandwich Loaf", ("Sandwich white bread", "Loaf white sandwich sliced"), "each", 480, T, (5, 20)),
        ("B-ROL", "Dinner Rolls 12pk", ("Bread rolls x12", "Rolls dinner (12)"), "pack", 540, T, (4, 12)),
        ("B-BRI", "Brioche Buns 6pk", ("Brioche burger buns x6", "Buns brioche 6 pack"), "pack", 720, T, (4, 12)),
        ("B-GFL", "Gluten Free Loaf", ("GF bread loaf", "Loaf - gluten free"), "each", 850, T, (2, 6)),
        ("B-DAN", "Danish Pastry Assorted", ("Assorted danishes", "Pastry danish mixed"), "each", 290, F, (12, 48)),
        ("B-MUF", "Muffin Blueberry", ("Blueberry muffins", "Muffins - blueberry"), "each", 260, F, (12, 36)),
    ],
    "dry_goods": [
        ("G-FLR", "Flour Plain 12.5kg", ("Plain flour 12.5 kg bag", "Flour - plain sack"), "bag", 1890, T, (1, 4)),
        ("G-SUG", "Sugar White 15kg", ("White sugar 15 kg", "Sugar - white bag 15kg"), "bag", 2450, T, (1, 3)),
        ("G-RIC", "Rice Jasmine 10kg", ("Jasmine rice 10kg", "Rice - jasmine bag"), "bag", 2290, T, (1, 4)),
        ("G-OIL", "Olive Oil Extra Virgin 4L", ("EVOO 4 litre tin", "Oil olive XV 4L"), "tin", 4590, T, (1, 4)),
        ("G-PAS", "Pasta Penne 5kg", ("Penne pasta 5 kg", "Pasta - penne rigate 5kg"), "bag", 2190, T, (1, 4)),
        ("G-TOM", "Tomatoes Crushed 2.5kg Tin x6", ("Crushed tomato 2.5kg (6)", "Tom crushed A10 carton"), "carton", 2690, T, (1, 4)),
        ("G-SAL", "Salt Sea Flakes 1kg", ("Sea salt flakes 1kg", "Salt - flake 1kg"), "tub", 890, T, (1, 4)),
        ("G-CHP", "Potato Chips Assorted 48pk", ("Chips variety 48s", "Crisps assorted x48"), "carton", 3890, F, (1, 3)),
        ("G-SFT", "Soft Drink Cans 375ml 24pk", ("Soft drinks 24 x 375ml", "Cans soft drink (24)"), "carton", 2890, F, (1, 6)),
        ("G-CHO", "Chocolate Bars 36pk", ("Choc bars x36", "Confectionery bars 36 box"), "box", 4290, F, (1, 3)),
    ],
    "liquor": [
        ("L-SHZ", "Shiraz Barossa 750ml Case 12", ("Barossa Shiraz 12x750", "Red - shiraz (case)"), "case", 19800, F, (1, 4)),
        ("L-SAV", "Sauvignon Blanc 750ml Case 12", ("Sav Blanc 12x750ml", "White - sauv blanc (case)"), "case", 16800, F, (1, 4)),
        ("L-SPK", "Sparkling Brut NV 750ml Case 12", ("Brut NV sparkling 12pk", "Sparkling - brut (case)"), "case", 15600, F, (1, 3)),
        ("L-PRO", "Prosecco DOC 750ml Case 12", ("Prosecco 12x750", "Sparkling - prosecco (case)"), "case", 17400, F, (1, 3)),
        ("L-LAG", "Lager Bottles 375ml Case 24", ("Lager 24x375ml", "Beer - lager stubbies"), "case", 5990, F, (2, 8)),
        ("L-GIN", "Gin London Dry 700ml", ("Dry gin 700ml", "Spirit - gin 700"), "bottle", 4890, F, (2, 6)),
        ("L-VOD", "Vodka 700ml", ("Vodka 700 ml bottle", "Spirit - vodka 700"), "bottle", 3990, F, (2, 6)),
        ("L-TON", "Tonic Water 200ml Case 24", ("Tonic 24x200ml", "Mixer - tonic (24)"), "case", 2390, F, (1, 4)),
    ],
    "cleaning": [
        ("C-DET20", "Dishwasher Detergent 20L", ("Dishwash liquid 20 litre", "Detergent - machine dish 20L"), "drum", 8900, F, (1, 3)),
        ("C-RIN20", "Rinse Aid 20L", ("Rinse additive 20L", "Rinse aid drum 20 litre"), "drum", 7900, F, (1, 2)),
        ("C-SAN5", "Food Surface Sanitiser 5L", ("Sanitiser food contact 5L", "Surface sanitiser 5 litre"), "bottle", 2890, F, (2, 6)),
        ("C-GLS5", "Glass Cleaner 5L", ("Window & glass cleaner 5L", "Glass clean 5 litre"), "bottle", 1990, F, (1, 4)),
        ("C-FLR5", "Floor Cleaner Neutral 5L", ("Neutral floor cleaner 5L", "Floor clean neutral 5 litre"), "bottle", 2290, F, (1, 4)),
        ("C-BLE5", "Bleach 5L", ("Bleach 5 litre", "Sodium hypochlorite 5L"), "bottle", 1290, F, (1, 4)),
        ("C-GLV", "Nitrile Gloves Large Box 100", ("Gloves nitrile L (100)", "Nitrile glove large x100"), "box", 1490, F, (2, 10)),
        ("C-TWL", "Paper Hand Towel Carton 16", ("Hand towel rolls (16)", "Towel paper roll carton"), "carton", 6490, F, (1, 4)),
        ("C-BIN", "Bin Liners 120L Carton 200", ("120L garbage bags x200", "Bin bags 120 litre (200)"), "carton", 5890, F, (1, 3)),
    ],
    "linen": [
        ("N-SHQ", "Sheet Queen Laundered", ("Queen sheet", "Sheets - queen flat"), "each", 210, F, (40, 200)),
        ("N-SHK", "Sheet King Laundered", ("King sheet", "Sheets - king flat"), "each", 240, F, (30, 150)),
        ("N-PIL", "Pillowcase Laundered", ("Pillow slip", "Pillowcases std"), "each", 65, F, (80, 300)),
        ("N-BTW", "Bath Towel Laundered", ("Towel - bath", "Bath towels"), "each", 110, F, (80, 300)),
        ("N-HTW", "Hand Towel Laundered", ("Towel - hand", "Hand towels"), "each", 70, F, (60, 250)),
        ("N-BMT", "Bath Mat Laundered", ("Mat - bath", "Bathmats"), "each", 90, F, (40, 150)),
        ("N-TBL", "Tablecloth Round Laundered", ("Round table cloth", "Tablecloths - round"), "each", 450, F, (10, 60)),
        ("N-NAP", "Napkin Linen Laundered", ("Linen napkins", "Serviettes - linen"), "each", 45, F, (50, 300)),
        ("N-DEL", "Delivery and Handling Fee", ("Delivery fee", "Freight & handling"), "each", 2500, F, (1, 1)),
    ],
    "amenities": [
        ("A-SHM", "Shampoo 30ml Carton 300", ("Shampoo bottles 30ml x300", "Shampoo mini (300)"), "carton", 18900, F, (1, 3)),
        ("A-CON", "Conditioner 30ml Carton 300", ("Conditioner 30ml x300", "Conditioner mini (300)"), "carton", 18900, F, (1, 3)),
        ("A-BDW", "Body Wash 30ml Carton 300", ("Body wash 30ml x300", "Shower gel mini (300)"), "carton", 17900, F, (1, 3)),
        ("A-SOP", "Soap Bar 25g Carton 500", ("Soap 25g x500", "Guest soap bars (500)"), "carton", 16500, F, (1, 2)),
        ("A-DEN", "Dental Kit Carton 250", ("Dental kits x250", "Toothbrush kit (250)"), "carton", 13750, F, (1, 2)),
        ("A-SLP", "Slippers Disposable Carton 100", ("Guest slippers x100", "Slippers - disposable (100pr)"), "carton", 12900, F, (1, 3)),
        ("A-TIS", "Facial Tissues Carton 48", ("Tissue boxes x48", "Facial tissue (48)"), "carton", 5290, F, (1, 3)),
        ("A-TPR", "Toilet Paper 2ply Carton 48", ("Toilet rolls 2 ply x48", "TP 2ply (48)"), "carton", 4590, F, (1, 4)),
    ],
    "maintenance": [
        ("X-LED9", "LED Globe 9W E27", ("E27 LED bulb 9 watt", "Globe LED 9W ES"), "each", 590, F, (10, 60)),
        ("X-DLT10", "LED Downlight 10W", ("Downlight LED 10 watt", "10W LED downlight"), "each", 1890, F, (4, 20)),
        ("X-SIL", "Silicone Sealant Clear 300g", ("Clear silicone 300g", "Sealant silicone (clear)"), "each", 990, F, (2, 12)),
        ("X-BAA", "Batteries AA 24pk", ("AA batteries x24", "Battery AA (24)"), "pack", 2190, F, (2, 10)),
        ("X-FLT", "HVAC Filter 595x595", ("Air con filter 595x595", "Filter panel 595 x 595"), "each", 3490, F, (4, 16)),
        ("X-PNT", "Ceiling Paint White 10L", ("White ceiling paint 10 litre", "Paint - ceiling white 10L"), "tin", 11900, F, (1, 3)),
        ("X-LUB", "Lubricant Spray 300g", ("Lube spray 300g", "Spray lubricant 300g can"), "can", 1290, F, (2, 8)),
        ("X-TAP", "Tap Washer Kit Assorted", ("Assorted tap washers", "Washer kit - tap"), "kit", 1690, F, (1, 4)),
        ("X-SHW", "Shower Head Low Flow", ("Low flow shower rose", "Showerhead water saver"), "each", 4590, F, (2, 10)),
    ],
    "coffee": [
        ("K-HB1", "Coffee Beans House Blend 1kg", ("House blend beans 1kg", "Beans - house 1 kilo"), "bag", 3290, T, (4, 20)),
        ("K-DC1", "Coffee Beans Decaf 1kg", ("Decaf beans 1kg", "Beans - decaf 1 kilo"), "bag", 3690, T, (1, 4)),
        ("K-SO1", "Single Origin Ethiopia 1kg", ("Ethiopian single origin 1kg", "Beans - SO Ethiopia"), "bag", 4290, T, (1, 4)),
        ("K-SUG", "Sugar Sticks Carton 2000", ("Sugar sticks x2000", "Sugar portion sticks"), "carton", 3490, T, (1, 2)),
        ("K-CUP8", "Takeaway Cups 8oz Carton 1000", ("8oz cups x1000", "Cups paper 8 oz (1000)"), "carton", 8900, F, (1, 3)),
        ("K-LID8", "Cup Lids 8oz Carton 1000", ("8oz lids x1000", "Lids for 8 oz cups (1000)"), "carton", 5900, F, (1, 3)),
        ("K-CLN", "Espresso Machine Cleaner 900g", ("Machine cleaning powder 900g", "Group head cleaner 900g"), "tub", 2890, F, (1, 2)),
        ("K-SVC", "Espresso Machine Service Visit", ("Machine service call", "Service - espresso machine"), "each", 18000, F, (1, 1)),
    ],
}
# fmt: on

# key, name, category, template, prints_codes, invoice number format, po_style, terms, kg decimals
SUPPLIER_SPECS = [
    ("greenleaf", "Greenleaf Fresh Produce Pty Ltd", "produce", "A", True, "INV-{:05d}", "canonical", 14, 2),
    ("southern_cross", "Southern Cross Butchery Pty Ltd", "meat", "D", True, "SCB{:06d}", "canonical", 14, 3),
    ("coastal_creamery", "Coastal Creamery Co", "dairy", "B", True, "CC-{:05d}", "compact", 7, 2),
    ("tidewater", "Tidewater Seafood Traders", "seafood", "C", False, "{:06d}", "canonical", 7, 3),
    ("wattle_street", "Wattle Street Bakehouse", "bakery", "C", False, "WSB{:04d}", "canonical", 7, 2),
    ("ironbark", "Ironbark Pantry Wholesale Pty Ltd", "dry_goods", "D", True, "IPW-{:06d}", "canonical", 30, 2),
    ("copper_still", "Copper Still Liquor Merchants", "liquor", "A", True, "CSL{:05d}", "compact", 30, 2),
    ("brightwash", "Brightwash Hygiene Supplies", "cleaning", "B", True, "BH-{:05d}", "canonical", 30, 2),
    ("bayside_linen", "Bayside Linen & Laundry Services", "linen", "A", True, "BLL-{:05d}", "canonical", 14, 2),
    ("lilly_pilly", "Lilly Pilly Guest Amenities", "amenities", "B", True, "LPA{:05d}", "canonical", 30, 2),
    ("steadfast", "Steadfast Maintenance Supplies", "maintenance", "D", True, "SMS-{:06d}", "canonical", 30, 2),
    ("morning_ridge", "Morning Ridge Coffee Roasters", "coffee", "C", False, "MR{:05d}", "canonical", 14, 2),
]

_STREETS = ("Quoll", "Saltbush", "Bellbird", "Banksia", "Ironwood", "Kestrel", "Paperbark", "Wombat")
_STREET_TYPES = ("Street", "Road", "Lane", "Parade", "Drive", "Way")
_SUBURBS = ("Bellbird Flats", "Quoll Creek", "Saltbush Heights", "Kookaburra Vale", "Banksia Ridge")


def make_suppliers(rng: random.Random) -> list[Supplier]:
    suppliers = []
    for i, (key, name, category, template, codes, inv_fmt, po_style, terms, kg_dp) in enumerate(SUPPLIER_SPECS):
        items = [
            Item(code, desc, aliases, unit, price, gst_free, qty)
            for code, desc, aliases, unit, price, gst_free, qty in ITEMS[category]
        ]
        unit_no = rng.randint(1, 40)
        street_no = rng.randint(2, 180)
        address = (
            f"Unit {unit_no}, {street_no} {rng.choice(_STREETS)} {rng.choice(_STREET_TYPES)}, "
            f"{rng.choice(_SUBURBS)} NSW {rng.randint(2100, 2799)}"
        )
        suppliers.append(
            Supplier(
                key=key,
                name=name,
                category=category,
                template=template,
                prints_codes=codes,
                invoice_number_format=inv_fmt,
                po_style=po_style,
                payment_terms_days=terms,
                qty_decimals=kg_dp,
                items=items,
                abn=generate_abn(rng),
                address=address,
                # .example is reserved (RFC 2606); 5550 xxxx is ACMA's fictitious number range.
                email=f"accounts@{key.replace('_', '')}.example",
                phone=f"(02) 5550 {1000 + i * 37 + rng.randint(0, 30):04d}",
            )
        )
    return suppliers
