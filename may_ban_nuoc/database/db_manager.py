"""
db_manager.py - Database Manager for Automatic Drink Vending Machine
=====================================================================
Xử lý toàn bộ CRUD operations với SQLite
Thay thế DRINKS_DB hardcode trong actions.py

Cách dùng:
    from database.db_manager import DatabaseManager
    db = DatabaseManager()
    drink = db.get_drink("coca")
    db.update_stock("coca", "330ml", -2)
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional

# ============================================================
# PATH CONFIG
# ============================================================

# Tự động tìm đúng đường dẫn dù gọi từ đâu trong project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH     = os.path.join(BASE_DIR, "vending_machine.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "database", "schema.sql")


# ============================================================
# DATABASE MANAGER
# ============================================================

class DatabaseManager:
    """
    Quản lý toàn bộ tương tác với SQLite database.
    Mỗi method tương ứng với 1 nghiệp vụ cụ thể của máy bán nước.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Tạo connection với cấu hình chuẩn."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row   # truy cập kết quả bằng tên cột: row["name"]
        conn.execute("PRAGMA foreign_keys = ON")  # bật kiểm tra foreign key
        conn.execute("PRAGMA journal_mode = WAL")  # tăng hiệu năng ghi
        return conn

    def _init_db(self):
        """Khởi tạo database từ schema.sql nếu chưa tồn tại."""
        if not os.path.exists(self.db_path):
            print(f"[DB] Tạo database mới: {self.db_path}")
            with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
                schema_sql = f.read()
            conn = self._get_conn()
            conn.executescript(schema_sql)
            conn.commit()
            conn.close()
            print("[DB] Schema khởi tạo thành công.")
        else:
            print(f"[DB] Kết nối database: {self.db_path}")

    # ============================================================
    # PRODUCTS — đọc thông tin sản phẩm
    # ============================================================

    def get_drink(self, product_id: str) -> Optional[dict]:
        """
        Lấy đầy đủ thông tin 1 sản phẩm theo id.
        Tương đương: DRINKS_DB.get("coca")

        Returns dict giống format DRINKS_DB gốc, hoặc None nếu không tìm thấy.
        """
        conn = self._get_conn()
        try:
            # Lấy thông tin sản phẩm
            product = conn.execute(
                "SELECT * FROM products WHERE id = ?", (product_id,)
            ).fetchone()

            if not product:
                return None

            # Lấy giá theo từng size
            prices_rows = conn.execute(
                "SELECT volume, price FROM product_prices WHERE product_id = ? ORDER BY price",
                (product_id,)
            ).fetchall()

            # Lấy danh sách aliases
            aliases_rows = conn.execute(
                "SELECT alias FROM product_aliases WHERE product_id = ?",
                (product_id,)
            ).fetchall()

            # Lấy tồn kho theo từng size
            inventory_rows = conn.execute(
                "SELECT volume, quantity FROM inventory WHERE product_id = ?",
                (product_id,)
            ).fetchall()

            # Build dict giống format DRINKS_DB gốc để actions.py không cần sửa nhiều
            prices = {row["volume"]: row["price"] for row in prices_rows}
            volumes = list(prices.keys())
            aliases = [row["alias"] for row in aliases_rows]
            stock_by_volume = {row["volume"]: row["quantity"] for row in inventory_rows}
            total_stock = sum(stock_by_volume.values())

            return {
                "id": product["id"],
                "name": product["name"],
                "brand": product["brand"],
                "category": product["category"],
                "default_volume": product["default_volume"],
                "volumes": volumes,
                "price": prices,
                "ingredients": product["ingredients"],
                "flavor": product["flavor"],
                "features": product["features"],
                "image": product["image"],
                "is_new": bool(product["is_new"]),
                "has_sugar": bool(product["has_sugar"]),
                "has_caffeine": bool(product["has_caffeine"]),
                "popularity": product["popularity"],
                "expiry_months": product["expiry_months"],
                "aliases": aliases,
                "stock": total_stock,
                "stock_by_volume": stock_by_volume,
                "sales": product["sales"],
            }
        finally:
            conn.close()

    def find_drink_by_alias(self, alias: str) -> Optional[dict]:
        """
        Tìm sản phẩm theo alias (tên gọi khác).
        Tương đương: find_drink() trong actions.py

        Ví dụ: find_drink_by_alias("coke") → trả về dict của "coca"
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT product_id FROM product_aliases WHERE alias = ?",
                (alias.lower().strip(),)
            ).fetchone()

            if not row:
                return None

            return self.get_drink(row["product_id"])
        finally:
            conn.close()

    def get_all_drinks(self) -> list[dict]:
        """
        Lấy toàn bộ sản phẩm — dùng để hiển thị menu.
        Tương đương: DRINKS_DB.items()
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id FROM products ORDER BY category, name"
            ).fetchall()
            return [self.get_drink(row["id"]) for row in rows]
        finally:
            conn.close()

    def get_menu_by_category(self) -> dict:
        """
        Lấy menu nhóm theo category.
        Tương đương: get_menu_list() trong drink_database.py
        """
        drinks = self.get_all_drinks()
        categories = {}
        for drink in drinks:
            cat = drink["category"]
            if cat not in categories:
                categories[cat] = []
            default_vol = drink["default_volume"]
            price = drink["price"][default_vol]
            new_badge = " 🆕" if drink["is_new"] else ""
            categories[cat].append(
                f"  {drink['image']} {drink['name']}{new_badge} ({default_vol}) - {price:,} VND"
            )
        return categories

    def get_recommendations(self, limit: int = 5) -> list[dict]:
        """Lấy top sản phẩm theo popularity, chỉ lấy sản phẩm còn hàng."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT p.id FROM products p
                WHERE (SELECT COALESCE(SUM(i.quantity), 0) FROM inventory i WHERE i.product_id = p.id) > 0
                ORDER BY p.popularity DESC LIMIT ?
            """, (limit,)).fetchall()
            return [self.get_drink(row["id"]) for row in rows]
        finally:
            conn.close()

    def get_new_products(self) -> list[dict]:
        """Lấy sản phẩm mới còn hàng."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT p.id FROM products p
                WHERE p.is_new = 1
                AND (SELECT COALESCE(SUM(i.quantity), 0) FROM inventory i WHERE i.product_id = p.id) > 0
            """).fetchall()
            return [self.get_drink(row["id"]) for row in rows]
        finally:
            conn.close()

    def get_top_by_sales(self, limit: int = 3) -> list[dict]:
        """Lấy top sản phẩm bán chạy nhất theo products.sales, chỉ lấy sản phẩm còn hàng."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT p.id FROM products p
                WHERE (SELECT COALESCE(SUM(i.quantity), 0) FROM inventory i WHERE i.product_id = p.id) > 0
                ORDER BY p.sales DESC LIMIT ?
            """, (limit,)).fetchall()
            return [self.get_drink(row["id"]) for row in rows]
        finally:
            conn.close()

    # ============================================================
    # INVENTORY — quản lý tồn kho (ĐỘNG)
    # ============================================================

    def get_stock(self, product_id: str, volume: str) -> int:
        """
        Lấy số lượng tồn kho của 1 sản phẩm + size cụ thể.

        Ví dụ: get_stock("coca", "330ml") → 120
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT quantity FROM inventory WHERE product_id = ? AND volume = ?",
                (product_id, volume)
            ).fetchone()
            return row["quantity"] if row else 0
        finally:
            conn.close()

    def update_stock(self, product_id: str, volume: str, delta: int) -> bool:
        """
        Cập nhật tồn kho. delta âm = bán ra, delta dương = nhập hàng.

        Ví dụ:
            update_stock("coca", "330ml", -2)  → khách mua 2 lon
            update_stock("coca", "330ml", +50) → nhập thêm 50 lon

        Returns True nếu thành công, False nếu không đủ hàng.
        """
        conn = self._get_conn()
        try:
            current = conn.execute(
                "SELECT quantity FROM inventory WHERE product_id = ? AND volume = ?",
                (product_id, volume)
            ).fetchone()

            if not current:
                print(f"[DB] Không tìm thấy inventory: {product_id} {volume}")
                return False

            new_qty = current["quantity"] + delta

            # Không cho tồn kho xuống dưới 0
            if new_qty < 0:
                print(f"[DB] Không đủ hàng: {product_id} {volume} (hiện có {current['quantity']}, cần {abs(delta)})")
                return False

            conn.execute("""
                UPDATE inventory
                SET quantity = ?, updated_at = ?
                WHERE product_id = ? AND volume = ?
            """, (new_qty, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), product_id, volume))
            conn.commit()
            print(f"[DB] Stock updated: {product_id} {volume} | {current['quantity']} → {new_qty}")
            return True
        finally:
            conn.close()

    def check_stock_available(self, product_id: str, volume: str, quantity: int) -> bool:
        """
        Kiểm tra xem có đủ hàng để bán không.

        Ví dụ: check_stock_available("coca", "330ml", 3) → True/False
        """
        return self.get_stock(product_id, volume) >= quantity

    def get_low_stock_items(self, threshold: int = 10) -> list[dict]:
        """
        Lấy danh sách sản phẩm sắp hết hàng (dùng cho dashboard).
        threshold: ngưỡng cảnh báo (mặc định <= 10)
        """
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT i.product_id, i.volume, i.quantity, p.name, p.image
                FROM inventory i
                JOIN products p ON i.product_id = p.id
                WHERE i.quantity <= ?
                ORDER BY i.quantity ASC
            """, (threshold,)).fetchall()

            return [dict(row) for row in rows]
        finally:
            conn.close()

    # ============================================================
    # ORDERS — quản lý đơn hàng
    # ============================================================

    def create_order(self, cart: list) -> Optional[int]:
        """
        Tạo đơn hàng mới từ cart (giống format cart trong actions.py).

        cart format (giống actions.py):
        [
            {
                "key": "coca",
                "name": "Coca-Cola",
                "image": "🥤",
                "volume": "330ml",
                "qty": 2,
                "unit_price": 12000,
                "subtotal": 24000,
            },
            ...
        ]

        Returns order_id nếu thành công, None nếu thất bại.
        """
        conn = self._get_conn()
        try:
            total = sum(item["subtotal"] for item in cart)

            # Tạo order
            cursor = conn.execute("""
                INSERT INTO orders (status, total_amount, created_at)
                VALUES ('pending', ?, ?)
            """, (total, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

            order_id = cursor.lastrowid

            # Tạo order_items
            for item in cart:
                conn.execute("""
                    INSERT INTO order_items
                        (order_id, product_id, volume, quantity, unit_price, subtotal)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    order_id,
                    item["key"],
                    item["volume"],
                    item["qty"],
                    item["unit_price"],
                    item["subtotal"],
                ))

            conn.commit()
            print(f"[DB] Order #{order_id} tạo thành công | Total: {total:,} VND")
            return order_id

        except Exception as e:
            conn.rollback()
            print(f"[DB] Lỗi tạo order: {e}")
            return None
        finally:
            conn.close()

    def complete_order(self, order_id: int, payment_method: str, cart: list) -> bool:
        """
        Hoàn thành đơn hàng sau khi thanh toán thành công:
        1. Cập nhật orders.status = 'completed'
        2. Trừ tồn kho từng sản phẩm
        3. Tạo transaction record

        Đây là method quan trọng nhất — gọi sau khi payment thành công.
        """
        conn = self._get_conn()
        try:
            # Kiểm tra order tồn tại
            order = conn.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()

            if not order:
                print(f"[DB] Không tìm thấy order #{order_id}")
                return False

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Cập nhật order status
            conn.execute("""
                UPDATE orders
                SET status = 'completed', payment_method = ?, completed_at = ?
                WHERE id = ?
            """, (payment_method, now, order_id))

            # Tạo transaction record
            conn.execute("""
                INSERT INTO transactions
                    (order_id, payment_method, amount, status, created_at)
                VALUES (?, ?, ?, 'success', ?)
            """, (order_id, payment_method, order["total_amount"], now))

            conn.commit()

            # Trừ tồn kho và tăng sales (gọi sau commit để transaction record đã được lưu)
            for item in cart:
                success = self.update_stock(item["key"], item["volume"], -item["qty"])
                if not success:
                    print(f"[DB] ⚠️ Cảnh báo: Không cập nhật được stock cho {item['key']} {item['volume']}")
                # Cộng dồn vào products.sales (tổng tích lũy qua các phiên)
                self.update_sales(item["key"], item["qty"])

            print(f"[DB] Order #{order_id} hoàn thành | Payment: {payment_method}")
            return True

        except Exception as e:
            conn.rollback()
            print(f"[DB] Lỗi complete_order: {e}")
            return False
        finally:
            conn.close()

    def cancel_order(self, order_id: int) -> bool:
        """Huỷ đơn hàng (khách bấm cancel)."""
        conn = self._get_conn()
        try:
            conn.execute("""
                UPDATE orders SET status = 'cancelled'
                WHERE id = ? AND status = 'pending'
            """, (order_id,))
            conn.commit()
            print(f"[DB] Order #{order_id} đã huỷ")
            return True
        except Exception as e:
            conn.rollback()
            print(f"[DB] Lỗi cancel_order: {e}")
            return False
        finally:
            conn.close()

    def get_order(self, order_id: int) -> Optional[dict]:
        """Lấy thông tin 1 đơn hàng kèm chi tiết items."""
        conn = self._get_conn()
        try:
            order = conn.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()

            if not order:
                return None

            items = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
            ).fetchall()

            return {
                "id": order["id"],
                "status": order["status"],
                "total_amount": order["total_amount"],
                "payment_method": order["payment_method"],
                "created_at": order["created_at"],
                "completed_at": order["completed_at"],
                "items": [dict(item) for item in items],
            }
        finally:
            conn.close()

    # ============================================================
    # STATISTICS — dùng cho dashboard (Phase 5)
    # ============================================================

    def get_daily_revenue(self, date: str = None) -> int:
        """
        Tổng doanh thu trong ngày.
        date format: "2024-01-15" (mặc định = hôm nay)
        """
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT COALESCE(SUM(total_amount), 0) as revenue
                FROM orders
                WHERE status = 'completed'
                AND DATE(created_at) = ?
            """, (date,)).fetchone()
            return row["revenue"]
        finally:
            conn.close()

    def get_total_orders_today(self) -> dict:
        """Thống kê đơn hàng hôm nay theo trạng thái."""
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM orders
                WHERE DATE(created_at) = ?
                GROUP BY status
            """, (today,)).fetchall()

            result = {"completed": 0, "cancelled": 0, "pending": 0}
            for row in rows:
                result[row["status"]] = row["count"]
            return result
        finally:
            conn.close()

    # ============================================================
    # ADMIN — thêm/sửa sản phẩm (dùng cho dashboard Phase 5)
    # ============================================================

    def update_sales(self, product_id: str, qty: int) -> bool:
        """Cộng dồn sales vào products.sales (gọi sau mỗi giao dịch thành công)."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE products SET sales = sales + ? WHERE id = ?",
                (qty, product_id)
            )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            print(f"[DB] Lỗi update_sales: {e}")
            return False
        finally:
            conn.close()

    def add_stock(self, product_id: str, volume: str, quantity: int) -> bool:
        """Nhập thêm hàng (tăng tồn kho)."""
        return self.update_stock(product_id, volume, +quantity)

    def update_price(self, product_id: str, volume: str, new_price: int) -> bool:
        """Cập nhật giá sản phẩm."""
        conn = self._get_conn()
        try:
            conn.execute("""
                UPDATE product_prices SET price = ?
                WHERE product_id = ? AND volume = ?
            """, (new_price, product_id, volume))
            conn.commit()
            print(f"[DB] Giá updated: {product_id} {volume} → {new_price:,} VND")
            return True
        except Exception as e:
            conn.rollback()
            print(f"[DB] Lỗi update_price: {e}")
            return False
        finally:
            conn.close()

    def set_product_new_status(self, product_id: str, is_new: bool) -> bool:
        """Đánh dấu sản phẩm là mới hoặc không."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE products SET is_new = ? WHERE id = ?",
                (1 if is_new else 0, product_id)
            )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            print(f"[DB] Lỗi set_product_new_status: {e}")
            return False
        finally:
            conn.close()