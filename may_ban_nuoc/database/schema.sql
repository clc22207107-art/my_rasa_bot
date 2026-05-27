-- ============================================================
-- SCHEMA - AUTOMATIC DRINK VENDING MACHINE
-- SQLite3
-- ============================================================

-- ============================================================
-- BẢNG 1: products
-- ============================================================
CREATE TABLE IF NOT EXISTS products (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    brand           TEXT NOT NULL,
    category        TEXT NOT NULL,
    default_volume  TEXT NOT NULL,
    ingredients     TEXT NOT NULL,
    flavor          TEXT NOT NULL,
    features        TEXT NOT NULL,
    image           TEXT NOT NULL,
    is_new          INTEGER NOT NULL DEFAULT 0,
    has_sugar       INTEGER NOT NULL DEFAULT 1,
    has_caffeine    INTEGER NOT NULL DEFAULT 0,
    popularity      REAL NOT NULL DEFAULT 0.0,
    sales           INTEGER NOT NULL DEFAULT 0,
    expiry_months   INTEGER NOT NULL DEFAULT 12,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ============================================================
-- BẢNG 2: product_prices
-- ============================================================
CREATE TABLE IF NOT EXISTS product_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,
    volume      TEXT NOT NULL,
    price       INTEGER NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    UNIQUE (product_id, volume)
);

-- ============================================================
-- BẢNG 3: product_aliases
-- ============================================================
CREATE TABLE IF NOT EXISTS product_aliases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,
    alias       TEXT NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    UNIQUE (alias)
);

-- ============================================================
-- BẢNG 4: inventory
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,
    volume      TEXT NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    UNIQUE (product_id, volume)
);

-- ============================================================
-- BẢNG 5: orders
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    status          TEXT NOT NULL DEFAULT 'pending',
    total_amount    INTEGER NOT NULL DEFAULT 0,
    payment_method  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    completed_at    TEXT
);

-- ============================================================
-- BẢNG 6: order_items
-- ============================================================
CREATE TABLE IF NOT EXISTS order_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL,
    product_id  TEXT NOT NULL,
    volume      TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    unit_price  INTEGER NOT NULL,
    subtotal    INTEGER NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id)
);

-- ============================================================
-- BẢNG 7: transactions
-- ============================================================
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL,
    payment_method  TEXT NOT NULL,
    amount          INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'success',
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

-- ============================================================
-- INDEX
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON product_aliases(alias);
CREATE INDEX IF NOT EXISTS idx_prices_product ON product_prices(product_id);
CREATE INDEX IF NOT EXISTS idx_inventory_product ON inventory(product_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_transactions_order ON transactions(order_id);