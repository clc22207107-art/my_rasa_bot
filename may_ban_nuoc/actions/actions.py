"""
actions.py - Automatic Drink Vending Machine (Rasa Custom Actions)
Enhanced version with multi-product cart, improved payment flow
Refactored: DRINKS_DB hardcode → SQLite via DatabaseManager
"""

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, AllSlotsReset, ConversationPaused
from typing import Any, Dict, List, Text, Optional
import re
import unicodedata
import json
import os
import sys
import sqlite3

# ============================================================
# DATABASE IMPORT
# ============================================================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from database import DatabaseManager

# 1 instance dùng chung toàn bộ actions
_db = DatabaseManager()

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
    if not text:
        return ""
    text = text.replace('đ', 'd').replace('Đ', 'd')
    nfkd = unicodedata.normalize('NFKD', text)
    ascii_str = ''.join(c for c in nfkd if not unicodedata.combining(c))
    ascii_str = re.sub(r'[^\w\s]', ' ', ascii_str)
    return re.sub(r'\s+', ' ', ascii_str).lower().strip()


def find_drink(name: str):
    """
    Tìm drink theo name/alias.
    Trước: duyệt DRINKS_DB hardcode.
    Sau  : query SQLite qua DatabaseManager.
    Returns (key, drink_data) hoặc (None, None).
    """
    if not name:
        return None, None

    name_norm = normalize_text(name)
    name_no_space = name_norm.replace(' ', '')

    # Thử exact alias
    drink = _db.find_drink_by_alias(name_norm)
    if drink:
        return drink["id"], drink

    drink = _db.find_drink_by_alias(name_no_space)
    if drink:
        return drink["id"], drink

    # Fuzzy match qua toàn bộ drinks + aliases
    all_drinks = _db.get_all_drinks()
    for d in all_drinks:
        key = d["id"]
        if name_norm == normalize_text(key):
            return key, d
        for alias in d["aliases"]:
            alias_norm = normalize_text(alias)
            alias_no_space = alias_norm.replace(' ', '')
            if name_norm == alias_norm:
                return key, d
            if name_no_space == alias_no_space:
                return key, d
            if len(alias_norm) >= 4 and alias_norm in name_norm:
                return key, d
            if len(name_norm) >= 4 and name_norm in alias_norm:
                return key, d

    return None, None


def find_drink_from_message(message: str):
    """Tìm drink phù hợp nhất từ message của user."""
    if not message:
        return None, None

    msg_norm = normalize_text(message)
    msg_no_space = msg_norm.replace(' ', '')

    candidates = []
    for drink in _db.get_all_drinks():
        for alias in drink["aliases"]:
            alias_norm = normalize_text(alias)
            alias_no_space = alias_norm.replace(' ', '')
            if len(alias_norm) < 2:
                continue
            if alias_norm in msg_norm or alias_no_space in msg_no_space:
                candidates.append((len(alias_norm), drink["id"], drink))

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_key, best_drink = candidates[0]
    return best_key, best_drink


def find_all_drinks_from_message(message: str):
    """
    Tìm TẤT CẢ sản phẩm trong 1 message kèm số lượng.
    Ví dụ: "give me 2 coca and 1 pepsi"
    Returns list of (key, drink_data, quantity).
    """
    if not message:
        return []

    msg_norm = normalize_text(message)
    msg_no_space = msg_norm.replace(' ', '')

    candidates = []
    for drink in _db.get_all_drinks():
        for alias in drink["aliases"]:
            alias_norm = normalize_text(alias)
            alias_no_space = alias_norm.replace(' ', '')
            if len(alias_norm) < 2:
                continue
            if alias_norm in msg_norm:
                pos = msg_norm.find(alias_norm)
                candidates.append((len(alias_norm), pos, alias_norm, drink["id"], drink))
            elif alias_no_space in msg_no_space:
                pos = msg_no_space.find(alias_no_space)
                candidates.append((len(alias_norm), pos, alias_norm, drink["id"], drink))

    if not candidates:
        return []

    best_per_key = {}
    for length, pos, alias_norm, key, drink in candidates:
        if key not in best_per_key or length > best_per_key[key][0]:
            best_per_key[key] = (length, pos, alias_norm, key, drink)

    sorted_matches = sorted(best_per_key.values(), key=lambda x: x[1])

    word_nums = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a": 1,
    }

    results = []
    for length, pos, alias_norm, key, drink in sorted_matches:
        prefix = msg_norm[:pos]
        for sep in [" and ", " with ", " plus ", " also ", ","]:
            idx = prefix.rfind(sep)
            if idx >= 0:
                prefix = prefix[idx + len(sep):]
                break
        prefix = prefix.strip()

        qty = 1
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
    last_msg = tracker.latest_message.get("text", "")
    key, drink_data = find_drink_from_message(last_msg)
    if drink_data:
        return key, drink_data
    drink_slot = tracker.get_slot("drink")
    return find_drink(drink_slot)


def parse_quantity(qty_str: str) -> int:
    if not qty_str:
        return 1
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a": 1,
    }
    qty_lower = qty_str.lower()
    for word, num in words.items():
        if word in qty_lower.split():
            return num
    numbers = re.findall(r'\d+', qty_str)
    return int(numbers[0]) if numbers else 1


def resolve_volume(drink_data: dict, size_slot: str) -> str:
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
    norm = normalize_text(text)
    qr_keywords = [
        "bank transfer", "wire transfer", "qr code", "qr", "scan qr",
        "transfer", "internet banking", "banking", "momo", "zalopay",
        "vnpay", "zalo pay", "e wallet", "ewallet",
    ]
    card_keywords = [
        "swipe card", "card", "credit card", "debit card", "visa",
        "mastercard", "atm", "bank card", "insert card",
    ]
    cash_keywords = [
        "cash", "paper money", "insert money", "coins", "pay cash",
        "put money", "drop money",
    ]
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


def _get_latest_pending_order_id() -> Optional[int]:
    """Lấy order pending gần nhất."""
    conn = sqlite3.connect(_db.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id FROM orders WHERE status='pending' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row["id"] if row else None


# ============================================================
# ACTIONS
# ============================================================

class ActionShowMenu(Action):
    def name(self) -> Text:
        return "action_show_menu"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_norm = normalize_text(tracker.latest_message.get("text", ""))

        if any(w in last_norm for w in ["new product", "new item", "new arrival", "what s new", "latest", "recently added", "just released"]):
            new_products = _db.get_new_products()
            if not new_products:
                dispatcher.utter_message(text="There are no new products at the moment. Type 'menu' to see all products!")
                return []
            lines = ["🆕 **NEW PRODUCTS**\n" + "─" * 35]
            for d in new_products:
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

        categories = _db.get_menu_by_category()
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
        # Với intent order_drink: KHÔNG scan message (vì message có thể chứa nhiều sản phẩm)
        # Chỉ dùng entity slot do Rasa NLU extract
        # ActionAddToCart sẽ tự scan message để xử lý multi-product
        intent = tracker.latest_message.get("intent", {}).get("name", "")

        if intent == "order_drink":
            # Với order_drink, bỏ qua action này — ActionAddToCart tự xử lý
            drink_slot = tracker.get_slot("drink")
            if drink_slot:
                key, drink_data = find_drink(drink_slot)
                if drink_data:
                    return [SlotSet("drink", key)]
            return []

        # Với các intent khác (ask_price, ask_ingredients, ask_product_info)
        # dùng resolve_drink() bình thường
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

        price_lines = [f"  • {vol}: {price:,} VND" for vol, price in drink_data["price"].items()]
        parts.append("💰 Price:\n" + "\n".join(price_lines))

        if any(w in last_norm for w in ["flavor", "taste", "what does it taste", "how does it taste"]):
            parts.append(f"😋 Flavor: {drink_data['flavor']}")
        if any(w in last_norm for w in ["features", "description", "about", "benefits", "properties"]):
            parts.append(f"✨ Features: {drink_data['features']}")
        if any(w in last_norm for w in ["ingredient", "made of", "contain", "what s in", "what is in"]):
            parts.append(f"🧪 Ingredients: {drink_data['ingredients']}")
        if any(w in last_norm for w in ["size", "volume", "ml", "liter"]):
            vols_str = ", ".join(f"{v}: {drink_data['price'][v]:,} VND" for v in drink_data["volumes"])
            parts.append(f"📦 Sizes & Prices: {vols_str}")

        dispatcher.utter_message(text=f"{drink_data['image']} **{drink_data['name']}**\n" + "\n".join(parts))
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
        info_parts = []

        if "caffeine" in last_norm:
            info_parts.append("☕ Contains caffeine." if drink_data["has_caffeine"] else "✅ Caffeine-free.")
        if any(w in last_norm for w in ["sugar", "sweet", "sweetened"]):
            info_parts.append("🍬 Contains sugar." if drink_data["has_sugar"] else "✅ Sugar-free / naturally low sugar.")
        if any(w in last_norm for w in ["carbonated", "fizzy", "sparkling", "gas", "bubbly"]):
            has_gas = drink_data.get("category") in ["Carbonated Soft Drinks", "Energy Drinks"]
            info_parts.append("🫧 Carbonated." if has_gas else "✅ Not carbonated.")
        if any(w in last_norm for w in ["probiotic", "bacteria", "culture", "lactobacillus"]):
            has_probiotic = any(w in drink_data["ingredients"].lower() for w in ["lactobacillus", "probiotic"])
            info_parts.append("🦠 Contains probiotic bacteria." if has_probiotic else "❌ No probiotic bacteria.")

        if not info_parts:
            info_parts.append(f"🧪 Ingredients: {drink_data['ingredients']}")

        if any(w in last_norm for w in ["price", "cost", "how much", "expensive", "cheap"]):
            price_lines = [f"  • {vol}: {price:,} VND" for vol, price in drink_data["price"].items()]
            info_parts.append("💰 Price:\n" + "\n".join(price_lines))
        if any(w in last_norm for w in ["size", "volume", "ml", "liter", "can or bottle"]):
            vols_str = ", ".join(f"{v}: {drink_data['price'][v]:,} VND" for v in drink_data["volumes"])
            info_parts.append(f"📦 Sizes & Prices: {vols_str}")
        if any(w in last_norm for w in ["flavor", "taste", "how does it taste"]):
            info_parts.append(f"😋 Flavor: {drink_data['flavor']}")
        if any(w in last_norm for w in ["features", "description", "benefits", "about"]):
            info_parts.append(f"✨ Features: {drink_data['features']}")

        dispatcher.utter_message(text=f"{drink_data['image']} **{drink_data['name']}**\n" + "\n".join(info_parts))
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

        if any(w in last_norm for w in ["price", "cost", "how much", "expensive", "cheap", "afford"]):
            price_lines = [f"  • {vol}: {price:,} VND" for vol, price in drink_data["price"].items()]
            info_parts.append("💰 Price:\n" + "\n".join(price_lines))
        if any(w in last_norm for w in ["ingredient", "made of", "contain", "what s in", "what is in", "recipe"]):
            info_parts.append(f"🧪 Ingredients: {drink_data['ingredients']}")
        if any(w in last_norm for w in stock_keywords):
            stock = drink_data["stock"]
            if stock == 0:
                status = "❌ Out of stock"
            elif stock < 10:
                status = f"⚠️ Almost out — only {stock} remaining"
            else:
                status = f"✅ {stock} in stock"
            info_parts.append(f"📦 Stock: {status}")
        flavor_keywords = ["flavor", "taste", "what does it taste", "is it good", "sweet", "bitter", "sour", "how does it taste", "what is the flavor", "what does it taste like"]
        if any(w in last_norm for w in flavor_keywords):
            info_parts.append(f"😋 Flavor: {drink_data['flavor']}")
        if any(w in last_norm for w in ["size", "volume", "ml", "liter", "how many sizes", "can or bottle"]):
            vols_str = ", ".join(f"{v}: {drink_data['price'][v]:,} VND" for v in drink_data["volumes"])
            info_parts.append(f"📦 Sizes & Prices: {vols_str}")
        if any(w in last_norm for w in ["brand", "manufacturer", "made by", "company", "who makes"]):
            info_parts.append(f"🏭 Brand: {drink_data['brand']}")
        if any(w in last_norm for w in ["category", "type of drink", "what type", "what kind", "belong to", "classify"]):
            info_parts.append(f"🏷️ Category: {drink_data['category']}")
        if any(w in last_norm for w in ["features", "description", "benefits", "about", "info", "properties"]):
            info_parts.append(f"✨ Features: {drink_data['features']}")
        if any(w in last_norm for w in ["expiry", "expiration", "shelf life", "best before", "how long", "expire"]):
            info_parts.append(f"📅 Shelf life: {drink_data['expiry_months']} months from production date")
        if "caffeine" in last_norm:
            info_parts.append("☕ Contains caffeine." if drink_data["has_caffeine"] else "✅ Caffeine-free.")
        if any(w in last_norm for w in ["sugar", "sweet", "sweetened"]):
            info_parts.append("🍬 Contains sugar." if drink_data["has_sugar"] else "✅ Sugar-free / naturally low sugar.")
        if any(w in last_norm for w in ["carbonated", "fizzy", "sparkling", "gas", "bubbly"]):
            has_gas = drink_data.get("category") in ["Carbonated Soft Drinks", "Energy Drinks"]
            info_parts.append("🫧 Carbonated." if has_gas else "✅ Not carbonated.")
        if any(w in last_norm for w in ["slogan", "tagline", "motto"]):
            info_parts.append(f"🎯 Slogan / Tagline: {drink_data.get('features', 'No slogan info available.')}")
        if any(w in last_norm for w in ["popular", "best seller", "sales", "rating", "rank", "how popular", "how many sold", "sold"]):
            stars = "⭐" * int(drink_data["popularity"])
            info_parts.append(f"📊 Popularity: {stars} ({drink_data['popularity']}/10)\n🛒 Total sold: {drink_data.get('sales', 0):,} units")
        if any(w in last_norm for w in ["new", "new product", "newly released", "just released"]):
            info_parts.append("🆕 This is a NEW product!" if drink_data["is_new"] else "✅ This is an established product, not new.")

        if info_parts:
            msg = f"{drink_data['image']} **{drink_data['name']}**\n" + "\n".join(info_parts)
        else:
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
    def name(self) -> Text:
        return "action_add_to_cart"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_msg = tracker.latest_message.get("text", "")
        size_slot = tracker.get_slot("size")
        cart = get_cart(tracker)

        found_items = find_all_drinks_from_message(last_msg)

        if not found_items:
            drink_slot = tracker.get_slot("drink")
            key, drink_data = find_drink(drink_slot)
            if not drink_data:
                dispatcher.utter_message(text="❌ I couldn't find that product. Type 'menu' to see the full list!")
                return []
            qty = parse_quantity(tracker.get_slot("quantity") or "1")
            found_items = [(key, drink_data, qty)]

        added_lines = []
        last_key = None

        for key, drink_data, qty in found_items:
            volume = resolve_volume(drink_data, size_slot)

            # Kiểm tra tồn kho thực tế từ DB
            if not _db.check_stock_available(key, volume, qty):
                current_stock = _db.get_stock(key, volume)
                if current_stock == 0:
                    dispatcher.utter_message(
                        text=f"😔 **{drink_data['name']}** ({volume}) is out of stock. Would you like to choose something else?"
                    )
                else:
                    dispatcher.utter_message(
                        text=f"⚠️ Only **{current_stock}** units of {drink_data['name']} ({volume}) left. You requested {qty}."
                    )
                continue

            unit_price = drink_data["price"].get(volume, list(drink_data["price"].values())[0])
            subtotal = unit_price * qty

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

        dispatcher.utter_message(text="➕ **Added to cart:**\n" + "\n".join(added_lines))
        return [
            SlotSet("cart", json.dumps(cart, ensure_ascii=False)),
            SlotSet("drink", last_key),
        ]


class ActionShowCart(Action):
    def name(self) -> Text:
        return "action_show_cart"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        cart = get_cart(tracker)
        if not cart:
            dispatcher.utter_message(text="🛒 Your cart is empty. Please choose a drink first!")
            return []
        dispatcher.utter_message(
            text=format_cart(cart) + "\n\n💬 Type 'confirm' to place your order, or add more products!"
        )
        return []


class ActionConfirmOrder(Action):
    def name(self) -> Text:
        return "action_confirm_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        cart = get_cart(tracker)

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

        # Tạo order pending trong DB
        _db.create_order(cart)

        total = cart_total(cart)
        msg = (
            f"✅ **ORDER CONFIRMATION**\n\n"
            f"{format_cart(cart)}\n\n"
            f"💳 How would you like to pay?\n\n"
            f"1️⃣ **Bank Transfer** — Scan QR code\n"
            f"2️⃣ **Cash** — Insert money into machine\n"
            f"3️⃣ **Card** — Swipe/insert card\n\n"
            f"👉 Please choose your payment method!"
        )
        dispatcher.utter_message(text=msg)
        return [
            SlotSet("cart", json.dumps(cart, ensure_ascii=False)),
            SlotSet("total_price", str(total)),
        ]


class ActionProcessPayment(Action):
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
                f"📲 **BANK TRANSFER / QR PAYMENT**\n{'═'*35}\n"
                f"{qr['qr_text']}\n\n"
                f"💰 Amount to transfer: **{total_price:,} VND**\n{'═'*35}\n"
                f"⏳ After a successful transfer,\n   the machine will automatically dispense your product!\n"
                f"📞 Contact support if you don't receive your order."
            )
        elif method == "cash":
            msg = (
                f"💵 **CASH PAYMENT**\n{'═'*35}\n"
                f"💰 Amount to pay: **{total_price:,} VND**\n\n"
                f"👇 Please insert money into the cash slot\n   on the right side of the machine.\n\n"
                f"ℹ️ Accepted bills: 5K, 10K, 20K, 50K, 100K, 200K, 500K VND\n"
                f"⚡ The machine will automatically return change!"
            )
        elif method == "card":
            msg = (
                f"💳 **CARD PAYMENT**\n{'═'*35}\n"
                f"💰 Amount: **{total_price:,} VND**\n\n"
                f"👇 Please insert or swipe your card\n   in the card reader on the left side.\n\n"
                f"✅ Accepted: Visa, Mastercard, Domestic ATM\n"
                f"⏳ Waiting for transaction confirmation..."
            )
        elif method == "pay":
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
                    f"❓ I didn't recognize your payment method.\nPlease choose:\n"
                    f"1️⃣ **Bank Transfer** (scan QR code)\n"
                    f"2️⃣ **Cash** (insert money into machine)\n"
                    f"3️⃣ **Card** (swipe/insert card)"
                )
            )
            return []

        dispatcher.utter_message(text=msg)

        # Complete order trong DB: trừ tồn kho + tạo transaction
        if cart:
            order_id = _get_latest_pending_order_id()
            if order_id:
                _db.complete_order(order_id, method, cart)
            else:
                # Fallback: tạo order mới nếu không tìm thấy pending
                new_id = _db.create_order(cart)
                if new_id:
                    _db.complete_order(new_id, method, cart)

        dispatcher.utter_message(
            text="\n🎉 **Payment successful!**\n🥤 Your drink is being dispensed...\nThank you for using our service! 😊"
        )

        return [
            SlotSet("cart", None), SlotSet("drink", None), SlotSet("size", None),
            SlotSet("quantity", "1"), SlotSet("payment_method", None),
            SlotSet("price_per_unit", None), SlotSet("total_price", None),
        ]


class ActionResetOrder(Action):
    def name(self) -> Text:
        return "action_reset_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        order_id = _get_latest_pending_order_id()
        if order_id:
            _db.cancel_order(order_id)

        dispatcher.utter_message(
            text=(
                "❌ Order cancelled.\n\n"
                "Would you like to:\n"
                "🔄 Browse or order something else? → Type a product name or 'menu'\n"
                "👋 Exit? → Type 'goodbye'"
            )
        )
        return [
            SlotSet("cart", None), SlotSet("drink", None), SlotSet("size", None),
            SlotSet("quantity", "1"), SlotSet("payment_method", None),
            SlotSet("price_per_unit", None), SlotSet("total_price", None),
        ]


# ============================================================
# BACKWARDS-COMPATIBLE ACTIONS
# ============================================================

class ActionCalculatePrice(Action):
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
            SlotSet("size", volume), SlotSet("quantity", str(quantity)),
            SlotSet("price_per_unit", str(price_per_unit)), SlotSet("total_price", str(total)),
        ]


class ActionShowOrderSummary(Action):
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
            f"🛒 **CART**\n{'─'*30}\n"
            f"{drink_data['image']} {drink_data['name']} ({size_slot})\n"
            f"   x{quantity} × {int(price_per_unit):,} VND = {int(total_price):,} VND\n"
            f"{'─'*30}\n💵 **Total: {int(total_price):,} VND**\n{'─'*30}\n\n"
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

        best_selling_keywords = [
            "best selling", "most sold", "buy most", "sold most",
            "sell the best", "sell most", "best sales", "top sales",
            "highest sales", "highest selling", "most sales",
            "sells the most", "sells most",
        ]
        if any(w in last_norm for w in best_selling_keywords):
            top3 = _db.get_top_by_sales(limit=3)
            lines = ["🏆 **TOP 3 BEST SELLERS**\n" + "─" * 32]
            for i, d in enumerate(top3, 1):
                lines.append(f"  {i}. {d['image']} {d['name']} — Sold: {d.get('sales', 0):,} units")
            lines.append("\n💬 Which one would you like?")
            dispatcher.utter_message(text="\n".join(lines))
            return []

        if any(w in last_norm for w in ["popular", "famous", "well known", "trending", "most popular"]):
            top3 = _db.get_recommendations(limit=3)
            lines = ["🌟 **TOP 3 MOST POPULAR**\n" + "─" * 32]
            for i, d in enumerate(top3, 1):
                lines.append(f"  {i}. {d['image']} {d['name']} — Popularity: {d['popularity']}/10")
            lines.append("\n💬 Which one would you like?")
            dispatcher.utter_message(text="\n".join(lines))
            return []

        top5 = _db.get_recommendations(limit=5)
        lines = ["🌟 **TODAY'S DRINK RECOMMENDATIONS**\n" + "─" * 35]
        for i, d in enumerate(top5, 1):
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
            "🎉 **CURRENT PROMOTIONS**\n" + "─" * 35 + "\n"
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
                "🍹 Ordering drinks\n💰 Checking product prices\n"
                "🧪 Ingredients / product info\n⭐ Drink recommendations\n\n"
                "👉 Type 'menu' to see the list, or tell me what you'd like to drink!"
            ),
            (
                "🤖 I can't answer that — I only serve drinks!\n\n"
                "Would you like to:\n• View menu → type 'menu'\n"
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