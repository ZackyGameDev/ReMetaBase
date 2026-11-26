```markdown
# MetaBase — Quick Setup & Run Guide (Windows)

## 1. Prerequisites (WSL 2, Docker & Python)
1. **Enable WSL 2** (open PowerShell as Administrator and restart if prompted):
```powershell
   wsl --install

```

2. **Install Docker Desktop:**
* Download and install **Docker Desktop for Windows** (keep **"Use WSL 2 instead of Hyper-V"** checked during installation).
* Launch Docker Desktop and wait until the bottom status bar shows **Engine running**.


3. **Install Python 3.11+:**
* Install Python from python.org (check **"Add python.exe to PATH"**).



---

## 2. Clone & Install Dependencies

```powershell
git clone <your-repo-url>
cd ReMetaBase

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

```

---

## 3. Spin Up Databases & Seed Data

Start the 3 Docker containers (`metabase_catalog` on `5432`, `erp_production` on `5433`, `hr_system` on `3307`), create `legacy_ops.db` (SQLite), and initialize the 13 catalog tables:

```powershell
.\start_demo.ps1
Get-Content .\init_catalog.sql | docker exec -i metabase_catalog_db psql -U metabase_admin -d metabase_catalog

```

*(To wipe and reset all 4 databases cleanly at any time, run `.\reset_demo.ps1`.)*

---

## 4. Run MetaBase

```powershell
cd backend
uvicorn main:app --reload --port 8000

```

Open **`http://localhost:8000`** in your browser and click **Check & Commit Schema Changes** to initialize `v1` across all sources.

```

```