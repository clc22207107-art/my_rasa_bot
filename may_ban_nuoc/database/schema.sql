-- ============================================================
-- SCHEMA - AUTOMATIC DRINK VENDING MACHINE
-- SQLite3
-- ============================================================
-- Chạy file này 1 lần duy nhất để khởi tạo database:
--   python -c "import sqlite3; conn=sqlite3.connect('vending_machine.db'); conn.executescript(open('database/schema.sql').read())"
-- Hoặc chạy qua db_manager.py (khuyến nghị)
-- ============================================================


-- ============================================================
-- BẢNG 1: products
-- Lưu thông tin tĩnh của sản phẩm
-- Tương đương với các field không đổi trong DRINKS_DB
-- ============================================================
CREATE TABLE IF NOT EXISTS products (
    id              TEXT PRIMARY KEY,   -- key trong DRINKS_DB: "coca", "pepsi", ...
    name            TEXT NOT NULL,      -- "Coca-Cola"
    brand           TEXT NOT NULL,      -- "Coca-Cola Company"
    category        TEXT NOT NULL,      -- "Carbonated Soft Drinks"
    default_volume  TEXT NOT NULL,      -- "330ml"
    ingredients     TEXT NOT NULL,      -- chuỗi thành phần
    flavor          TEXT NOT NULL,      -- mô tả vị
    features        TEXT NOT NULL,      -- đặc điểm nổi bật
    image           TEXT NOT NULL,      -- emoji: "🥤"
    is_new          INTEGER NOT NULL DEFAULT 0,  -- 0=False, 1=True
    has_sugar       INTEGER NOT NULL DEFAULT 1,  -- 0=False, 1=True
    has_caffeine    INTEGER NOT NULL DEFAULT 0,  -- 0=False, 1=True
    popularity      REAL NOT NULL DEFAULT 0.0,   -- 0.0 -> 10.0
    expiry_months   INTEGER NOT NULL DEFAULT 12,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);


-- ============================================================
-- BẢNG 2: product_prices
-- Lưu giá theo từng size (1 sản phẩm có thể có nhiều size)
-- Tương đương: "price": {"330ml": 12000, "500ml": 15000, "1.5L": 28000}
-- ============================================================
CREATE TABLE IF NOT EXISTS product_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,          -- "coca"
    volume      TEXT NOT NULL,          -- "330ml"
    price       INTEGER NOT NULL,       -- 12000 (VND)
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    UNIQUE (product_id, volume)         -- mỗi cặp product+volume là duy nhất
);


-- ============================================================
-- BẢNG 3: product_aliases
-- Lưu các tên gọi khác của sản phẩm để nhận dạng
-- Tương đương: "aliases": ["coca", "coca cola", "coke", ...]
-- ============================================================
CREATE TABLE IF NOT EXISTS product_aliases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,          -- "coca"
    alias       TEXT NOT NULL,          -- "coke"
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    UNIQUE (alias)                      -- mỗi alias chỉ thuộc 1 sản phẩm
);


-- ============================================================
-- BẢNG 4: inventory
-- Lưu tồn kho — CẬP NHẬT ĐỘNG mỗi khi có giao dịch
-- Tách khỏi products vì thay đổi liên tục
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,          -- "coca"
    volume      TEXT NOT NULL,          -- "330ml"
    quantity    INTEGER NOT NULL DEFAULT 0,  -- số lượng hiện tại
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    UNIQUE (product_id, volume)         -- mỗi cặp product+volume là duy nhất
);


-- ============================================================
-- BẢNG 5: orders
-- Mỗi lần khách nhấn "confirm" → tạo 1 order
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 'pending'   : đang chờ thanh toán (sau khi confirm)
    -- 'completed' : đã thanh toán thành công
    -- 'cancelled' : khách huỷ
    total_amount    INTEGER NOT NULL DEFAULT 0,     -- tổng tiền (VND)
    payment_method  TEXT,               -- 'cash', 'card', 'qr' — NULL nếu chưa thanh toán
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    completed_at    TEXT                -- NULL nếu chưa hoàn thành
);


-- ============================================================
-- BẢNG 6: order_items
-- Chi tiết từng sản phẩm trong 1 đơn hàng
-- Tương đương cart[] trong actions.py
-- ============================================================
CREATE TABLE IF NOT EXISTS order_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL,       -- thuộc order nào
    product_id  TEXT NOT NULL,          -- "coca"
    volume      TEXT NOT NULL,          -- "330ml"
    quantity    INTEGER NOT NULL,       -- 2
    unit_price  INTEGER NOT NULL,       -- 12000
    subtotal    INTEGER NOT NULL,       -- 24000
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id)
);


-- ============================================================
-- BẢNG 7: transactions
-- Lịch sử thanh toán — ghi lại mỗi lần xử lý payment
-- ============================================================
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL,
    payment_method  TEXT NOT NULL,      -- 'cash', 'card', 'qr'
    amount          INTEGER NOT NULL,   -- số tiền thực tế thanh toán
    status          TEXT NOT NULL DEFAULT 'success',
    -- 'success' : thanh toán thành công
    -- 'failed'  : thất bại (ví dụ không đủ tiền mặt)
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);


-- ============================================================
-- INDEX — tăng tốc truy vấn thường dùng
-- ============================================================

-- Tìm sản phẩm theo category (hiển thị menu theo nhóm)
CREATE INDEX IF NOT EXISTS idx_products_category
    ON products(category);

-- Tìm alias nhanh (dùng trong find_drink())
CREATE INDEX IF NOT EXISTS idx_aliases_alias
    ON product_aliases(alias);

-- Tìm giá theo product_id (dùng rất thường xuyên)
CREATE INDEX IF NOT EXISTS idx_prices_product
    ON product_prices(product_id);

-- Tìm tồn kho theo product_id
CREATE INDEX IF NOT EXISTS idx_inventory_product
    ON inventory(product_id);

-- Tìm order theo status (lọc pending/completed)
CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders(status);

-- Tìm order_items theo order_id (load chi tiết đơn hàng)
CREATE INDEX IF NOT EXISTS idx_order_items_order
    ON order_items(order_id);

-- Tìm transactions theo order_id
CREATE INDEX IF NOT EXISTS idx_transactions_order
    ON transactions(order_id);