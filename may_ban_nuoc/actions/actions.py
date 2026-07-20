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

EWALLET_INFO = {
    "momo":    {"number": "0901 234 567", "name": "NGUYEN VAN A",   "emoji": "💜", "label": "MoMo"},
    "zalopay": {"number": "0901 234 567", "name": "NGUYEN VAN A",   "emoji": "🔵", "label": "ZaloPay"},
    "vnpay":   {"number": "1234 5678 90", "name": "AUTOMATIC VENDING", "emoji": "🔴", "label": "VNPay"},
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




def resolve_drink(tracker):
    """Resolve the drink the user is asking about, using NLU entities first."""
    entities = tracker.latest_message.get("entities", [])
    drink_ents = [e for e in entities if e.get("entity") == "drink"]
    if drink_ents:
        key, data = find_drink(drink_ents[0].get("value", ""))
        if data:
            return key, data
    drink_slot = tracker.get_slot("drink")
    return find_drink(drink_slot)


def drinks_from_entities(tracker) -> List[tuple]:
    """
    Build [(key, drink_data, qty)] from NLU entities in the latest message.
    Pairs each drink entity with the nearest preceding (or following) quantity entity.
    """
    entities = tracker.latest_message.get("entities", [])
    drink_ents = sorted(
        [e for e in entities if e.get("entity") == "drink"],
        key=lambda e: e.get("start", 0),
    )
    qty_ents = sorted(
        [e for e in entities if e.get("entity") == "quantity"],
        key=lambda e: e.get("start", 0),
    )

    results = []
    for d_ent in drink_ents:
        key, drink_data = find_drink(d_ent.get("value", ""))
        if not drink_data:
            continue

        d_start = d_ent.get("start", 0)
        d_end = d_ent.get("end", d_start)

        # Closest quantity that ends before this drink starts (preceding)
        preceding = [q for q in qty_ents if q.get("end", 0) <= d_start]
        # Closest quantity that starts after this drink ends (following)
        following = [q for q in qty_ents if q.get("start", 0) >= d_end]

        qty = 1
        if preceding:
            qty = parse_quantity(preceding[-1].get("value", "1"))
        elif following:
            qty = parse_quantity(following[0].get("value", "1"))

        results.append((key, drink_data, qty))

    return results


def parse_quantity(qty_str: str) -> int:
    if not qty_str:
        return 1
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a": 1,
    }
    qty_lower = qty_str.lower()
    # Strip size patterns (330ml, 500 ml, 1.5l, 1 liter...) before looking for numbers
    qty_clean = re.sub(r'\d+[\.,]?\d*\s*(?:ml|l|liter|liters|litre|litres)\b', '', qty_lower)
    for word, num in words.items():
        if word in qty_clean.split():
            return num
    numbers = re.findall(r'\d+', qty_clean)
    return int(numbers[0]) if numbers else 1


_SMALL_WORDS = {
    "small", "mini", "tiny", "little", "compact", "sm", "petite", "short",
    "smaller", "smallest", "x-small", "xsmall", "xs", "bite", "lite",
}
_LARGE_WORDS = {
    "large", "big", "tall", "jumbo", "xl", "xxl", "grand", "grande",
    "bigger", "biggest", "larger", "largest", "huge", "giant", "max",
    "maxi", "king", "family", "super", "mega", "extra large",
}
_MEDIUM_WORDS = {
    "medium", "mid", "standard", "middle", "moderate", "regular",
    "normal", "average", "med", "medium-sized",
}

def _sort_volumes(volumes: list) -> list:
    def to_ml(v):
        v = v.lower().strip()
        if "l" in v and "ml" not in v:
            return float(re.sub(r"[^0-9.]", "", v)) * 1000
        return float(re.sub(r"[^0-9.]", "", v))
    return sorted(volumes, key=to_ml)

def resolve_volume(drink_data: dict, size_slot: str):
    """Map size slot (relative word or ml string) → actual volume string for this drink."""
    if not size_slot:
        return drink_data["volumes"][0]
    size_lower = size_slot.lower().strip()
    # Direct ml/L match first
    for vol in drink_data["volumes"]:
        if vol.lower() in size_lower or size_lower in vol.lower():
            return vol
    # Relative size words
    sorted_vols = _sort_volumes(drink_data["volumes"])
    n = len(sorted_vols)
    if size_lower in _SMALL_WORDS:
        return sorted_vols[0]
    if size_lower in _LARGE_WORDS:
        return sorted_vols[-1]
    if size_lower in _MEDIUM_WORDS:
        return sorted_vols[n // 2] if n >= 3 else sorted_vols[0]
    # No match → signal caller
    return None


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
        entities = tracker.latest_message.get("entities", [])
        info_types = [e["value"].lower() for e in entities if e["entity"] == "info_type"]
        if any(t in info_types for t in ["new", "arrived", "is_new"]):
            new_products = _db.get_new_products()
            if not new_products:
                dispatcher.utter_message(text="No new products right now. Say menu to see all drinks.")
                return []
            names = ", ".join(d["name"] for d in new_products)
            dispatcher.utter_message(text=f"New products: {names}. Say a name to order or get info.")
            return []

        categories = _db.get_menu_by_category()
        all_names = []
        for cat, items in categories.items():
            for i in items:
                part = re.sub(r'^\S+\s+', '', i.strip())
                part = re.sub(r'\s*🆕?\s*\(.*$', '', part)
                all_names.append(part.strip())
        dispatcher.utter_message(text="Menu: " + ", ".join(all_names))
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

        # Với ask_product_info: dùng resolve_drink() bình thường
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


# ============================================================
# UNIFIED PRODUCT QUERY (entity-based multi-info_type)
# ============================================================

# Map info_type entity value → (label, value_fn)
# value_fn(drink_data) → string
def _info_price(d):
    prices = ", ".join(f"{v}: {p:,} VND" for v, p in d["price"].items())
    return f"Price: {prices}"

def _info_stock(d):
    s = d["stock"]
    if s == 0:    status = "❌ Out of stock"
    elif s < 10:  status = f"⚠️ Almost out — only {s} remaining"
    else:         status = f"✅ {s} in stock"
    return f"📦 Stock: {status}"

def _info_carbonated(d):
    has = d.get("category") in ["Carbonated Soft Drinks", "Energy Drinks"]
    return "🫧 Carbonated." if has else "✅ Not carbonated."

_INFO_HANDLERS = {
    "price":       _info_price,
    "ingredients": lambda d: f"🧪 Ingredients: {d['ingredients']}",
    "brand":       lambda d: f"🏭 Brand: {d['brand']}",
    "stock":       _info_stock,
    "flavor":      lambda d: f"😋 Flavor: {d['flavor']}",
    "size":        lambda d: "📦 Sizes: " + ", ".join(f"{v}: {d['price'][v]:,} VND" for v in d["volumes"]),
    "category":    lambda d: f"🏷️ Category: {d['category']}",
    "features":    lambda d: f"✨ Features: {d['features']}",
    "expiry":      lambda d: f"📅 Shelf life: {d['expiry_months']} months from production date",
    "caffeine":    lambda d: "☕ Contains caffeine." if d["has_caffeine"] else "✅ Caffeine-free.",
    "sugar":       lambda d: "🍬 Contains sugar." if d["has_sugar"] else "✅ Sugar-free.",
    "carbonated":  _info_carbonated,
    "popularity":  lambda d: f"Popularity: {d['popularity']}/10. Total sold: {d.get('sales', 0):,} units.",
    "is_new":      lambda d: "🆕 New product!" if d["is_new"] else "✅ Established product.",
}

_INTENT_TO_INFO = {
    "ask_price":         "price",
    "ask_ingredients":   "ingredients",
    "ask_brand":         "brand",
    "ask_flavor":        "flavor",
    "ask_stock":         "stock",
    "ask_features":      "features",
    "ask_expiry":        "expiry",
    "ask_category":      "category",
    "ask_size":          "size",
    "ask_popularity":    "popularity",
    "ask_sugar_content": "sugar",
    "ask_caffeine_info": "caffeine",
}

class ActionAnswerProductQuery(Action):
    def name(self) -> Text:
        return "action_answer_product_query"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        _, drink_data = resolve_drink(tracker)
        if not drink_data:
            dispatcher.utter_message(text="Which product would you like info on? (Type 'menu' to browse)")
            return []

        intent = tracker.get_intent_of_latest_message()
        info_key = _INTENT_TO_INFO.get(intent)
        if info_key:
            handler = _INFO_HANDLERS[info_key]
            dispatcher.utter_message(text=f"{drink_data['name']}: {handler(drink_data)}")
            return []

        # ask_product_info: multi-info query — use info_type entities
        entities = tracker.latest_message.get("entities", [])
        info_types = list(dict.fromkeys(
            e["value"].lower() for e in entities if e["entity"] == "info_type"
        ))
        parts = [h(drink_data) for t in info_types if (h := _INFO_HANDLERS.get(t))]
        if parts:
            dispatcher.utter_message(text=f"{drink_data['name']}: " + " ".join(parts))
        else:
            dispatcher.utter_message(text=f"What would you like to know about {drink_data['name']}? "
                                     f"(price, ingredients, brand, flavor, size, features, stock, expiry, category)")
        return []


# ============================================================
# CART-BASED ORDER FLOW
# ============================================================

class ActionAddToCart(Action):
    def name(self) -> Text:
        return "action_add_to_cart"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        # Size from current message entities only (not persisted slot from previous turns)
        current_entities = tracker.latest_message.get("entities", [])
        size_slot = next((e["value"] for e in current_entities if e["entity"] == "size"), None)
        cart = get_cart(tracker)

        # Primary path: drink entities extracted by DrinkEntityMasker + DIET
        found_items = drinks_from_entities(tracker)

        if not found_items:
            # Fallback: drink slot was set in a previous turn
            drink_slot = tracker.get_slot("drink")
            key, drink_data = find_drink(drink_slot)
            if not drink_data and cart:
                key = cart[-1]["key"]
                drink_data = _db.get_drink(key)
            if not drink_data:
                dispatcher.utter_message(text="❌ I couldn't find that product. Type 'menu' to see the full list!")
                return []
            qty_ents = [e for e in current_entities if e.get("entity") == "quantity"]
            qty_val = qty_ents[0].get("value", "1") if qty_ents else tracker.get_slot("quantity") or "1"
            found_items = [(key, drink_data, parse_quantity(qty_val))]

        added_lines = []
        last_key = None

        for key, drink_data, qty in found_items:
            volume = resolve_volume(drink_data, size_slot)

            if volume is None:
                available = ", ".join(drink_data["volumes"])
                dispatcher.utter_message(
                    text=f"❌ **{drink_data['name']}** doesn't come in **{size_slot}**. "
                         f"Available sizes: {available}. Which size would you like?"
                )
                continue

            # Kiểm tra tồn kho thực tế từ DB
            if not _db.check_stock_available(key, volume, qty):
                current_stock = _db.get_stock(key, volume)
                if current_stock == 0:
                    # Tìm size khác còn hàng
                    alt = next(
                        (v for v in drink_data["volumes"] if v != volume and _db.get_stock(key, v) > 0),
                        None
                    )
                    if alt:
                        alt_stock = _db.get_stock(key, alt)
                        dispatcher.utter_message(
                            text=f"😔 **{drink_data['name']}** ({volume}) is out of stock. "
                                 f"But {alt} is available ({alt_stock} units). Would you like that instead?"
                        )
                    else:
                        dispatcher.utter_message(
                            text=f"😔 **{drink_data['name']}** is completely out of stock. Would you like to choose something else?"
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

        dispatcher.utter_message(text="Added to cart: " + ", ".join(l.replace("  ✅ ", "") for l in added_lines))
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
        items_str = ", ".join(f"{i['qty']}x {i['name']} ({i['volume']})" for i in cart)
        total = cart_total(cart)
        dispatcher.utter_message(text=f"Cart: {items_str}. Total: {total:,} VND. Say confirm to order.")
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
        items_str = ", ".join(f"{i['qty']}x {i['name']}" for i in cart)
        msg = f"Order confirmed: {items_str}. Total: {total:,} VND. How would you like to pay? Cash, card, or bank transfer?"
        dispatcher.utter_message(text=msg)
        return [
            SlotSet("cart", json.dumps(cart, ensure_ascii=False)),
            SlotSet("total_price", str(total)),
        ]


class ActionProcessPayment(Action):
    def name(self) -> Text:
        return "action_process_payment"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        method = (tracker.get_slot("payment_method") or "").lower().strip()

        cart = get_cart(tracker)
        total_price = cart_total(cart) if cart else int(tracker.get_slot("total_price") or 0)

        if method in ("momo", "zalopay", "vnpay"):
            info = EWALLET_INFO[method]
            msg = f"Transfer {total_price:,} VND to {info['label']} number {info['number']} ({info['name']}). Your order will be dispensed after payment."
        elif method == "e-wallet":
            msg = f"Which e-wallet? MoMo, ZaloPay, or VNPay? Total: {total_price:,} VND."
        elif method == "bank transfer":
            qr = BANK_QR_INFO
            msg = f"Bank transfer {total_price:,} VND to {qr['bank']} account {qr['account']} ({qr['name']}). Order dispensed after transfer."
        elif method == "cash":
            msg = f"Please insert {total_price:,} VND into the cash slot. The machine will return change automatically."
        elif method == "card":
            msg = f"Please insert or swipe your card. Amount: {total_price:,} VND."
        else:
            dispatcher.utter_message(text=f"Total: {total_price:,} VND. How would you like to pay? Cash, card, or bank transfer?")
            return []

        dispatcher.utter_message(text=msg)

        # Complete order trong DB: trừ tồn kho + tạo transaction
        if cart:
            order_id = _get_latest_pending_order_id()
            if order_id:
                _db.complete_order(order_id, method, cart)
            else:
                new_id = _db.create_order(cart)
                if new_id:
                    _db.complete_order(new_id, method, cart)

        dispatcher.utter_message(text="Payment successful! Your drink is being dispensed. Thank you!")

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

        dispatcher.utter_message(text="Order cancelled. Say menu to browse or goodbye to exit.")
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
        last_msg = tracker.latest_message.get("text", "")
        _, drink_data = find_drink(drink_slot)
        if not drink_data:
            return []
        volume = resolve_volume(drink_data, size_slot)
        if volume is None:
            available = ", ".join(drink_data["volumes"])
            dispatcher.utter_message(
                text=f"❌ **{drink_data['name']}** doesn't come in **{size_slot}**. "
                     f"Available sizes: {available}. Which size would you like?"
            )
            return []
        qty_from_msg = parse_quantity(last_msg)
        quantity_slot = tracker.get_slot("quantity") or "1"
        quantity = qty_from_msg if qty_from_msg > 1 else parse_quantity(quantity_slot)
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
        msg = f"Cart: {quantity}x {drink_data['name']} ({size_slot}). Total: {int(total_price):,} VND. Say confirm or cancel."
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
        top3 = _db.get_top_by_sales(limit=3)
        parts = [f"{d['name']} ({d.get('sales', 0):,} sold)" for d in top3]
        dispatcher.utter_message(text=f"Top 3 best sellers: {', '.join(parts)}. Which one would you like?")
        return []


class ActionCheckPromotion(Action):
    def name(self) -> Text:
        return "action_check_promotion"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(text="Current promotions: buy 2 get 1 free on Sting and Number 1. 10% off for 5+ items. New products 15% off. Say a product name to order!")
        return []


class ActionCompareDrinks(Action):
    def name(self) -> Text:
        return "action_compare_drinks"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        entities = tracker.latest_message.get("entities", [])
        drink_vals = [e["value"] for e in entities if e["entity"] == "drink"]

        if len(drink_vals) < 2:
            dispatcher.utter_message(text="Please name two drinks to compare! e.g. 'Compare Coke vs Pepsi'")
            return []

        _, d1 = find_drink(drink_vals[0])
        _, d2 = find_drink(drink_vals[1])

        if not d1:
            dispatcher.utter_message(text=f"Sorry, I couldn't find '{drink_vals[0]}' in our menu.")
            return []
        if not d2:
            dispatcher.utter_message(text=f"Sorry, I couldn't find '{drink_vals[1]}' in our menu.")
            return []

        p1 = min(d1["price"].values()) if d1.get("price") else 0
        p2 = min(d2["price"].values()) if d2.get("price") else 0
        cheaper = d1["name"] if p1 <= p2 else d2["name"]

        pop1 = d1.get("sales", 0)
        pop2 = d2.get("sales", 0)
        more_pop = d1["name"] if pop1 >= pop2 else d2["name"]

        caff = lambda d: "☕ Yes" if d.get("has_caffeine") else "✅ No"
        sugar = lambda d: "🍬 Yes" if d.get("has_sugar") else "✅ No"

        lines = [
            f"📊 **{d1['name']} vs {d2['name']}**",
            f"💵 Price: from {p1:,}đ  |  from {p2:,}đ  → **{cheaper}** is cheaper",
            f"☕ Caffeine: {caff(d1)}  |  {caff(d2)}",
            f"🍬 Sugar: {sugar(d1)}  |  {sugar(d2)}",
            f"🏷️ Category: {d1.get('category','N/A')}  |  {d2.get('category','N/A')}",
            f"🏆 More popular: **{more_pop}** ({max(pop1, pop2):,} sold)",
        ]
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionRemoveFromCart(Action):
    def name(self) -> Text:
        return "action_remove_from_cart"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        cart = get_cart(tracker)
        if not cart:
            dispatcher.utter_message(text="🛒 Your cart is already empty.")
            return []

        entities = tracker.latest_message.get("entities", [])
        drink_entity = next((e["value"] for e in entities if e["entity"] == "drink"), None)
        size_entity  = next((e["value"] for e in entities if e["entity"] == "size"), None)
        if not drink_entity:
            dispatcher.utter_message(text="Which drink would you like to remove? Please specify the name.")
            return []

        drink_key, drink_data = find_drink(drink_entity)
        if not drink_data:
            dispatcher.utter_message(text=f"I couldn't find '{drink_entity}' in your cart.")
            return []

        # Size-aware removal: if size given, only remove that specific volume
        if size_entity and drink_data:
            target_volume = resolve_volume(drink_data, size_entity)
            if target_volume:
                new_cart = [item for item in cart
                            if not (item["key"] == drink_key and item["volume"] == target_volume)]
                if len(new_cart) == len(cart):
                    dispatcher.utter_message(
                        text=f"{drink_data['name']} ({target_volume}) is not in your cart.")
                    return []
            else:
                new_cart = [item for item in cart if item["key"] != drink_key]
        else:
            new_cart = [item for item in cart if item["key"] != drink_key]

        if len(new_cart) == len(cart):
            dispatcher.utter_message(text=f"{drink_data['name']} is not in your cart.")
            return []

        dispatcher.utter_message(text=f"✅ Removed {drink_data['name']} from your cart.")
        if new_cart:
            items_str = ", ".join(f"{i['qty']}x {i['name']} ({i['volume']})" for i in new_cart)
            total = cart_total(new_cart)
            dispatcher.utter_message(text=f"Cart: {items_str}. Total: {total:,} VND. Say confirm to order.")
        else:
            dispatcher.utter_message(text="🛒 Your cart is now empty.")

        return [SlotSet("cart", json.dumps(new_cart, ensure_ascii=False) if new_cart else None)]


class ActionClearCart(Action):
    def name(self) -> Text:
        return "action_clear_cart"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(text="🗑️ Your cart has been cleared. What would you like to order?")
        return [
            SlotSet("cart", None),
            SlotSet("drink", None),
            SlotSet("size", None),
            SlotSet("quantity", "1"),
        ]


_CATEGORY_ALIASES: Dict[str, List[str]] = {
    "energy":          ["Energy Drinks"],
    "energy drink":    ["Energy Drinks"],
    "energy drinks":   ["Energy Drinks"],
    "cola":            ["Carbonated Soft Drinks"],
    "soda":            ["Carbonated Soft Drinks"],
    "carbonated":      ["Carbonated Soft Drinks"],
    "soft drink":      ["Carbonated Soft Drinks"],
    "soft drinks":     ["Carbonated Soft Drinks"],
    "sparkling":       ["Carbonated Soft Drinks"],
    "tea":             ["Bottled Tea", "Herbal Tea"],
    "green tea":       ["Bottled Tea"],
    "bottled tea":     ["Bottled Tea"],
    "herbal tea":      ["Herbal Tea"],
    "coffee":          ["Canned Coffee"],
    "canned coffee":   ["Canned Coffee"],
    "milk":            ["Milk", "Plant-Based Milk"],
    "dairy":           ["Milk"],
    "soy milk":        ["Plant-Based Milk"],
    "soy":             ["Plant-Based Milk"],
    "plant based":     ["Plant-Based Milk"],
    "plant-based":     ["Plant-Based Milk"],
    "water":           ["Mineral Water", "Purified Water"],
    "mineral water":   ["Mineral Water"],
    "purified water":  ["Purified Water"],
    "juice":           ["Fruit Juice", "Coconut Water / Fruit Juice"],
    "fruit juice":     ["Fruit Juice"],
    "coconut":         ["Coconut Water / Fruit Juice"],
    "coconut water":   ["Coconut Water / Fruit Juice"],
    "herbal":          ["Herbal Drinks", "Herbal Tea"],
    "herbal drink":    ["Herbal Drinks"],
    "herbal drinks":   ["Herbal Drinks"],
    "yogurt":          ["Drinkable Yogurt"],
    "probiotic":       ["Drinkable Yogurt"],
    "electrolyte":     ["Electrolyte Drinks"],
    "sports":          ["Electrolyte Drinks"],
    "sport":           ["Electrolyte Drinks"],
    "isotonic":        ["Electrolyte Drinks"],
}


class ActionCheapestDrinks(Action):
    def name(self) -> Text:
        return "action_cheapest_drinks"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        drinks = _db.get_all_drinks()
        price_list = []
        for d in drinks:
            if d.get("price"):
                min_price = min(d["price"].values())
                min_vol   = _sort_volumes(d["volumes"])[0]
                price_list.append((min_price, d["name"], d.get("image", "🥤"), min_vol))
        price_list.sort(key=lambda x: x[0])
        top5 = price_list[:5]
        lines = ["💰 Most affordable drinks:"]
        for price, name, img, vol in top5:
            lines.append(f"  {img} {name} ({vol}) — {price:,} VND")
        lines.append("Would you like to order any of these?")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionDrinkByCategory(Action):
    def name(self) -> Text:
        return "action_drink_by_category"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        entities = tracker.latest_message.get("entities", [])
        drink_val    = next((e["value"] for e in entities if e["entity"] == "drink"), None)
        category_val = next((e["value"] for e in entities if e["entity"] == "category"), None)

        # --- Case 1: user asks about a specific drink name ("any coca?") ---
        if drink_val:
            drink_key, drink_data = find_drink(drink_val)
            if drink_data:
                vols = ", ".join(drink_data.get("volumes", []))
                min_price = min(drink_data["price"].values()) if drink_data.get("price") else 0
                dispatcher.utter_message(
                    text=f"✅ Yes! We have {drink_data['name']} {drink_data.get('image','🥤')}. "
                         f"Available in: {vols}. Starting from {min_price:,} VND. Want to order?")
            else:
                dispatcher.utter_message(
                    text=f"❌ Sorry, we don't carry '{drink_val}' right now. "
                         f"Say 'menu' to see what's available!")
            return []

        # --- Case 2: user asks about a category ("any energy drinks?") ---
        if not category_val:
            dispatcher.utter_message(
                text="Which drink or category are you looking for? "
                     "E.g. 'any coca', 'any energy drinks', 'show me milk options'.")
            return []

        cat_lower = category_val.lower().strip()
        target_cats = _CATEGORY_ALIASES.get(cat_lower)

        if not target_cats:
            for alias, cats in _CATEGORY_ALIASES.items():
                if alias in cat_lower or cat_lower in alias:
                    target_cats = cats
                    break

        if not target_cats:
            dispatcher.utter_message(
                text=f"Sorry, I don't recognise '{category_val}'. "
                     f"Try: energy drinks, milk, tea, coffee, water, juice, cola, soda.")
            return []

        drinks = _db.get_all_drinks()
        matches = [d for d in drinks if d.get("category") in target_cats]

        if not matches:
            dispatcher.utter_message(text=f"Sorry, no {category_val} drinks available right now.")
            return []

        names = ", ".join(f"{d.get('image','🥤')} {d['name']}" for d in matches)
        dispatcher.utter_message(
            text=f"🏷️ {category_val.title()} options: {names}. Which one would you like?")
        return []


class ActionNewProducts(Action):
    def name(self) -> Text:
        return "action_new_products"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        drinks = _db.get_all_drinks()
        new_drinks = [d for d in drinks if d.get("is_new", False)]
        if new_drinks:
            names = ", ".join(f"{d.get('image','🥤')} {d['name']}" for d in new_drinks)
            dispatcher.utter_message(text=f"🆕 New arrivals: {names}. Would you like to try any?")
        else:
            top5 = sorted(drinks, key=lambda d: d.get("popularity", 0), reverse=True)[:5]
            names = ", ".join(f"{d.get('image','🥤')} {d['name']}" for d in top5)
            dispatcher.utter_message(
                text=f"🔥 No new arrivals right now! Our current top picks: {names}. Want one?")
        return []


class ActionCaffeineFreeDrinks(Action):
    def name(self) -> Text:
        return "action_caffeine_free_drinks"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        drinks = _db.get_all_drinks()
        cf_drinks = [d for d in drinks if not d.get("has_caffeine", True)]
        if not cf_drinks:
            dispatcher.utter_message(text="Sorry, all drinks here contain some caffeine.")
            return []
        names = ", ".join(f"{d.get('image','🥤')} {d['name']}" for d in cf_drinks)
        dispatcher.utter_message(
            text=f"☕ Caffeine-free options: {names}. All safe if you're avoiding caffeine! Which one?")
        return []


# ── SUPPORTED PAYMENT METHODS ────────────────────────────────
_SUPPORTED_PAYMENTS = {
    "cash", "money", "tiền mặt",
    "card", "credit card", "visa", "mastercard", "atm",
    "momo", "mo mo",
    "zalopay", "zalo pay", "zalo",
    "vnpay", "vn pay",
    "qr", "qr code",
    "e-wallet", "ewallet", "wallet",
    "bank transfer", "banking", "internet banking",
}


class ActionPriceRange(Action):
    def name(self) -> Text:
        return "action_price_range"

    def run(self, dispatcher, tracker, domain):
        entities = tracker.latest_message.get("entities", [])
        limit_val = next((e["value"] for e in entities if e["entity"] == "price_limit"), None)
        if not limit_val:
            # fallback: scan raw text for numbers
            raw = tracker.latest_message.get("text", "")
            nums = re.findall(r"\d[\d,\.]*", raw)
            if not nums:
                dispatcher.utter_message(text="Please tell me your budget. E.g. 'drinks under 10000' or 'anything below 20k'.")
                return []
            limit_val = nums[-1]

        # normalise: "10k" / "10,000" → float
        limit_str = str(limit_val).lower().replace(",", "").replace(".", "").replace("k", "000").strip()
        try:
            limit = float(re.sub(r"[^\d]", "", limit_str))
        except ValueError:
            dispatcher.utter_message(text="Could not read the price limit. Please use a number, e.g. 10000 or 15k.")
            return []

        drinks = _db.get_all_drinks()
        matches = []
        for d in drinks:
            if d.get("price"):
                min_p = min(d["price"].values())
                if min_p <= limit:
                    matches.append((min_p, d))
        matches.sort(key=lambda x: x[0])

        if not matches:
            dispatcher.utter_message(text=f"Sorry, no drinks available under {int(limit):,} VND right now.")
            return []

        lines = [f"💰 Drinks under {int(limit):,} VND:"]
        for price, d in matches:
            vol = _sort_volumes(d["volumes"])[0]
            lines.append(f"  {d.get('image','🥤')} {d['name']} ({vol}) — {price:,} VND")
        lines.append("Which one would you like?")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionIngredientCheck(Action):
    def name(self) -> Text:
        return "action_ingredient_check"

    def run(self, dispatcher, tracker, domain):
        entities = tracker.latest_message.get("entities", [])
        drink_val      = next((e["value"] for e in entities if e["entity"] == "drink"), None)
        ingredient_val = next((e["value"] for e in entities if e["entity"] == "ingredient"), None)

        if not drink_val:
            dispatcher.utter_message(text="Which drink do you want to check? E.g. 'does coca have sugar'.")
            return []
        if not ingredient_val:
            dispatcher.utter_message(text="Which ingredient are you looking for? E.g. 'does pepsi have caffeine'.")
            return []

        _, drink_data = find_drink(drink_val)
        if not drink_data:
            dispatcher.utter_message(text=f"Sorry, I couldn't find '{drink_val}' in our menu.")
            return []

        ingredients_str = drink_data.get("ingredients", "")
        keyword = ingredient_val.lower().strip()
        found = keyword in ingredients_str.lower()

        if found:
            dispatcher.utter_message(
                text=f"✅ Yes! {drink_data['name']} contains {ingredient_val}.\n"
                     f"📋 Full ingredients: {ingredients_str}")
        else:
            dispatcher.utter_message(
                text=f"❌ No, {drink_data['name']} does not appear to contain {ingredient_val}.\n"
                     f"📋 Its ingredients are: {ingredients_str}")
        return []


class ActionPaymentCheck(Action):
    def name(self) -> Text:
        return "action_payment_check"

    def run(self, dispatcher, tracker, domain):
        entities = tracker.latest_message.get("entities", [])
        pm_val = next((e["value"] for e in entities if e["entity"] == "payment_method"), None)
        if not pm_val:
            raw = tracker.latest_message.get("text", "").lower()
            # try to find any payment keyword in raw text
            pm_val = next((w for w in raw.split() if len(w) > 3), None)

        if not pm_val:
            dispatcher.utter_message(text="Which payment method are you asking about?")
            return []

        pm_lower = pm_val.lower().strip()
        supported = any(s in pm_lower or pm_lower in s for s in _SUPPORTED_PAYMENTS)

        if supported:
            dispatcher.utter_message(
                text=f"✅ Yes, we accept {pm_val}! You can use it at checkout.")
        else:
            dispatcher.utter_message(
                text=f"❌ Sorry, we don't support {pm_val}.\n"
                     f"💳 Accepted methods: Cash · Card (Visa/Mastercard/ATM) · MoMo · ZaloPay · VNPay · QR Code · Bank Transfer.")
        return []


class ActionMostPopular(Action):
    def name(self) -> Text:
        return "action_most_popular"

    def run(self, dispatcher, tracker, domain):
        entities = tracker.latest_message.get("entities", [])
        # optional: top N from message
        raw = tracker.latest_message.get("text", "")
        nums = re.findall(r"\b(\d+)\b", raw)
        top_n = int(nums[0]) if nums and 1 <= int(nums[0]) <= 10 else 5

        drinks = _db.get_all_drinks()
        ranked = sorted(drinks, key=lambda d: (d.get("sales", 0), d.get("popularity", 0)), reverse=True)[:top_n]

        lines = [f"🏆 Top {top_n} most popular drinks:"]
        for i, d in enumerate(ranked, 1):
            sales = d.get("sales", 0)
            pop   = d.get("popularity", 0)
            min_p = min(d["price"].values()) if d.get("price") else 0
            lines.append(f"  {i}. {d.get('image','🥤')} {d['name']} — {sales:,} sold · {pop}/10 · from {min_p:,} VND")
        lines.append("Want to order one of these?")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionDrinkCount(Action):
    def name(self) -> Text:
        return "action_drink_count"

    def run(self, dispatcher, tracker, domain):
        drinks = _db.get_all_drinks()
        total  = len(drinks)
        cats   = {}
        for d in drinks:
            c = d.get("category", "Other")
            cats[c] = cats.get(c, 0) + 1
        cat_summary = ", ".join(f"{c}: {n}" for c, n in sorted(cats.items(), key=lambda x: -x[1]))
        dispatcher.utter_message(
            text=f"🛒 We currently carry {total} different drinks!\n"
                 f"📊 By category: {cat_summary}.\nSay 'menu' to see them all!")
        return []


class ActionNoSugar(Action):
    def name(self) -> Text:
        return "action_no_sugar"

    def run(self, dispatcher, tracker, domain):
        drinks = _db.get_all_drinks()
        no_sugar = [d for d in drinks if not d.get("has_sugar", True)]
        if not no_sugar:
            dispatcher.utter_message(text="Sorry, all our drinks currently contain some sugar.")
            return []
        names = ", ".join(f"{d.get('image','🥤')} {d['name']}" for d in no_sugar)
        dispatcher.utter_message(
            text=f"🥗 Sugar-free drinks ({len(no_sugar)} options): {names}.\nAll have zero added sugar! Which one?")
        return []


class ActionCheapestInCategory(Action):
    def name(self) -> Text:
        return "action_cheapest_in_category"

    def run(self, dispatcher, tracker, domain):
        entities = tracker.latest_message.get("entities", [])
        cat_val = next((e["value"] for e in entities if e["entity"] == "category"), None)

        if not cat_val:
            dispatcher.utter_message(text="Which category? E.g. 'cheapest energy drink', 'cheapest milk'.")
            return []

        cat_lower   = cat_val.lower().strip()
        target_cats = _CATEGORY_ALIASES.get(cat_lower)
        if not target_cats:
            for alias, cats in _CATEGORY_ALIASES.items():
                if alias in cat_lower or cat_lower in alias:
                    target_cats = cats
                    break

        if not target_cats:
            dispatcher.utter_message(text=f"I don't recognise '{cat_val}'. Try: energy, milk, tea, coffee, water, juice.")
            return []

        drinks  = _db.get_all_drinks()
        matches = [d for d in drinks if d.get("category") in target_cats and d.get("price")]
        if not matches:
            dispatcher.utter_message(text=f"No {cat_val} drinks found in our menu right now.")
            return []

        ranked = sorted(matches, key=lambda d: min(d["price"].values()))[:3]
        lines  = [f"💰 Cheapest {cat_val} options:"]
        for d in ranked:
            min_p = min(d["price"].values())
            vol   = _sort_volumes(d["volumes"])[0]
            lines.append(f"  {d.get('image','🥤')} {d['name']} ({vol}) — {min_p:,} VND")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionDrinkByBrand(Action):
    def name(self) -> Text:
        return "action_by_brand"

    def run(self, dispatcher, tracker, domain):
        drinks = _db.get_all_drinks()
        # collect all brands from DB
        brands = {d.get("brand", "").lower(): d.get("brand", "") for d in drinks if d.get("brand")}
        raw_text = tracker.latest_message.get("text", "").lower()

        matched_brand = None
        for b_lower, b_orig in brands.items():
            if b_lower and b_lower in raw_text:
                matched_brand = b_orig
                break

        if not matched_brand:
            brand_list = ", ".join(sorted(set(brands.values())))
            dispatcher.utter_message(
                text=f"Which brand are you looking for? Available brands: {brand_list}.")
            return []

        matches = [d for d in drinks if d.get("brand", "").lower() == matched_brand.lower()]
        if not matches:
            dispatcher.utter_message(text=f"No drinks found for brand '{matched_brand}'.")
            return []

        names = ", ".join(f"{d.get('image','🥤')} {d['name']}" for d in matches)
        dispatcher.utter_message(
            text=f"🏭 {matched_brand} products: {names}. Which one would you like?")
        return []


class ActionHandleOutOfDomain(Action):
    def name(self) -> Text:
        return "action_handle_out_of_domain"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(text="Sorry, I only handle drink orders. Say menu to see drinks or ask about a product.")
        return []