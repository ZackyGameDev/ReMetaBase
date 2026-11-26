-- 1. Core Hierarchy
CREATE TABLE data_source (
    source_id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    db_type VARCHAR(30) NOT NULL,
    host VARCHAR(255),
    port INT,
    database_name VARCHAR(255) NOT NULL,
    username VARCHAR(100),
    password_enc TEXT,
    status VARCHAR(30) DEFAULT 'ACTIVE',
    last_extracted_at TIMESTAMP
);

CREATE TABLE schema_meta (
    schema_id SERIAL PRIMARY KEY,
    source_id INT NOT NULL REFERENCES data_source(source_id) ON DELETE CASCADE,
    schema_name VARCHAR(100) NOT NULL,
    UNIQUE (source_id, schema_name)
);

CREATE TABLE table_meta (
    table_id SERIAL PRIMARY KEY,
    schema_id INT NOT NULL REFERENCES schema_meta(schema_id) ON DELETE CASCADE,
    table_name VARCHAR(150) NOT NULL,
    table_type VARCHAR(30) DEFAULT 'BASE TABLE',
    fragmentation_type VARCHAR(30) DEFAULT 'NONE',
    fragment_predicate VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE (schema_id, table_name)
);

-- Logical fragment groups can contain multiple physical tables.
CREATE TABLE table_fragment (
    fragment_id SERIAL PRIMARY KEY,
    logical_name VARCHAR(120) NOT NULL,
    fragment_type VARCHAR(30) NOT NULL CHECK (fragment_type IN ('HORIZONTAL', 'VERTICAL')),
    table_id INT NOT NULL REFERENCES table_meta(table_id) ON DELETE CASCADE,
    predicate VARCHAR(255),
    UNIQUE (logical_name, fragment_type, table_id)
);

-- 2. Cross-Dialect Type Normalization (Populated dynamically on sync)
CREATE TABLE type_mapping (
    type_mapping_id SERIAL PRIMARY KEY,
    dialect VARCHAR(30) NOT NULL,
    native_type VARCHAR(100) NOT NULL,
    unified_type VARCHAR(50) NOT NULL,
    UNIQUE (dialect, native_type)
);

CREATE TABLE column_meta (
    column_id SERIAL PRIMARY KEY,
    table_id INT NOT NULL REFERENCES table_meta(table_id) ON DELETE CASCADE,
    type_mapping_id INT REFERENCES type_mapping(type_mapping_id),
    column_name VARCHAR(150) NOT NULL,
    raw_type VARCHAR(100) NOT NULL,
    ordinal_position INT NOT NULL,
    is_primary_key BOOLEAN DEFAULT FALSE,
    is_nullable BOOLEAN DEFAULT TRUE,
    is_active BOOLEAN DEFAULT TRUE,
    dialect_props JSONB DEFAULT '{}'::jsonb,
    UNIQUE (table_id, column_name)
);

-- 3. Foreign Keys & Cross-DB Lineage
CREATE TABLE column_references (
    ref_id SERIAL PRIMARY KEY,
    child_column_id INT NOT NULL REFERENCES column_meta(column_id) ON DELETE CASCADE,
    parent_column_id INT NOT NULL REFERENCES column_meta(column_id) ON DELETE CASCADE,
    constraint_name VARCHAR(150),
    is_logical_cross_db BOOLEAN DEFAULT FALSE,
    UNIQUE (child_column_id, parent_column_id)
);

-- 4. Indexes
CREATE TABLE index_meta (
    index_id SERIAL PRIMARY KEY,
    table_id INT NOT NULL REFERENCES table_meta(table_id) ON DELETE CASCADE,
    index_name VARCHAR(150) NOT NULL,
    is_unique BOOLEAN DEFAULT FALSE,
    UNIQUE (table_id, index_name)
);

CREATE TABLE index_columns (
    index_id INT NOT NULL REFERENCES index_meta(index_id) ON DELETE CASCADE,
    column_id INT NOT NULL REFERENCES column_meta(column_id) ON DELETE CASCADE,
    position INT NOT NULL,
    PRIMARY KEY (index_id, column_id)
);

-- 5. Versioning & Schema Diff Log
CREATE TABLE schema_snapshot (
    snapshot_id SERIAL PRIMARY KEY,
    source_id INT NOT NULL REFERENCES data_source(source_id) ON DELETE CASCADE,
    version_no INT NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE schema_change_log (
    change_id SERIAL PRIMARY KEY,
    snapshot_id INT NOT NULL REFERENCES schema_snapshot(snapshot_id) ON DELETE CASCADE,
    object_type VARCHAR(30) NOT NULL,
    object_name VARCHAR(255) NOT NULL,
    change_type VARCHAR(30) NOT NULL,
    old_value TEXT,
    new_value TEXT
);

-- 6. Operational Profiling
CREATE TABLE operational_metric (
    metric_id SERIAL PRIMARY KEY,
    table_id INT NOT NULL REFERENCES table_meta(table_id) ON DELETE CASCADE,
    row_count BIGINT DEFAULT 0,
    null_fraction NUMERIC(5, 4) DEFAULT 0.0,
    bloat_pct NUMERIC(5, 2) DEFAULT 0.0,
    refresh_status VARCHAR(30) DEFAULT 'HEALTHY',
    profiled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. Users & Business Annotations
CREATE TABLE app_user (
    user_id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    role VARCHAR(30) DEFAULT 'STEWARD'
);

CREATE TABLE business_annotation (
    annotation_id SERIAL PRIMARY KEY,
    user_id INT REFERENCES app_user(user_id),
    table_id INT REFERENCES table_meta(table_id) ON DELETE CASCADE,
    column_id INT REFERENCES column_meta(column_id) ON DELETE CASCADE,
    data_owner VARCHAR(100),
    purpose TEXT,
    definition TEXT,
    tags JSONB DEFAULT '[]'::jsonb,
    CHECK (table_id IS NOT NULL OR column_id IS NOT NULL)
);

-- Seed Team Members
INSERT INTO app_user (name, email, role) VALUES
('Aayan Gohar', 'aayangohar@jklu.edu.in', 'ADMIN'),
('Arijeet Basak', 'arijeetbasak@jklu.edu.in', 'ARCHITECT'),
('Kartik Gupta', 'kartikgupta@jklu.edu.in', 'ENGINEER'),
('Sukanksha Saxena', 'sukankshasaxena@jklu.edu.in', 'STEWARD');
