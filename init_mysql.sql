CREATE TABLE departments (
    dept_id INT AUTO_INCREMENT PRIMARY KEY,
    dept_name VARCHAR(80) NOT NULL UNIQUE,
    budget DECIMAL(12, 2) NOT NULL
);

-- Vertical Fragment (Part A): Core HR identity linked to PostgreSQL sales_reps via emp_id
CREATE TABLE employees (
    emp_id INT PRIMARY KEY,
    dept_id INT NOT NULL,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    is_active TINYINT(1) DEFAULT 1,
    employment_type ENUM('FULL_TIME', 'CONTRACT', 'INTERN') NOT NULL,
    hire_date DATETIME NOT NULL,
    FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
);

INSERT INTO departments (dept_name, budget) VALUES
('Enterprise Sales', 500000.00),
('Supply Chain Ops', 320000.00);

INSERT INTO employees (emp_id, dept_id, first_name, last_name, is_active, employment_type, hire_date) VALUES
(101, 1, 'Rohan', 'Verma', 1, 'FULL_TIME', '2025-06-01 09:00:00'),
(102, 1, 'Priya', 'Nair', 1, 'FULL_TIME', '2025-08-15 09:00:00'),
(103, 2, 'Kabir', 'Mehta', 1, 'CONTRACT', '2026-01-10 09:00:00');