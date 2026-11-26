param(
    [switch]$CatalogOnly
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($CatalogOnly) {
    Write-Host "==> Wiping ONLY metabase_catalog..." -ForegroundColor Yellow
    docker exec -i metabase_catalog_db psql -U metabase_admin -d metabase_catalog -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
}
else {
    Write-Host "==> 1/3 Tearing down all Docker containers, volumes, and legacy_ops.db..." -ForegroundColor Yellow
    docker compose down -v
    Remove-Item ".\legacy_ops.db" -ErrorAction SilentlyContinue

    Write-Host "==> 2/3 Starting fresh containers and seeding target databases..." -ForegroundColor Cyan
    .\start_demo.ps1
}

Write-Host "==> 3/3 Initializing metabase_catalog schema (13 tables + team users)..." -ForegroundColor Cyan
Get-Content ".\init_catalog.sql" | docker exec -i metabase_catalog_db psql -U metabase_admin -d metabase_catalog

Write-Host "`nAll clean! Ready for a fresh sync at http://localhost:8000" -ForegroundColor Green