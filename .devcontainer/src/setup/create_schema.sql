-- Create the database schema (idempotent, safe to run on every start)

CREATE TABLE IF NOT EXISTS items (
    id           SERIAL PRIMARY KEY,
    sku          VARCHAR(32)    NOT NULL UNIQUE,
    name         VARCHAR(100)   NOT NULL,
    description  TEXT,
    quantity     INT            NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    min_quantity INT            NOT NULL DEFAULT 0 CHECK (min_quantity >= 0),
    unit_price   NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    created_at   TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ    NOT NULL DEFAULT now()
);

-- Audit log of stock movements. Records survive deletion of the item
-- (item_id is set to NULL, item_sku keeps the reference readable).
CREATE TABLE IF NOT EXISTS stock_movements (
    id             SERIAL PRIMARY KEY,
    item_id        INT          REFERENCES items(id) ON DELETE SET NULL,
    item_sku       VARCHAR(32)  NOT NULL,
    delta          INT          NOT NULL CHECK (delta <> 0),
    quantity_after INT          NOT NULL CHECK (quantity_after >= 0),
    reason         VARCHAR(255) NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stock_movements_item_id    ON stock_movements (item_id);
CREATE INDEX IF NOT EXISTS idx_stock_movements_created_at ON stock_movements (created_at DESC);

-- EOF
