-- shopdemo: a small e-commerce database used as an "external" project connection in tests and demos.
-- Run it against an empty database (tests/fixtures/shopdemo.py does that idempotently).
-- ~300 customers, 40 products, 2000 orders spread over the last 12 months, items and refunds. Deterministic apart from "now()".

SELECT setseed(0.42);

CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded');
CREATE TYPE customer_segment AS ENUM ('consumer', 'smb', 'enterprise');

CREATE TABLE customers (
  id          SERIAL PRIMARY KEY,
  email       TEXT NOT NULL UNIQUE,
  first_name  TEXT NOT NULL,
  last_name   TEXT NOT NULL,
  phone       TEXT,
  city        TEXT,
  country     TEXT NOT NULL,
  segment     customer_segment NOT NULL DEFAULT 'consumer',
  created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE products (
  id           SERIAL PRIMARY KEY,
  sku          TEXT NOT NULL UNIQUE,
  name         TEXT NOT NULL,
  category     TEXT NOT NULL,
  price_cents  INTEGER NOT NULL CHECK (price_cents > 0),
  active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE orders (
  id               SERIAL PRIMARY KEY,
  customer_id      INTEGER NOT NULL REFERENCES customers(id),
  status           order_status NOT NULL,
  channel          TEXT NOT NULL,
  currency         TEXT NOT NULL DEFAULT 'USD',
  total_cents      INTEGER NOT NULL DEFAULT 0,
  shipping_address TEXT,
  created_at       TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_orders_customer ON orders (customer_id);
CREATE INDEX ix_orders_created ON orders (created_at);

CREATE TABLE order_items (
  id               SERIAL PRIMARY KEY,
  order_id         INTEGER NOT NULL REFERENCES orders(id),
  product_id       INTEGER NOT NULL REFERENCES products(id),
  quantity         INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_cents INTEGER NOT NULL
);
CREATE INDEX ix_order_items_order ON order_items (order_id);

CREATE TABLE refunds (
  id            SERIAL PRIMARY KEY,
  order_id      INTEGER NOT NULL REFERENCES orders(id),
  amount_cents  INTEGER NOT NULL,
  reason        TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL
);

INSERT INTO customers (email, first_name, last_name, phone, city, country, segment, created_at)
SELECT 'customer' || g || '@example.com',
       (ARRAY['Ava','Liam','Noah','Mia','Zoe','Ethan','Ivy','Leo','Nora','Owen'])[1 + (g % 10)],
       (ARRAY['Smith','Jones','Brown','Taylor','Wilson','Davies','Evans','Thomas','Roberts','Walker'])[1 + ((g / 3) % 10)],
       '+1555' || lpad((1000000 + g)::text, 7, '0'),
       (ARRAY['Austin','Denver','Boston','Seattle','Chicago','Miami'])[1 + (g % 6)],
       (ARRAY['US','US','US','CA','GB','DE'])[1 + (g % 6)],
       (ARRAY['consumer','consumer','consumer','smb','enterprise'])[1 + (g % 5)]::customer_segment,
       now() - (random() * 400 || ' days')::interval
FROM generate_series(1, 300) g;

INSERT INTO products (sku, name, category, price_cents, active, created_at)
SELECT 'SKU-' || lpad(g::text, 4, '0'),
       (ARRAY['Classic','Premium','Compact','Deluxe','Eco'])[1 + (g % 5)] || ' ' ||
       (ARRAY['Jacket','Lamp','Headphones','Backpack','Serum','Mug','Sneakers','Speaker'])[1 + (g % 8)] || ' ' || g,
       (ARRAY['Apparel','Home','Electronics','Outdoors','Beauty'])[1 + (g % 5)],
       500 + (g * 137) % 24500,
       g % 13 <> 0,
       now() - interval '420 days'
FROM generate_series(1, 40) g;

INSERT INTO orders (customer_id, status, channel, currency, shipping_address, created_at)
SELECT 1 + floor(random() * 300)::int,
       (CASE WHEN r < 0.05 THEN 'pending' WHEN r < 0.12 THEN 'cancelled' WHEN r < 0.18 THEN 'refunded'
             WHEN r < 0.30 THEN 'paid' WHEN r < 0.45 THEN 'shipped' ELSE 'delivered' END)::order_status,
       (ARRAY['web','web','web','mobile','mobile','store'])[1 + floor(random() * 6)::int],
       'USD',
       (100 + g) || ' Market Street',
       now() - (random() * 365 || ' days')::interval
FROM (SELECT g, random() AS r FROM generate_series(1, 2000) g) s;

INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents)
SELECT o.id, p.id, 1 + floor(random() * 3)::int, p.price_cents
FROM orders o
CROSS JOIN LATERAL generate_series(1, 1 + (o.id % 4)) n
JOIN LATERAL (SELECT id, price_cents FROM products WHERE id = 1 + ((o.id * 7 + n * 13) % 40)) p ON TRUE;

UPDATE orders o SET total_cents = t.total
FROM (SELECT order_id, sum(quantity * unit_price_cents)::int AS total FROM order_items GROUP BY order_id) t
WHERE t.order_id = o.id;

INSERT INTO refunds (order_id, amount_cents, reason, created_at)
SELECT o.id,
       CASE WHEN o.id % 3 = 0 THEN o.total_cents ELSE o.total_cents / 2 END,
       (ARRAY['damaged','wrong_item','late','changed_mind'])[1 + (o.id % 4)],
       o.created_at + ((2 + (o.id % 20)) || ' days')::interval
FROM orders o
WHERE o.status = 'refunded' OR (o.status = 'delivered' AND o.id % 17 = 0);
