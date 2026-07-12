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


def resolve_volume(drink_data: dict, size_slot: str):
    """Return matched volume string, or None if a specific size was requested but not found."""
    if not size_slot:
        return drink_data["volumes"][0]   # default: smallest size
    size_lower = size_slot.lower()
    for vol in drink_data["volumes"]:
        if vol.lower() in size_lower or size_lower in vol.lower():
            return vol
    size_norm = normalize_text(size_slot)
    if any(w in size_norm for w in ["large", "big", "xl"]):
        return drink_data["volumes"][-1]
    if any(w in size_norm for w in ["small", "sm"]):
        return drink_data["volumes"][0]
    # Specific size requested but not matched → signal caller to reject
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
    "ask_price":       "price",
    "ask_ingredients": "ingredients",
    "ask_brand":       "brand",
    "ask_flavor":      "flavor",
    "ask_stock":       "stock",
    "ask_features":    "features",
    "ask_expiry":      "expiry",
    "ask_category":    "category",
    "ask_size":        "size",
    "ask_popularity":  "popularity",
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


class ActionHandleOutOfDomain(Action):
    def name(self) -> Text:
        return "action_handle_out_of_domain"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(text="Sorry, I only handle drink orders. Say menu to see drinks or ask about a product.")
        return []