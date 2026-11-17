-- Populate the database with initial data.
-- Runs only when the items table is empty, so restarting the stack
-- does not overwrite data stored in the named volume.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM items) THEN
        INSERT INTO items (sku, name, description, quantity, min_quantity, unit_price) VALUES
            ('SCR-M4-20',  'Šroub M4x20',          'Pozinkovaný, balení 100 ks',  120, 50, 89.90),
            ('NUT-M4',     'Matice M4',            'Pozinkovaná, balení 100 ks',   40, 50, 49.90),
            ('DRL-18V',    'Aku vrtačka 18V',      'Včetně 2 baterií a nabíječky',   3,  2, 3290.00),
            ('GLV-L',      'Pracovní rukavice L',  'Nitrilové, pár',                0, 10, 59.00),
            ('TAPE-50',    'Lepicí páska 50 mm',   'Hnědá, 66 m',                  25,  5, 39.90),
            ('SAW-BLADE',  'Pilový list 24T',      'Na dřevo, 190 mm',              1,  3, 249.00);

        INSERT INTO stock_movements (item_id, item_sku, delta, quantity_after, reason)
        SELECT id, sku, quantity, quantity, 'Počáteční stav (seed)'
        FROM items
        WHERE quantity > 0;
    END IF;
END $$;

-- EOF
