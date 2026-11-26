CREATE TABLE customers (
    customer_id SERIAL PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    region VARCHAR(20) CHECK (region IN ('APAC', 'EMEA', 'AMER')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Vertical Fragment (Part B): Sales metrics for employees whose core HR record lives in MySQL (hr_system.employees)
CREATE TABLE sales_reps (
    emp_id INT PRIMARY KEY, -- Logical link to MySQL hr_system.employees(emp_id)
    assigned_region VARCHAR(20) NOT NULL,
    quarterly_quota NUMERIC(12, 2) NOT NULL
);

CREATE TABLE products (
    product_id SERIAL PRIMARY KEY,
    sku VARCHAR(50) UNIQUE NOT NULL,
    product_name VARCHAR(120) NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL CHECK (unit_price >= 0),
    discontinued_notes TEXT
);

-- Horizontal Fragment 1: APAC Orders
CREATE TABLE orders_apac (
    order_id SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(customer_id),
    sales_rep_emp_id INT NOT NULL REFERENCES sales_reps(emp_id),
    warehouse_code VARCHAR(20) NOT NULL, -- Logical link to SQLite legacy_ops.db
    order_date DATE NOT NULL,
    status VARCHAR(30) DEFAULT 'PENDING'
);

-- Horizontal Fragment 2: EMEA Orders
CREATE TABLE orders_emea (
    order_id SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(customer_id),
    sales_rep_emp_id INT NOT NULL REFERENCES sales_reps(emp_id),
    warehouse_code VARCHAR(20) NOT NULL,
    order_date DATE NOT NULL,
    status VARCHAR(30) DEFAULT 'PENDING'
);

CREATE TABLE order_items (
    item_id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES orders_apac(order_id) ON DELETE CASCADE,
    product_id INT NOT NULL REFERENCES products(product_id),
    quantity INT NOT NULL CHECK (quantity > 0)
);

CREATE INDEX idx_orders_apac_date ON orders_apac(order_date);

-- Seed Data
INSERT INTO customers (full_name, email, region) VALUES
('Aarav Sharma', 'aarav@example.com', 'APAC'),
('Elena Rostova', 'elena@example.com', 'EMEA');

INSERT INTO sales_reps (emp_id, assigned_region, quarterly_quota) VALUES
(101, 'APAC', 150000.00),
(102, 'EMEA', 180000.00);

INSERT INTO products (sku, product_name, unit_price, discontinued_notes) VALUES
('SKU-101', 'Cloud Router X1', 249.99, NULL),
('SKU-102', 'Legacy Switch S4', 89.50, 'Replaced by S5 in Q3');

INSERT INTO orders_apac (customer_id, sales_rep_emp_id, warehouse_code, order_date, status)
VALUES (1, 101, 'JAI-01', '2026-10-15', 'SHIPPED');

INSERT INTO orders_emea (customer_id, sales_rep_emp_id, warehouse_code, order_date, status)
VALUES (2, 102, 'FRA-02', '2026-10-18', 'DELIVERED');

INSERT INTO order_items (order_id, product_id, quantity) VALUES (1, 1, 2);