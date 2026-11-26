# start_demo.ps1 - Boots MetaBase demo databases cleanly
$ErrorActionPreference = "Stop"

Write-Host "Starting PostgreSQL and MySQL containers..." -ForegroundColor Cyan
docker compose up -d

if (-not (Test-Path ".\legacy_ops.db")) {
    Write-Host "Generating SQLite target database (legacy_ops.db)..." -ForegroundColor Cyan
    python -c @"
import sqlite3
conn = sqlite3.connect('legacy_ops.db')
conn.executescript('''
CREATE TABLE warehouse_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_code TEXT NOT NULL,
    supervisor_emp_id INTEGER NOT NULL,
    temperature_c REAL,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO warehouse_logs (warehouse_code, supervisor_emp_id, temperature_c) VALUES
('JAI-01', 103, 22.4),
('FRA-02', 103, NULL);
''')
conn.close()
"@
    Write-Host "legacy_ops.db created!" -ForegroundColor Green
} else {
    Write-Host "legacy_ops.db already exists, skipping creation." -ForegroundColor Yellow
}

Write-Host "`nContainer Status:" -ForegroundColor Cyan
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"