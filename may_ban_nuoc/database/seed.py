"""
seed.py - Import toàn bộ DRINKS_DB vào SQLite database
=======================================================
Chạy 1 lần duy nhất để khởi tạo dữ liệu ban đầu.

Cách chạy (từ thư mục gốc may_ban_nuoc/):
    python database/seed.py

Chạy lại nếu muốn reset toàn bộ data:
    python database/seed.py --reset
"""

import sqlite3
import sys
import os
from datetime import datetime

# ============================================================
# PATH SETUP — để import đúng dù chạy từ đâu
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from database.db_manager import DatabaseManager, DB_PATH

# ============================================================
# DRINKS_DB — copy nguyên từ actions.py gốc của bạn
# (30 sản phẩm, keys đã được đồng bộ)
# ============================================================

DRINKS_DB = {
    "coca": {
        "name": "Coca-Cola",
        "aliases": ["coca", "coca cola", "coke", "coca-cola", "cocacola"],
        "brand": "Coca-Cola Company", "volumes": ["330ml", "500ml", "1.5L"],
        "default_volume": "330ml", "price": {"330ml": 12000, "500ml": 15000, "1.5L": 28000},
        "ingredients": "Water, sugar, CO2, caramel color, phosphoric acid, natural flavoring, caffeine",
        "flavor": "Classic sweet taste, carbonated, light caramel aroma",
        "features": "Classic carbonated soft drink, great refreshment",
        "category": "Carbonated Soft Drinks", "is_new": False, "popularity": 9.5,
        "sales": 1500, "stock": 120, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": True, "image": "🥤",
    },
    "pepsi": {
        "name": "Pepsi",
        "aliases": ["pepsi"],
        "brand": "PepsiCo", "volumes": ["330ml", "500ml", "1.5L"],
        "default_volume": "330ml", "price": {"330ml": 11000, "500ml": 14000, "1.5L": 26000},
        "ingredients": "Water, sugar, CO2, phosphoric acid, caramel color, natural flavoring, caffeine",
        "flavor": "Slightly lighter than Coca-Cola, carbonated, hint of vanilla",
        "features": "Globally popular carbonated soft drink",
        "category": "Carbonated Soft Drinks", "is_new": False, "popularity": 9.0,
        "sales": 1300, "stock": 100, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": True, "image": "🥤",
    },
    "sting": {
        "name": "Sting",
        "aliases": ["sting", "sting energy", "sting energy drink"],
        "brand": "PepsiCo", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 10000},
        "ingredients": "Water, sugar, citric acid, taurine, caffeine, Vitamins B3, B6, B12, strawberry flavor",
        "flavor": "Sweet taste with distinctive strawberry flavor",
        "features": "Popular energy drink, affordable, quick energy boost",
        "category": "Energy Drinks", "is_new": False, "popularity": 8.8,
        "sales": 200, "stock": 150, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "⚡",
    },
    "redbull": {
        "name": "Red Bull",
        "aliases": ["redbull", "red bull", "red bull energy", "redbull energy", "bull"],
        "brand": "Red Bull GmbH", "volumes": ["250ml"], "default_volume": "250ml",
        "price": {"250ml": 18000},
        "ingredients": "Water, sugar, citric acid, taurine (1000mg), caffeine (80mg), niacinamide, Vitamins B6, B12",
        "flavor": "Clean sweet taste, slightly tart, lightly carbonated",
        "features": "Premium imported energy drink, boosts focus and stamina",
        "category": "Energy Drinks", "is_new": False, "popularity": 9.2,
        "sales": 900, "stock": 80, "expiry_months": 18,
        "has_sugar": True, "has_caffeine": True, "image": "🐂",
    },
    "sprite": {
        "name": "Sprite",
        "aliases": ["sprite"],
        "brand": "Coca-Cola Company", "volumes": ["330ml", "500ml"],
        "default_volume": "330ml", "price": {"330ml": 11000, "500ml": 14000},
        "ingredients": "Water, sugar, CO2, citric acid, natural lemon flavor",
        "flavor": "Sweet-sour taste, fresh lemon aroma, carbonated",
        "features": "Colorless carbonated soft drink, refreshing in summer",
        "category": "Carbonated Soft Drinks", "is_new": False, "popularity": 8.5,
        "sales": 1000, "stock": 90, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍋",
    },
    "7up": {
        "name": "7UP",
        "aliases": ["7up", "7 up", "seven up"],
        "brand": "PepsiCo", "volumes": ["330ml", "500ml"],
        "default_volume": "330ml", "price": {"330ml": 10000, "500ml": 13000},
        "ingredients": "Water, sugar, CO2, citric acid, lemon & lime flavor",
        "flavor": "Mildly sweet-sour, lemon-lime aroma, carbonated",
        "features": "Clear carbonated soft drink, refreshing",
        "category": "Carbonated Soft Drinks", "is_new": False, "popularity": 8.0,
        "sales": 800, "stock": 70, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍋",
    },
    "fanta": {
        "name": "Fanta",
        "aliases": ["fanta", "fanta orange", "fanta grape"],
        "brand": "Coca-Cola Company", "volumes": ["330ml", "500ml"],
        "default_volume": "330ml", "price": {"330ml": 11000, "500ml": 14000},
        "ingredients": "Water, sugar, CO2, citric acid, natural orange/grape flavor, food coloring",
        "flavor": "Rich sweet taste, fruity flavor (orange or grape), carbonated",
        "features": "Fruit-flavored carbonated soft drink, variety of flavors",
        "category": "Carbonated Soft Drinks", "is_new": False, "popularity": 8.2,
        "sales": 850, "stock": 85, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍊",
    },
    "mirinda": {
        "name": "Mirinda",
        "aliases": ["mirinda", "mirinda orange"],
        "brand": "PepsiCo", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 10000},
        "ingredients": "Water, sugar, CO2, citric acid, orange flavor, food coloring",
        "flavor": "Rich sweet taste, bold orange flavor, carbonated",
        "features": "Orange-flavored carbonated soft drink",
        "category": "Carbonated Soft Drinks", "is_new": False, "popularity": 7.5,
        "sales": 600, "stock": 60, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍊",
    },
    "aquafina": {
        "name": "Aquafina",
        "aliases": ["aquafina", "aquafina water"],
        "brand": "PepsiCo", "volumes": ["500ml", "1.5L"], "default_volume": "500ml",
        "price": {"500ml": 7000, "1.5L": 12000},
        "ingredients": "Purified water (7-step RO filtration)",
        "flavor": "Pure, odorless, tasteless",
        "features": "Purified bottled water, 7-step RO filtered",
        "category": "Purified Water", "is_new": False, "popularity": 8.8,
        "sales": 1100, "stock": 200, "expiry_months": 24,
        "has_sugar": False, "has_caffeine": False, "image": "💧",
    },
    "lavie": {
        "name": "La Vie",
        "aliases": ["lavie", "la vie", "la vie water"],
        "brand": "Nestlé", "volumes": ["500ml", "1.5L"], "default_volume": "500ml",
        "price": {"500ml": 8000, "1.5L": 13000},
        "ingredients": "Natural mineral water, natural minerals (Ca, Mg, Na...)",
        "flavor": "Clean light taste, contains natural minerals",
        "features": "Natural mineral water, replenishes body minerals",
        "category": "Mineral Water", "is_new": False, "popularity": 8.5,
        "sales": 950, "stock": 180, "expiry_months": 24,
        "has_sugar": False, "has_caffeine": False, "image": "💧",
    },
    "revive": {
        "name": "Revive",
        "aliases": ["revive", "revive electrolyte"],
        "brand": "Coca-Cola Company", "volumes": ["500ml"], "default_volume": "500ml",
        "price": {"500ml": 10000},
        "ingredients": "Water, sugar, salt, potassium citrate, sodium citrate, zinc gluconate, Vitamin C, lemon-salt flavor",
        "flavor": "Mildly salty-sweet, distinctive lemon-salt flavor",
        "features": "Electrolyte drink for rehydration, ideal post-exercise or when dehydrated",
        "category": "Electrolyte Drinks", "is_new": False, "popularity": 8.3,
        "sales": 700, "stock": 90, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "⚗️",
    },
    "c2": {
        "name": "C2",
        "aliases": ["c2", "c2 green tea", "c2 lemon"],
        "brand": "URC Vietnam", "volumes": ["360ml", "455ml"], "default_volume": "360ml",
        "price": {"360ml": 9000, "455ml": 11000},
        "ingredients": "Water, sugar, green tea extract, citric acid, lemon flavor, Vitamin C",
        "flavor": "Mildly sweet, green tea and fresh lemon flavor",
        "features": "Popular bottled green tea, rich in antioxidants",
        "category": "Bottled Tea", "is_new": False, "popularity": 8.7,
        "sales": 1050, "stock": 110, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍵",
    },
    "zero_degree_green_tea": {
        "name": "Zero Degree Green Tea",
        "aliases": ["zero degree", "zero degree green tea", "tra xanh khong do", "khong do"],
        "brand": "Tan Hiep Phat", "volumes": ["350ml", "500ml"], "default_volume": "350ml",
        "price": {"350ml": 9000, "500ml": 12000},
        "ingredients": "Water, green tea extract, sugar, citric acid, jasmine flavor",
        "flavor": "Light tea taste, subtle jasmine floral notes, less sweet",
        "features": "Vietnamese green tea, low calorie, naturally refreshing",
        "category": "Bottled Tea", "is_new": False, "popularity": 8.6,
        "sales": 980, "stock": 100, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍵",
    },
    "oolong_tea": {
        "name": "Olong Tea+",
        "aliases": ["olong tea", "oolong tea", "olong", "oolong", "olong tea plus"],
        "brand": "Tan Hiep Phat", "volumes": ["350ml"], "default_volume": "350ml",
        "price": {"350ml": 9000},
        "ingredients": "Water, oolong tea extract, low sugar, natural tea flavor",
        "flavor": "Rich tea taste, distinctive oolong aroma, less sweet",
        "features": "Low-sugar oolong tea, aids digestion and weight management",
        "category": "Bottled Tea", "is_new": False, "popularity": 7.8,
        "sales": 650, "stock": 75, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍵",
    },
    "dr_thanh_herbal": {
        "name": "Dr Thanh",
        "aliases": ["dr thanh", "dr. thanh", "herbal drink dr thanh", "drthanh"],
        "brand": "Tan Hiep Phat", "volumes": ["350ml"], "default_volume": "350ml",
        "price": {"350ml": 10000},
        "ingredients": "Water, extract of 9 herbs (monk fruit, honeysuckle, chrysanthemum...), sugar, citric acid",
        "flavor": "Clean sweet taste, light herbal aroma, slightly bitter",
        "features": "Herbal drink for cooling, detoxifying, health benefits",
        "category": "Herbal Drinks", "is_new": False, "popularity": 8.0,
        "sales": 720, "stock": 80, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "🌿",
    },
    "monster": {
        "name": "Monster Energy",
        "aliases": ["monster", "monster energy"],
        "brand": "Monster Beverage Corporation", "volumes": ["355ml", "500ml"], "default_volume": "355ml",
        "price": {"355ml": 25000, "500ml": 35000},
        "ingredients": "Water, sugar, CO2, taurine, ginseng extract, L-carnitine, caffeine (160mg/500ml), Vitamin B",
        "flavor": "Strong sweet taste, carbonated, mixed fruit flavor",
        "features": "Premium imported energy drink, high caffeine, popular with gym-goers and gamers",
        "category": "Energy Drinks", "is_new": False, "popularity": 8.9,
        "sales": 560, "stock": 60, "expiry_months": 24,
        "has_sugar": True, "has_caffeine": True, "image": "👾",
    },
    "number1": {
        "name": "Number 1",
        "aliases": ["number 1", "number1", "no 1", "num 1"],
        "brand": "Tan Hiep Phat", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 10000},
        "ingredients": "Water, sugar, taurine, inositol, caffeine, Vitamins B3, B6, B12",
        "flavor": "Sweet taste, light ginseng flavor",
        "features": "Vietnamese energy drink, good value, suitable for everyone",
        "category": "Energy Drinks", "is_new": False, "popularity": 7.5,
        "sales": 700, "stock": 100, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "⚡",
    },
    "warrior": {
        "name": "Warrior",
        "aliases": ["warrior"],
        "brand": "Tan Hiep Phat", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 9000},
        "ingredients": "Water, sugar, taurine (800mg), caffeine, Vitamins B6, B12, niacin, citric acid",
        "flavor": "Sweet taste, lightly carbonated, fruit flavor",
        "features": "Budget energy drink, popular among students",
        "category": "Energy Drinks", "is_new": False, "popularity": 7.0,
        "sales": 580, "stock": 90, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "⚡",
    },
    "yakult": {
        "name": "Yakult",
        "aliases": ["yakult"],
        "brand": "Yakult Honsha", "volumes": ["65ml"], "default_volume": "65ml",
        "price": {"65ml": 8000},
        "ingredients": "Water, skim milk, sugar, Lactobacillus casei Shirota probiotic bacteria (6.5 billion/bottle)",
        "flavor": "Distinctive sweet-sour taste, milky aroma",
        "features": "Probiotic drinking yogurt, good for digestion and immune system",
        "category": "Drinkable Yogurt", "is_new": False, "popularity": 8.4,
        "sales": 800, "stock": 120, "expiry_months": 1,
        "has_sugar": True, "has_caffeine": False, "image": "🍶",
    },
    "vinamilk_chocolate": {
        "name": "Vinamilk Chocolate",
        "aliases": ["vinamilk", "vinamilk chocolate", "vinamilk milk"],
        "brand": "Vinamilk", "volumes": ["180ml", "250ml"], "default_volume": "180ml",
        "price": {"180ml": 8000, "250ml": 12000},
        "ingredients": "Fresh milk, sugar, cocoa powder, vanilla flavor",
        "flavor": "Sweet, creamy, rich chocolate aroma",
        "features": "Chocolate-flavored UHT milk, rich in calcium and protein",
        "category": "Milk", "is_new": False, "popularity": 8.0,
        "sales": 650, "stock": 85, "expiry_months": 6,
        "has_sugar": True, "has_caffeine": False, "image": "🍫",
    },
    "th_true_milk": {
        "name": "TH True Milk",
        "aliases": ["th true milk", "th milk", "th truemilk"],
        "brand": "TH Group", "volumes": ["180ml", "500ml", "1L"], "default_volume": "180ml",
        "price": {"180ml": 9000, "500ml": 18000, "1L": 32000},
        "ingredients": "100% pure fresh milk, Vitamins A, D, B2, calcium",
        "flavor": "Clean sweet taste, lightly creamy, natural milk aroma",
        "features": "100% pure fresh milk, no preservatives, from clean farms",
        "category": "Milk", "is_new": False, "popularity": 8.7,
        "sales": 750, "stock": 80, "expiry_months": 1,
        "has_sugar": True, "has_caffeine": False, "image": "🥛",
    },
    "dutch_lady": {
        "name": "Dutch Lady",
        "aliases": ["dutch lady", "dutchlady", "dutch lady milk"],
        "brand": "FrieslandCampina", "volumes": ["180ml", "1L"], "default_volume": "180ml",
        "price": {"180ml": 8500, "1L": 30000},
        "ingredients": "Fresh milk, sugar, Vitamins (A, D, B1, B2, B6, C), calcium, iron",
        "flavor": "Moderately sweet, aromatic, lightly creamy",
        "features": "UHT milk rich in nutrients, vitamins and minerals",
        "category": "Milk", "is_new": False, "popularity": 8.1,
        "sales": 600, "stock": 70, "expiry_months": 6,
        "has_sugar": True, "has_caffeine": False, "image": "🥛",
    },
    "nescafe": {
        "name": "Nescafé RTD",
        "aliases": ["nescafe", "nescafé", "nescafe coffee"],
        "brand": "Nestlé", "volumes": ["180ml"], "default_volume": "180ml",
        "price": {"180ml": 15000},
        "ingredients": "Water, sugar, instant coffee (2%), milk, natural coffee flavor",
        "flavor": "Lightly bitter, coffee aroma, moderately sweet",
        "features": "Ready-to-drink coffee, convenient, quick alertness boost",
        "category": "Canned Coffee", "is_new": False, "popularity": 7.8,
        "sales": 480, "stock": 60, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "☕",
    },
    "birdy": {
        "name": "Café Birdy",
        "aliases": ["birdy", "birdy coffee", "cafe birdy"],
        "brand": "Ajinomoto", "volumes": ["170ml"], "default_volume": "170ml",
        "price": {"170ml": 12000},
        "ingredients": "Water, sugar, Robusta coffee, condensed milk, coffee flavor",
        "flavor": "Rich bitter taste, milky sweetness, strong Robusta coffee aroma",
        "features": "Famous Thai canned coffee, distinctive rich flavor",
        "category": "Canned Coffee", "is_new": False, "popularity": 7.5,
        "sales": 400, "stock": 50, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "☕",
    },
    "lipton": {
        "name": "Lipton Peach Tea",
        "aliases": ["lipton", "lipton peach tea", "lipton tea"],
        "brand": "Unilever", "volumes": ["330ml", "455ml"], "default_volume": "330ml",
        "price": {"330ml": 10000, "455ml": 13000},
        "ingredients": "Water, sugar, tea extract, citric acid, natural peach flavor, Vitamin C",
        "flavor": "Mildly sweet, fresh peach aroma",
        "features": "Bottled peach tea, refreshing, fewer calories than soda",
        "category": "Bottled Tea", "is_new": False, "popularity": 7.9,
        "sales": 680, "stock": 80, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍑",
    },
    "nestea": {
        "name": "Nestea Peach Tea",
        "aliases": ["nestea", "nestea peach tea", "nestea tea"],
        "brand": "Nestlé", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 10000},
        "ingredients": "Water, sugar, tea extract, peach flavor, citric acid, Vitamin C",
        "flavor": "Sweet-sour, stronger peach flavor than Lipton",
        "features": "Canned peach tea, great refreshment",
        "category": "Bottled Tea", "is_new": False, "popularity": 7.6,
        "sales": 550, "stock": 65, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍑",
    },
    "cocoxim": {
        "name": "Cocoxim Coconut Water",
        "aliases": ["cocoxim", "cocoxim coconut", "coconut water", "coconut"],
        "brand": "Cocoxim", "volumes": ["330ml", "1L"], "default_volume": "330ml",
        "price": {"330ml": 15000, "1L": 38000},
        "ingredients": "100% pure fresh coconut water, no added sugar, no preservatives",
        "flavor": "Naturally mildly sweet, refreshing coconut flavor",
        "features": "Pure coconut water, rich in natural electrolytes, no added sugar",
        "category": "Coconut Water / Fruit Juice", "is_new": False, "popularity": 8.3,
        "sales": 620, "stock": 70, "expiry_months": 12,
        "has_sugar": False, "has_caffeine": False, "image": "🥥",
    },
    "twister": {
        "name": "Twister Orange Juice",
        "aliases": ["twister", "twister orange", "twister orange juice"],
        "brand": "Coca-Cola Company", "volumes": ["455ml"], "default_volume": "455ml",
        "price": {"455ml": 12000},
        "ingredients": "Orange juice (15%), water, sugar, citric acid, Vitamin C, natural orange flavor",
        "flavor": "Sweet-sour, fresh orange taste",
        "features": "Popular orange juice, rich in Vitamin C",
        "category": "Fruit Juice", "is_new": False, "popularity": 7.7,
        "sales": 500, "stock": 60, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍊",
    },
    "aloe_vera": {
        "name": "Aloe Vera Drink",
        "aliases": ["aloe vera", "aloe", "aloe vera drink"],
        "brand": "Woongjin", "volumes": ["500ml"], "default_volume": "500ml",
        "price": {"500ml": 18000},
        "ingredients": "Water, sugar, aloe vera pulp (8%), citric acid, Vitamin C, aloe vera flavor",
        "flavor": "Mildly sweet, refreshing, with crunchy aloe vera pieces",
        "features": "Korean aloe vera drink, good for skin and digestion",
        "category": "Fruit / Herbal Drinks", "is_new": True, "popularity": 8.1,
        "sales": 430, "stock": 55, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "🌵",
    },
    "wakeup247": {
        "name": "Wake Up 247",
        "aliases": ["wake up", "wake up 247", "wakeup247"],
        "brand": "Tan Hiep Phat", "volumes": ["240ml"], "default_volume": "240ml",
        "price": {"240ml": 13000},
        "ingredients": "Water, roasted ground coffee (Robusta & Arabica), sugar, milk, coffee flavor",
        "flavor": "Rich bitter taste, roasted coffee aroma, moderately sweet",
        "features": "Vietnamese canned coffee, rich flavor, great value",
        "category": "Canned Coffee", "is_new": False, "popularity": 7.6,
        "sales": 450, "stock": 55, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "☕",
    },
    "brown_rice_tea": {
        "name": "Roasted Brown Rice Tea",
        "aliases": ["brown rice tea", "roasted rice tea", "roasted brown rice tea", "tra gao lut"],
        "brand": "Fami", "volumes": ["350ml"], "default_volume": "350ml",
        "price": {"350ml": 10000},
        "ingredients": "Water, roasted brown rice, palm sugar, refined salt, natural rice flavor",
        "flavor": "Nutty toasted rice aroma, mildly sweet, rustic flavor",
        "features": "Vietnamese roasted brown rice tea, suitable for dieters and diabetics",
        "category": "Herbal Tea", "is_new": True, "popularity": 7.2,
        "sales": 280, "stock": 40, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "🌾",
    },
    "soy_milk": {
        "name": "Vita Milk Soy Milk",
        "aliases": ["vita milk", "vitamilk", "soy milk vita", "vita soy milk"],
        "brand": "Vita Food", "volumes": ["200ml"], "default_volume": "200ml",
        "price": {"200ml": 9000},
        "ingredients": "Water, soybeans (20%), sugar, salt, Vitamin D, calcium",
        "flavor": "Distinctively sweet and creamy soy bean taste, lightly fragrant",
        "features": "Famous Thai soy milk, rich in plant-based protein",
        "category": "Plant-Based Milk", "is_new": False, "popularity": 7.9,
        "sales": 520, "stock": 65, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "🫘",
    },
}


# ============================================================
# SEED FUNCTIONS
# ============================================================

def clear_all_data(conn: sqlite3.Connection):
    """Xoá toàn bộ data (giữ nguyên bảng)."""
    print("  Đang xoá data cũ...")
    conn.executescript("""
        DELETE FROM transactions;
        DELETE FROM order_items;
        DELETE FROM orders;
        DELETE FROM inventory;
        DELETE FROM product_aliases;
        DELETE FROM product_prices;
        DELETE FROM products;
    """)
    conn.commit()
    print("  ✅ Xoá xong.")


def seed_products(conn: sqlite3.Connection):
    """Import toàn bộ DRINKS_DB vào database."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(DRINKS_DB)
    print(f"\n  Đang import {total} sản phẩm...")

    for product_id, data in DRINKS_DB.items():

        # 1. Bảng products
        conn.execute("""
            INSERT INTO products
                (id, name, brand, category, default_volume,
                 ingredients, flavor, features, image,
                 is_new, has_sugar, has_caffeine,
                 popularity, sales, expiry_months, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            product_id,
            data["name"],
            data["brand"],
            data["category"],
            data["default_volume"],
            data["ingredients"],
            data["flavor"],
            data["features"],
            data["image"],
            1 if data["is_new"] else 0,
            1 if data["has_sugar"] else 0,
            1 if data["has_caffeine"] else 0,
            data["popularity"],
            data.get("sales", 0),
            data["expiry_months"],
            now,
        ))

        # 2. Bảng product_prices — 1 dòng cho mỗi size
        for volume, price in data["price"].items():
            conn.execute("""
                INSERT INTO product_prices (product_id, volume, price)
                VALUES (?, ?, ?)
            """, (product_id, volume, price))

        # 3. Bảng product_aliases — 1 dòng cho mỗi alias
        for alias in data["aliases"]:
            conn.execute("""
                INSERT OR IGNORE INTO product_aliases (product_id, alias)
                VALUES (?, ?)
            """, (product_id, alias.lower().strip()))

        # 4. Bảng inventory — tồn kho ban đầu từ DRINKS_DB
        #    Phân bổ stock đều cho các size
        volumes = data["volumes"]
        total_stock = data["stock"]
        stock_per_volume = total_stock // len(volumes)
        remainder = total_stock % len(volumes)

        for i, volume in enumerate(volumes):
            # Size đầu tiên nhận phần dư nếu không chia đều
            qty = stock_per_volume + (remainder if i == 0 else 0)
            conn.execute("""
                INSERT INTO inventory (product_id, volume, quantity, updated_at)
                VALUES (?, ?, ?, ?)
            """, (product_id, volume, qty, now))

        print(f"    ✅ {product_id:<25} | {data['name']:<30} | stock: {total_stock}")

    conn.commit()


def verify_seed(conn: sqlite3.Connection):
    """Kiểm tra data sau khi seed."""
    print("\n  Kiểm tra kết quả:")

    counts = {
        "products":        conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "product_prices":  conn.execute("SELECT COUNT(*) FROM product_prices").fetchone()[0],
        "product_aliases": conn.execute("SELECT COUNT(*) FROM product_aliases").fetchone()[0],
        "inventory":       conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0],
    }

    for table, count in counts.items():
        print(f"    {table:<20}: {count} dòng")

    # Kiểm tra tổng tồn kho
    total_stock = conn.execute("SELECT SUM(quantity) FROM inventory").fetchone()[0]
    print(f"\n    Tổng tồn kho toàn bộ: {total_stock} sản phẩm")

    # Kiểm tra thử 1 sản phẩm
    coca = conn.execute("SELECT * FROM products WHERE id = 'coca'").fetchone()
    coca_prices = conn.execute(
        "SELECT volume, price FROM product_prices WHERE product_id = 'coca'"
    ).fetchall()
    coca_stock = conn.execute(
        "SELECT SUM(quantity) FROM inventory WHERE product_id = 'coca'"
    ).fetchone()[0]

    print(f"\n    Sample — Coca-Cola:")
    print(f"      Name    : {coca['name']}")
    print(f"      Prices  : {[(r['volume'], r['price']) for r in coca_prices]}")
    print(f"      Stock   : {coca_stock}")


# ============================================================
# MAIN
# ============================================================

def main():
    reset_mode = "--reset" in sys.argv

    print("=" * 55)
    print("  SEED DATABASE — AUTOMATIC DRINK VENDING MACHINE")
    print("=" * 55)
    print(f"  Database: {DB_PATH}")
    print(f"  Mode    : {'RESET (xoá data cũ)' if reset_mode else 'NORMAL (giữ data cũ nếu có)'}")
    print()

    db = DatabaseManager()
    conn = db._get_conn()

    try:
        # Kiểm tra đã có data chưa
        existing = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]

        if existing > 0 and not reset_mode:
            print(f"  ⚠️  Database đã có {existing} sản phẩm.")
            print("  Chạy lại với --reset để xoá và import lại:")
            print("      python database/seed.py --reset")
            return

        if reset_mode and existing > 0:
            clear_all_data(conn)

        seed_products(conn)
        verify_seed(conn)

        print("\n" + "=" * 55)
        print("  ✅ SEED HOÀN THÀNH!")
        print(f"  File: {DB_PATH}")
        print("=" * 55)

    finally:
        conn.close()


if __name__ == "__main__":
    main()