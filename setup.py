"""
setup.py -- Crea la estructura de carpetas del pipeline FINRA Short Interest.
Ejecutar una sola vez al clonar el repo.
"""

import io
import os
import sys
import json
from pathlib import Path
from datetime import datetime

# Forzar UTF-8 en stdout (necesario en Windows con Python 3.7+)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# -- Raiz del proyecto (donde vive este script) --------------------------------
ROOT = Path(__file__).parent

# -- Carpetas a crear ----------------------------------------------------------
FOLDERS = [
    "data/raw",          # bulk files originales de FINRA (.parquet por fecha)
    "data/processed",    # datos limpios filtrados NYSE/NASDAQ por fecha
    "data/history",      # serie historica acumulada (shares + float %)
    "data/latest",       # snapshot mas reciente (sobreescrito en cada run)
    "data/cache",        # float_shares cacheados de Yahoo Finance
    "pipeline",          # modulos Python del pipeline
    "docs",              # index.html -> GitHub Pages
    ".github/workflows", # GitHub Actions
    "logs",              # run_log por ejecucion
]

# -- registry.json -- controla que fechas ya fueron procesadas -----------------
REGISTRY_PATH = ROOT / "data" / "registry.json"
REGISTRY_TEMPLATE = {
    "last_updated": None,
    "processed_dates": [],
    "pipeline_a": {
        "last_run": None,
        "last_date": None,
        "total_tickers": 0
    },
    "pipeline_b": {
        "last_run": None,
        "last_date": None,
        "total_tickers": 0,
        "float_cache_size": 0
    }
}


def create_folders():
    print("[+] Creando estructura de carpetas...")
    for folder in FOLDERS:
        path = ROOT / folder
        path.mkdir(parents=True, exist_ok=True)
        gitkeep = path / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")
        print(f"    OK  {folder}/")


def create_registry():
    if REGISTRY_PATH.exists():
        print("\n[!] registry.json ya existe -- no se sobreescribe.")
        return
    print("\n[+] Creando registry.json...")
    REGISTRY_PATH.write_text(json.dumps(REGISTRY_TEMPLATE, indent=2), encoding="utf-8")
    print("    OK  data/registry.json")


def create_gitignore():
    gitignore_path = ROOT / ".gitignore"
    content = (
        "# Python\n"
        "__pycache__/\n"
        "*.py[cod]\n"
        ".env\n"
        "venv/\n"
        ".venv/\n\n"
        "# Data -- raw y processed no se suben a git (pesados)\n"
        "data/raw/\n"
        "data/processed/\n"
        "data/cache/\n\n"
        "# Si se suben al repo:\n"
        "# data/history/       <- serie historica acumulada\n"
        "# data/latest/        <- snapshot mas reciente\n"
        "# data/registry.json  <- estado del pipeline\n\n"
        "# Logs\n"
        "logs/\n\n"
        "# OS\n"
        ".DS_Store\n"
    )
    gitignore_path.write_text(content, encoding="utf-8")
    print("\n[+] Creando .gitignore...")
    print("    OK  .gitignore")


def create_requirements():
    req_path = ROOT / "requirements.txt"
    content = (
        "requests==2.31.0\n"
        "pandas==2.2.2\n"
        "pyarrow==16.0.0\n"
        "yfinance==0.2.40\n"
        "APScheduler==3.10.4\n"
        "python-dotenv==1.0.1\n"
    )
    req_path.write_text(content, encoding="utf-8")
    print("\n[+] Creando requirements.txt...")
    print("    OK  requirements.txt")


def print_summary():
    sep = "-" * 55
    print("\n" + sep)
    print("SETUP COMPLETO. Estructura creada:")
    print("""
finra-short-interest/
+-- setup.py                   <- este script
+-- requirements.txt
+-- .gitignore
|
+-- pipeline/
|   +-- extract.py             <- descarga FINRA
|   +-- validate.py            <- quality checks
|   +-- transform.py           <- limpieza y filtros
|   +-- analyze.py             <- ATH / Near High
|   +-- float_fetcher.py       <- Yahoo Finance float
|   +-- report.py              <- genera index.html
|   +-- run_pipeline.py        <- orquestador principal
|
+-- data/
|   +-- raw/                   <- bulk FINRA sin tocar
|   +-- processed/             <- datos limpios
|   +-- history/               <- serie acumulada
|   +-- latest/                <- snapshot actual
|   +-- cache/                 <- float cacheados
|   +-- registry.json          <- control de estado
|
+-- docs/
|   +-- index.html             <- GitHub Pages
|
+-- logs/
|
+-- .github/workflows/
    +-- pipeline.yml           <- GitHub Actions cron
""")
    print(sep)
    print("Siguiente paso:  pip install -r requirements.txt")
    print("Luego ejecutar:  python pipeline/run_pipeline.py")
    print(sep)


if __name__ == "__main__":
    sep = "=" * 55
    print(sep)
    print("  FINRA Short Interest Pipeline -- Setup")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(sep)

    create_folders()
    create_registry()
    create_gitignore()
    create_requirements()
    print_summary()
