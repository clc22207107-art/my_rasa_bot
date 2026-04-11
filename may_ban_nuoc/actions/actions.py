"""
actions.py - Automatic Drink Vending Machine (Rasa Custom Actions)
Enhanced version with multi-product cart, improved payment flow
"""

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, AllSlotsReset, ConversationPaused
from typing import Any, Dict, List, Text, Optional
import re
import unicodedata
import json

# ============================================================
# INLINE DATABASE (30 products)
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
    "tra_xanh_khong_do": {
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
    "olong_tea": {
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
    "dr_thanh": {
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
    "vinamilk": {
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
        "slogan": "Open up",
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
    "wake_up_247": {
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
    "tra_gao_rut": {
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
    "vita_milk": {
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
# QR CODE PLACEHOLDER
# ============================================================
BANK_QR_INFO = {
    "bank": "Vietcombank",
    "account": "1234567890",
    "name": "AUTOMATIC DRINK VENDING MACHINE",
    "qr_text": "[QR CODE - BANK TRANSFER]\nBank: Vietcombank\nAccount: 1234 5678 90\nAccount Name: AUTOMATIC DRINK VENDING MACHINE\nNote: <Order number>",
}

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def normalize_text(text: str) -> str:
    """Normalize text: lowercase, remove accents and special characters."""
    if not text:
        return ""
    text = text.replace('đ', 'd').replace('Đ', 'd')
    nfkd = unicodedata.normalize('NFKD', text)
    ascii_str = ''.join(c for c in nfkd if not unicodedata.combining(c))
    ascii_str = re.sub(r'[^\w\s]', ' ', ascii_str)
    return re.sub(r'\s+', ' ', ascii_str).lower().strip()


def find_drink(name: str):
    """Find a drink by name or alias. Returns (key, drink_data) or (None, None)."""
    if not name:
        return None, None
    name_norm = normalize_text(name)
    name_no_space = name_norm.replace(' ', '')

    for key, drink in DRINKS_DB.items():
        key_norm = normalize_text(key)
        if name_norm == key_norm:
            return key, drink
        for alias in drink["aliases"]:
            alias_norm = normalize_text(alias)
            alias_no_space = alias_norm.replace(' ', '')
            if name_norm == alias_norm:
                return key, drink
            if name_no_space == alias_no_space:
                return key, drink
            if len(alias_norm) >= 4 and alias_norm in name_norm:
                return key, drink
            if len(name_norm) >= 4 and name_norm in alias_norm:
                return key, drink
    return None, None


def find_drink_from_message(message: str):
    """Find the best matching drink from a user message."""
    if not message:
        return None, None
    msg_norm = normalize_text(message)
    msg_no_space = msg_norm.replace(' ', '')

    candidates = []
    for key, drink in DRINKS_DB.items():
        for alias in drink["aliases"]:
            alias_norm = normalize_text(alias)
            alias_no_space = alias_norm.replace(' ', '')
            if len(alias_norm) < 2:
                continue
            if alias_norm in msg_norm or alias_no_space in msg_no_space:
                candidates.append((len(alias_norm), key, drink))

    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_key, best_drink = candidates[0]
    return best_key, best_drink


def find_all_drinks_from_message(message: str):
    """
    Find ALL products mentioned in a single message, with quantities.
    Example: "give me 2 coca and 1 pepsi"
    Returns list of (key, drink_data, quantity)
    """
    if not message:
        return []
    msg_norm = normalize_text(message)
    msg_no_space = msg_norm.replace(' ', '')

    # Step 1: Find all (alias, key, drink) matches in message, prefer longest alias
    candidates = []
    for key, drink in DRINKS_DB.items():
        for alias in drink["aliases"]:
            alias_norm = normalize_text(alias)
            alias_no_space = alias_norm.replace(' ', '')
            if len(alias_norm) < 2:
                continue
            if alias_norm in msg_norm:
                pos = msg_norm.find(alias_norm)
                candidates.append((len(alias_norm), pos, alias_norm, key, drink))
            elif alias_no_space in msg_no_space:
                pos = msg_no_space.find(alias_no_space)
                candidates.append((len(alias_norm), pos, alias_norm, key, drink))

    if not candidates:
        return []

    # Step 2: Remove duplicates per key — keep longest alias match
    best_per_key = {}
    for length, pos, alias_norm, key, drink in candidates:
        if key not in best_per_key or length > best_per_key[key][0]:
            best_per_key[key] = (length, pos, alias_norm, key, drink)

    # Step 3: Sort by position in message
    sorted_matches = sorted(best_per_key.values(), key=lambda x: x[1])

    # Step 4: For each product, find quantity in text BEFORE its position
    word_nums = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "a": 1,
    }

    results = []
    for length, pos, alias_norm, key, drink in sorted_matches:
        # Take text before the alias (up to 25 chars, after last separator)
        prefix = msg_norm[:pos]
        # Cut from nearest separator (and, with, plus, also, ,)
        for sep in [" and ", " with ", " plus ", " also ", ","]:
            idx = prefix.rfind(sep)
            if idx >= 0:
                prefix = prefix[idx + len(sep):]
                break
        prefix = prefix.strip()

        qty = 1
        # Look for digits first
        nums = re.findall(r'\d+', prefix)
        if nums:
            qty = int(nums[-1])
        else:
            for word, num in word_nums.items():
                if word in prefix.split():
                    qty = num
                    break

        results.append((key, drink, qty))

    return results


def resolve_drink(tracker):
    """Get the drink from the latest message or fall back to slot."""
    last_msg = tracker.latest_message.get("text", "")
    key, drink_data = find_drink_from_message(last_msg)
    if drink_data:
        return key, drink_data
    drink_slot = tracker.get_slot("drink")
    return find_drink(drink_slot)


def parse_quantity(qty_str: str) -> int:
    """Parse a quantity string (e.g. '2 bottles', 'three') to int."""
    if not qty_str:
        return 1
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "a": 1,
    }
    qty_lower = qty_str.lower()
    for word, num in words.items():
        if word in qty_lower.split():
            return num
    numbers = re.findall(r'\d+', qty_str)
    return int(numbers[0]) if numbers else 1


def resolve_volume(drink_data: dict, size_slot: str) -> str:
    """Resolve size slot to a valid volume string."""
    if not size_slot:
        return drink_data["default_volume"]
    size_lower = size_slot.lower()
    for vol in drink_data["volumes"]:
        if vol.lower() in size_lower or size_lower in vol.lower():
            return vol
    size_norm = normalize_text(size_slot)
    if any(w in size_norm for w in ["large", "big", "xl", "l"]):
        return drink_data["volumes"][-1]
    if any(w in size_norm for w in ["small", "sm", "s"]):
        return drink_data["volumes"][0]
    return drink_data["default_volume"]


def get_cart(tracker) -> list:
    """Get cart from slot (JSON string). Returns list of dicts."""
    cart_str = tracker.get_slot("cart")
    if not cart_str:
        return []
    try:
        return json.loads(cart_str)
    except Exception:
        return []


def cart_total(cart: list) -> int:
    return sum(item["subtotal"] for item in cart)


def format_cart(cart: list) -> str:
    """Display cart as a formatted table."""
    if not cart:
        return "🛒 Your cart is empty."
    lines = ["🛒 **SHOPPING CART**\n" + "─" * 32]
    for i, item in enumerate(cart, 1):
        lines.append(
            f"{i}. {item['image']} {item['name']} ({item['volume']})\n"
            f"   x{item['qty']} × {item['unit_price']:,} VND = {item['subtotal']:,} VND"
        )
    lines.append("─" * 32)
    lines.append(f"💵 **Total: {cart_total(cart):,} VND**")
    return "\n".join(lines)


def detect_payment_method(text: str) -> str:
    """
    Detect payment method from user message.
    Returns: 'qr' | 'cash' | 'card' | 'pay' (general request) | ''
    """
    norm = normalize_text(text)

    # Bank transfer / QR — check before card to avoid "scan" confusion
    qr_keywords = [
        "bank transfer", "wire transfer", "qr code", "qr", "scan qr",
        "transfer", "internet banking", "banking", "momo", "zalopay",
        "vnpay", "zalo pay", "e wallet", "ewallet",
    ]
    # Card payment
    card_keywords = [
        "swipe card", "card", "credit card", "debit card", "visa",
        "mastercard", "atm", "bank card", "insert card",
    ]
    # Cash
    cash_keywords = [
        "cash", "paper money", "insert money", "coins", "pay cash",
        "put money", "drop money",
    ]
    # General payment request (no method specified)
    pay_general_keywords = [
        "pay", "checkout", "payment", "buy now", "check out",
        "i want to pay", "ready to pay", "let me pay",
    ]

    for kw in qr_keywords:
        if kw in norm:
            return "qr"
    for kw in card_keywords:
        if kw in norm:
            return "card"
    for kw in cash_keywords:
        if kw in norm:
            return "cash"
    for kw in pay_general_keywords:
        if kw in norm:
            return "pay"
    return ""


# ============================================================
# ACTIONS
# ============================================================

class ActionShowMenu(Action):
    def name(self) -> Text:
        return "action_show_menu"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_norm = normalize_text(tracker.latest_message.get("text", ""))

        if any(w in last_norm for w in ["new product", "new item", "new arrival", "what s new", "latest", "recently added", "just released"]):
            new_products = [(k, v) for k, v in DRINKS_DB.items() if v["is_new"]]
            if not new_products:
                dispatcher.utter_message(text="There are no new products at the moment. Type 'menu' to see all products!")
                return []
            lines = ["🆕 **NEW PRODUCTS**\n" + "─" * 35]
            for key, d in new_products:
                default_vol = d["default_volume"]
                price = d["price"][default_vol]
                lines.append(
                    f"\n{d['image']} **{d['name']}**\n"
                    f"   💰 Price: {price:,} VND ({default_vol})\n"
                    f"   ✨ {d['features']}"
                )
            lines.append("\n💬 Would you like more info or to order any of these?")
            dispatcher.utter_message(text="\n".join(lines))
            return []

        categories = {}
        for key, drink in DRINKS_DB.items():
            cat = drink["category"]
            if cat not in categories:
                categories[cat] = []
            default_vol = drink["default_volume"]
            price = drink["price"][default_vol]
            new_badge = " 🆕" if drink["is_new"] else ""
            categories[cat].append(f"  {drink['image']} {drink['name']}{new_badge} ({default_vol}) - {price:,} VND")
        lines = ["📋 DRINK MENU\n" + "─" * 35]
        for cat, items in categories.items():
            lines.append(f"\n🏷️ {cat}:")
            lines.extend(items)
        lines.append("\n💬 Ask me about any product for more details!")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionGetDrinkInfo(Action):
    def name(self) -> Text:
        return "action_get_drink_info"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        key, drink_data = resolve_drink(tracker)
        if not drink_data:
            dispatcher.utter_message(
                text="❌ Sorry, I couldn't find that product in our menu. Type 'menu' to see the full list!"
            )
            return [SlotSet("drink", None)]
        if drink_data["stock"] == 0:
            dispatcher.utter_message(
                text=f"😔 Sorry, **{drink_data['name']}** is currently out of stock. Would you like to choose another product?"
            )
            return [SlotSet("drink", None)]
        return [SlotSet("drink", key)]


class ActionShowPrice(Action):
    def name(self) -> Text:
        return "action_show_price"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        _, drink_data = resolve_drink(tracker)
        if not drink_data:
            dispatcher.utter_message(text="Which product would you like to check the price of? (Type 'menu' to see the list)")
            return []

        last_norm = normalize_text(tracker.latest_message.get("text", ""))
        parts = []

        # Always include price
        price_lines = [f"  • {vol}: {price:,} VND" for vol, price in drink_data["price"].items()]
        parts.append("💰 Price:\n" + "\n".join(price_lines))

        # Also include flavor if asked
        if any(w in last_norm for w in ["flavor", "taste", "what does it taste", "how does it taste"]):
            parts.append(f"😋 Flavor: {drink_data['flavor']}")

        # Also include features/description if asked
        if any(w in last_norm for w in ["features", "description", "about", "benefits", "properties"]):
            parts.append(f"✨ Features: {drink_data['features']}")

        # Also include ingredients if asked
        if any(w in last_norm for w in ["ingredient", "made of", "contain", "what s in", "what is in"]):
            parts.append(f"🧪 Ingredients: {drink_data['ingredients']}")

        # Also include sizes if asked
        if any(w in last_norm for w in ["size", "volume", "ml", "liter"]):
            vols_str = ", ".join(f"{v}: {drink_data['price'][v]:,} VND" for v in drink_data["volumes"])
            parts.append(f"📦 Sizes & Prices: {vols_str}")

        msg = f"{drink_data['image']} **{drink_data['name']}**\n" + "\n".join(parts)
        dispatcher.utter_message(text=msg)
        return []


class ActionShowIngredients(Action):
    def name(self) -> Text:
        return "action_show_ingredients"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        _, drink_data = resolve_drink(tracker)
        if not drink_data:
            dispatcher.utter_message(text="Which product would you like to check the ingredients of?")
            return []

        last_norm = normalize_text(tracker.latest_message.get("text", ""))

        if "caffeine" in last_norm:
            if drink_data["has_caffeine"]:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n☕ Contains caffeine."
            else:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n✅ Caffeine-free."
            dispatcher.utter_message(text=msg)
            return []

        if any(w in last_norm for w in ["sugar", "sweet", "sweetened"]):
            if drink_data["has_sugar"]:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n🍬 Contains sugar."
            else:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n✅ Sugar-free / naturally low sugar."
            dispatcher.utter_message(text=msg)
            return []

        if any(w in last_norm for w in ["carbonated", "fizzy", "sparkling", "gas", "bubbly"]):
            has_gas = drink_data.get("category") in ["Carbonated Soft Drinks", "Energy Drinks"]
            if has_gas:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n🫧 Carbonated."
            else:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n✅ Not carbonated."
            dispatcher.utter_message(text=msg)
            return []

        if any(w in last_norm for w in ["probiotic", "bacteria", "culture", "lactobacillus"]):
            has_probiotic = any(
                w in drink_data["ingredients"].lower()
                for w in ["lactobacillus", "probiotic"]
            )
            if has_probiotic:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n🦠 Contains probiotic bacteria."
            else:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n❌ No probiotic bacteria."
            dispatcher.utter_message(text=msg)
            return []

        # Default: show full ingredients
        parts = [f"🧪 Ingredients: {drink_data['ingredients']}"]

        # Also include price if asked
        if any(w in last_norm for w in ["price", "cost", "how much", "expensive", "cheap"]):
            price_lines = [f"  • {vol}: {price:,} VND" for vol, price in drink_data["price"].items()]
            parts.append("💰 Price:\n" + "\n".join(price_lines))

        # Also include flavor if asked
        if any(w in last_norm for w in ["flavor", "taste", "how does it taste"]):
            parts.append(f"😋 Flavor: {drink_data['flavor']}")

        msg = f"{drink_data['image']} **{drink_data['name']}**\n" + "\n".join(parts)
        dispatcher.utter_message(text=msg)
        return []


class ActionShowProductInfo(Action):
    def name(self) -> Text:
        return "action_show_product_info"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        _, drink_data = resolve_drink(tracker)
        if not drink_data:
            dispatcher.utter_message(text="Which product would you like info on? (Type 'menu' to browse)")
            return []

        last_norm = normalize_text(tracker.latest_message.get("text", ""))

        stock_keywords = [
            "stock", "quantity", "available", "in stock", "out of stock",
            "how many left", "remaining", "left", "sold out", "do you have",
            "do you still have", "do you carry",
        ]

        info_parts = []

        # Price (combined query support)
        if any(w in last_norm for w in ["price", "cost", "how much", "expensive", "cheap", "afford"]):
            price_lines = [f"  • {vol}: {price:,} VND" for vol, price in drink_data["price"].items()]
            info_parts.append("💰 Price:\n" + "\n".join(price_lines))

        # Ingredients (combined query support)
        if any(w in last_norm for w in ["ingredient", "made of", "contain", "what s in", "what is in", "recipe"]):
            info_parts.append(f"🧪 Ingredients: {drink_data['ingredients']}")

        # Stock
        if any(w in last_norm for w in stock_keywords):
            stock = drink_data["stock"]
            if stock == 0:
                status = "❌ Out of stock"
            elif stock < 10:
                status = f"⚠️ Almost out — only {stock} remaining"
            else:
                status = f"✅ {stock} in stock"
            info_parts.append(f"📦 Stock: {status}")

        # Flavor
        flavor_keywords = [
            "flavor", "taste", "what does it taste", "is it good",
            "sweet", "bitter", "sour", "how does it taste",
            "what is the flavor", "what does it taste like",
        ]
        if any(w in last_norm for w in flavor_keywords):
            info_parts.append(f"😋 Flavor: {drink_data['flavor']}")

        # Volume / size
        if any(w in last_norm for w in ["size", "volume", "ml", "liter", "how many sizes", "can or bottle"]):
            vols_str = ", ".join(f"{v}: {drink_data['price'][v]:,} VND" for v in drink_data["volumes"])
            info_parts.append(f"📦 Sizes & Prices: {vols_str}")

        # Brand
        if any(w in last_norm for w in ["brand", "manufacturer", "made by", "company", "who makes"]):
            info_parts.append(f"🏭 Brand: {drink_data['brand']}")

        # Features / description
        if any(w in last_norm for w in ["features", "description", "benefits", "about", "info", "properties"]):
            info_parts.append(f"✨ Features: {drink_data['features']}")

        # Expiry
        if any(w in last_norm for w in ["expiry", "expiration", "shelf life", "best before", "how long", "expire"]):
            info_parts.append(f"📅 Shelf life: {drink_data['expiry_months']} months from production date")

        # Caffeine
        if "caffeine" in last_norm:
            val = "☕ Contains caffeine." if drink_data["has_caffeine"] else "✅ Caffeine-free."
            info_parts.append(val)

        # Sugar
        if any(w in last_norm for w in ["sugar", "sweet", "sweetened"]):
            val = "🍬 Contains sugar." if drink_data["has_sugar"] else "✅ Sugar-free / naturally low sugar."
            info_parts.append(val)

        # Carbonation
        if any(w in last_norm for w in ["carbonated", "fizzy", "sparkling", "gas", "bubbly"]):
            has_gas = drink_data.get("category") in ["Carbonated Soft Drinks", "Energy Drinks"]
            val = "🫧 Carbonated." if has_gas else "✅ Not carbonated."
            info_parts.append(val)

        # Slogan
        if any(w in last_norm for w in ["slogan", "tagline", "motto"]):
            slogan = drink_data.get("features", "No slogan info available.")
            info_parts.append(f"🎯 Slogan / Tagline: {slogan}")

        # Popularity / sales
        if any(w in last_norm for w in ["popular", "best seller", "sales", "rating", "rank",
                                         "how popular", "how many sold", "sold"]):
            stars = "⭐" * int(drink_data["popularity"])
            info_parts.append(
                f"📊 Popularity: {stars} ({drink_data['popularity']}/10)\n"
                f"🛒 Total sold: {drink_data['sales']:,} units"
            )

        # New product
        if any(w in last_norm for w in ["new", "new product", "newly released", "just released"]):
            val = "🆕 This is a NEW product!" if drink_data["is_new"] else "✅ This is an established product, not new."
            info_parts.append(val)

        # Compile result
        if info_parts:
            header = f"{drink_data['image']} **{drink_data['name']}**"
            msg = header + "\n" + "\n".join(info_parts)
        else:
            # Fallback: show full overview
            new_badge = " 🆕" if drink_data["is_new"] else ""
            vols_str = ", ".join(f"{v}: {drink_data['price'][v]:,} VND" for v in drink_data["volumes"])
            msg = (
                f"{drink_data['image']} **{drink_data['name']}**{new_badge}\n"
                f"🏭 Brand: {drink_data['brand']}\n"
                f"📦 Sizes & Prices: {vols_str}\n"
                f"😋 Flavor: {drink_data['flavor']}\n"
                f"✨ Features: {drink_data['features']}"
            )

        dispatcher.utter_message(text=msg)
        return []


# ============================================================
# CART-BASED ORDER FLOW
# ============================================================

class ActionAddToCart(Action):
    """
    Add products to the shopping cart when customer places an order.
    Supports multiple products in a single message (e.g. "give me 2 coca and 1 pepsi").
    Only confirms what was added, does not prompt further.
    """
    def name(self) -> Text:
        return "action_add_to_cart"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_msg = tracker.latest_message.get("text", "")
        size_slot = tracker.get_slot("size")
        cart = get_cart(tracker)

        # Find all products + quantities in the message
        found_items = find_all_drinks_from_message(last_msg)

        # Fallback: if nothing found from message, use slot
        if not found_items:
            drink_slot = tracker.get_slot("drink")
            key, drink_data = find_drink(drink_slot)
            if not drink_data:
                dispatcher.utter_message(
                    text="❌ I couldn't find that product. Type 'menu' to see the full list!"
                )
                return []
            qty_slot = tracker.get_slot("quantity") or "1"
            qty = parse_quantity(qty_slot)
            found_items = [(key, drink_data, qty)]

        added_lines = []
        last_key = None
        for key, drink_data, qty in found_items:
            if drink_data["stock"] == 0:
                dispatcher.utter_message(
                    text=f"😔 **{drink_data['name']}** is out of stock. Would you like to choose something else?"
                )
                continue

            volume = resolve_volume(drink_data, size_slot)
            unit_price = drink_data["price"].get(volume, list(drink_data["price"].values())[0])
            subtotal = unit_price * qty

            # Merge with existing cart item if same product & volume
            existing = next((item for item in cart if item["key"] == key and item["volume"] == volume), None)
            if existing:
                existing["qty"] += qty
                existing["subtotal"] = existing["qty"] * existing["unit_price"]
            else:
                cart.append({
                    "key": key,
                    "name": drink_data["name"],
                    "image": drink_data["image"],
                    "volume": volume,
                    "qty": qty,
                    "unit_price": unit_price,
                    "subtotal": subtotal,
                })
            added_lines.append(f"  ✅ {drink_data['image']} {qty} × {drink_data['name']} ({volume})")
            last_key = key

        if not added_lines:
            return []

        msg = "➕ **Added to cart:**\n" + "\n".join(added_lines)
        dispatcher.utter_message(text=msg)

        return [
            SlotSet("cart", json.dumps(cart, ensure_ascii=False)),
            SlotSet("drink", last_key),
        ]


class ActionShowCart(Action):
    """Display the current shopping cart."""
    def name(self) -> Text:
        return "action_show_cart"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        cart = get_cart(tracker)
        if not cart:
            dispatcher.utter_message(text="🛒 Your cart is empty. Please choose a drink first!")
            return []
        msg = format_cart(cart) + (
            "\n\n💬 Type 'confirm' to place your order, or add more products!"
        )
        dispatcher.utter_message(text=msg)
        return []


class ActionConfirmOrder(Action):
    """
    Confirm the order: show cart summary and ask for payment method.
    Called when customer says 'confirm', 'ok', 'yes', etc.
    """
    def name(self) -> Text:
        return "action_confirm_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        cart = get_cart(tracker)

        # If cart is empty, try to reconstruct from old slots (backwards compatibility)
        if not cart:
            drink_slot = tracker.get_slot("drink")
            _, drink_data = find_drink(drink_slot)
            if drink_data:
                size_slot = tracker.get_slot("size") or drink_data["default_volume"]
                qty = parse_quantity(tracker.get_slot("quantity") or "1")
                unit_price = drink_data["price"].get(size_slot, list(drink_data["price"].values())[0])
                cart = [{
                    "key": drink_slot,
                    "name": drink_data["name"],
                    "image": drink_data["image"],
                    "volume": size_slot,
                    "qty": qty,
                    "unit_price": unit_price,
                    "subtotal": unit_price * qty,
                }]
            else:
                dispatcher.utter_message(text="⚠️ Your cart is empty! Please select a product first.")
                return []

        total = cart_total(cart)
        cart_display = format_cart(cart)

        msg = (
            f"✅ **ORDER CONFIRMATION**\n\n"
            f"{cart_display}\n\n"
            f"💳 How would you like to pay?\n\n"
            f"1️⃣ **Bank Transfer** — Scan QR code\n"
            f"2️⃣ **Cash** — Insert money into machine\n"
            f"3️⃣ **Card** — Swipe/insert card\n\n"
            f"👉 Please choose your payment method!"
        )
        dispatcher.utter_message(text=msg)
        return [SlotSet("cart", json.dumps(cart, ensure_ascii=False))]


class ActionProcessPayment(Action):
    """
    Process payment — recognizes many synonymous phrases.
    Only called after the customer has confirmed the order.
    """
    def name(self) -> Text:
        return "action_process_payment"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_msg = tracker.latest_message.get("text", "")
        payment_slot = tracker.get_slot("payment_method") or ""

        method = detect_payment_method(last_msg)
        if not method:
            method = detect_payment_method(payment_slot)

        cart = get_cart(tracker)
        total_price = cart_total(cart) if cart else int(tracker.get_slot("total_price") or 0)

        if method == "qr":
            qr = BANK_QR_INFO
            msg = (
                f"📲 **BANK TRANSFER / QR PAYMENT**\n"
                f"{'═' * 35}\n"
                f"{qr['qr_text']}\n\n"
                f"💰 Amount to transfer: **{total_price:,} VND**\n"
                f"{'═' * 35}\n"
                f"⏳ After a successful transfer,\n"
                f"   the machine will automatically dispense your product!\n"
                f"📞 Contact support if you don't receive your order."
            )

        elif method == "cash":
            msg = (
                f"💵 **CASH PAYMENT**\n"
                f"{'═' * 35}\n"
                f"💰 Amount to pay: **{total_price:,} VND**\n\n"
                f"👇 Please insert money into the cash slot\n"
                f"   on the right side of the machine.\n\n"
                f"ℹ️ Accepted bills: 5K, 10K, 20K, 50K, 100K, 200K, 500K VND\n"
                f"⚡ The machine will automatically return change!"
            )

        elif method == "card":
            msg = (
                f"💳 **CARD PAYMENT**\n"
                f"{'═' * 35}\n"
                f"💰 Amount: **{total_price:,} VND**\n\n"
                f"👇 Please insert or swipe your card\n"
                f"   in the card reader on the left side.\n\n"
                f"✅ Accepted: Visa, Mastercard, Domestic ATM\n"
                f"⏳ Waiting for transaction confirmation..."
            )

        elif method == "pay":
            # Customer said "pay" but didn't specify method → ask again
            dispatcher.utter_message(
                text=(
                    f"💳 How would you like to pay **{total_price:,} VND**?\n\n"
                    f"1️⃣ **Bank Transfer** — Scan QR code\n"
                    f"2️⃣ **Cash** — Insert money into machine\n"
                    f"3️⃣ **Card** — Swipe/insert card"
                )
            )
            return []

        else:
            dispatcher.utter_message(
                text=(
                    f"❓ I didn't recognize your payment method.\n"
                    f"Please choose:\n"
                    f"1️⃣ **Bank Transfer** (scan QR code)\n"
                    f"2️⃣ **Cash** (insert money into machine)\n"
                    f"3️⃣ **Card** (swipe/insert card)"
                )
            )
            return []

        dispatcher.utter_message(text=msg)
        dispatcher.utter_message(
            text=(
                f"\n🎉 **Payment successful!**\n"
                f"🥤 Your drink is being dispensed...\n"
                f"Thank you for using our service! 😊"
            )
        )

        return [
            SlotSet("cart", None),
            SlotSet("drink", None),
            SlotSet("size", None),
            SlotSet("quantity", "1"),
            SlotSet("payment_method", None),
            SlotSet("price_per_unit", None),
            SlotSet("total_price", None),
        ]


class ActionResetOrder(Action):
    def name(self) -> Text:
        return "action_reset_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(
            text=(
                "❌ Order cancelled.\n\n"
                "Would you like to:\n"
                "🔄 Browse or order something else? → Type a product name or 'menu'\n"
                "👋 Exit? → Type 'goodbye'"
            )
        )
        return [
            SlotSet("cart", None),
            SlotSet("drink", None),
            SlotSet("size", None),
            SlotSet("quantity", "1"),
            SlotSet("payment_method", None),
            SlotSet("price_per_unit", None),
            SlotSet("total_price", None),
        ]


# Backwards-compatible actions for existing stories/rules

class ActionCalculatePrice(Action):
    """Backwards compatible — calculates price for a single product."""
    def name(self) -> Text:
        return "action_calculate_price"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        drink_slot = tracker.get_slot("drink")
        size_slot = tracker.get_slot("size")
        quantity_slot = tracker.get_slot("quantity") or "1"

        _, drink_data = find_drink(drink_slot)
        if not drink_data:
            return []

        volume = resolve_volume(drink_data, size_slot)
        quantity = parse_quantity(quantity_slot)
        price_per_unit = drink_data["price"].get(volume, list(drink_data["price"].values())[0])
        total = price_per_unit * quantity

        return [
            SlotSet("size", volume),
            SlotSet("quantity", str(quantity)),
            SlotSet("price_per_unit", str(price_per_unit)),
            SlotSet("total_price", str(total)),
        ]


class ActionShowOrderSummary(Action):
    """Backwards compatible — shows order summary for a single product."""
    def name(self) -> Text:
        return "action_show_order_summary"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        drink_slot = tracker.get_slot("drink")
        size_slot = tracker.get_slot("size")
        quantity_slot = tracker.get_slot("quantity") or "1"
        total_price = tracker.get_slot("total_price") or "0"
        price_per_unit = tracker.get_slot("price_per_unit") or "0"

        _, drink_data = find_drink(drink_slot)
        if not drink_data:
            dispatcher.utter_message(text="⚠️ No product in the order yet.")
            return []

        quantity = parse_quantity(quantity_slot)
        msg = (
            f"🛒 **CART**\n"
            f"{'─' * 30}\n"
            f"{drink_data['image']} {drink_data['name']} ({size_slot})\n"
            f"   x{quantity} × {int(price_per_unit):,} VND = {int(total_price):,} VND\n"
            f"{'─' * 30}\n"
            f"💵 **Total: {int(total_price):,} VND**\n"
            f"{'─' * 30}\n\n"
            f"💬 Would you like to:\n"
            f"  🛍️ Add another product → Say the product name\n"
            f"  ✅ Confirm order → Type 'confirm'\n"
            f"  ❌ Cancel → Type 'cancel'"
        )
        dispatcher.utter_message(text=msg)
        return []


class ActionAskQuantity(Action):
    def name(self) -> Text:
        return "action_ask_quantity"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        drink_slot = tracker.get_slot("drink")
        _, drink_data = find_drink(drink_slot)
        drink_name = drink_data["name"] if drink_data else "the product"
        dispatcher.utter_message(text=f"🔢 How many **{drink_name}** would you like?")
        return []


class ActionRecommendDrink(Action):
    def name(self) -> Text:
        return "action_recommend_drink"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_norm = normalize_text(tracker.latest_message.get("text", ""))

        # Best selling → top 3 by sales
        if any(w in last_norm for w in ["best selling", "most sold", "buy most", "sold most"]):
            top3 = sorted(DRINKS_DB.items(), key=lambda x: x[1]["sales"], reverse=True)[:3]
            lines = ["🏆 **TOP 3 BEST SELLERS**\n" + "─" * 32]
            for i, (key, d) in enumerate(top3, 1):
                lines.append(f"  {i}. {d['image']} {d['name']} — Sold: {d['sales']:,} units")
            lines.append("\n💬 Which one would you like?")
            dispatcher.utter_message(text="\n".join(lines))
            return []

        # Most popular → top 3 by popularity
        if any(w in last_norm for w in ["popular", "famous", "well known", "trending", "most popular"]):
            top3 = sorted(DRINKS_DB.items(), key=lambda x: x[1]["popularity"], reverse=True)[:3]
            lines = ["🌟 **TOP 3 MOST POPULAR**\n" + "─" * 32]
            for i, (key, d) in enumerate(top3, 1):
                lines.append(f"  {i}. {d['image']} {d['name']} — Popularity: {d['popularity']}/10")
            lines.append("\n💬 Which one would you like?")
            dispatcher.utter_message(text="\n".join(lines))
            return []

        # General recommendation → top 5 by popularity
        top5 = sorted(DRINKS_DB.items(), key=lambda x: x[1]["popularity"], reverse=True)[:5]
        lines = ["🌟 **TODAY'S DRINK RECOMMENDATIONS**\n" + "─" * 35]
        for i, (key, d) in enumerate(top5, 1):
            default_vol = d["default_volume"]
            price = d["price"][default_vol]
            lines.append(f"  {i}. {d['image']} {d['name']} - {price:,} VND\n     👉 {d['flavor']}")
        lines.append("\n💬 Which one would you like?")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionCheckPromotion(Action):
    def name(self) -> Text:
        return "action_check_promotion"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        msg = (
            "🎉 **CURRENT PROMOTIONS**\n"
            "─" * 35 + "\n"
            "🔥 Buy 2 get 1 free on Sting and Number 1!\n"
            "💚 10% off when buying 5 or more products\n"
            "🆕 New products: Aloe Vera & Roasted Brown Rice Tea — 15% off\n"
            "📅 Promotions valid until end of month\n\n"
            "Would you like to order now? 😊"
        )
        dispatcher.utter_message(text=msg)
        return []


class ActionHandleOutOfDomain(Action):
    def name(self) -> Text:
        return "action_handle_out_of_domain"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        import random
        responses = [
            (
                "🚫 Sorry, that question is outside my capabilities.\n"
                "I'm just a drink vending machine, I only support:\n\n"
                "🍹 Ordering drinks\n"
                "💰 Checking product prices\n"
                "🧪 Ingredients / product info\n"
                "⭐ Drink recommendations\n\n"
                "👉 Type 'menu' to see the list, or tell me what you'd like to drink!"
            ),
            (
                "🤖 I can't answer that — I only serve drinks!\n\n"
                "Would you like to:\n"
                "• View menu → type 'menu'\n"
                "• Order a drink → say the product name\n"
                "• Check a price → 'How much is [product]?'"
            ),
            (
                "⚠️ That question is outside my scope.\n"
                "I only support drink-related information.\n\n"
                "👉 Try asking: 'How much is Coca-Cola?' or 'What drinks do you have?'"
            ),
        ]
        dispatcher.utter_message(text=random.choice(responses))
        return []
