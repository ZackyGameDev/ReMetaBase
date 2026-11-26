-- Apply once to existing metabase_catalog databases to enable fragment groups.
CREATE TABLE IF NOT EXISTS table_fragment (
    fragment_id SERIAL PRIMARY KEY,
    logical_name VARCHAR(120) NOT NULL,
    fragment_type VARCHAR(30) NOT NULL CHECK (fragment_type IN ('HORIZONTAL', 'VERTICAL')),
    table_id INT NOT NULL REFERENCES table_meta(table_id) ON DELETE CASCADE,
    predicate VARCHAR(255),
    UNIQUE (logical_name, fragment_type, table_id)
);
