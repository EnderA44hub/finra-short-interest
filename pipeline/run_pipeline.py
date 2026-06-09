"""
run_pipeline.py — Orquestador principal.
Ejecuta todos los pasos en orden y registra el resultado en logs/run_log.csv
"""

import csv
import sys
import traceback
from pathlib import Path
from datetime import datetime

ROOT     = Path(__file__).parent.parent
LOGS_DIR = ROOT / "logs"
LOG_FILE = LOGS_DIR / "run_log.csv"

# Agregar pipeline/ al path
sys.path.insert(0, str(Path(__file__).parent))

from extract       import run_extract
from validate      import run_validate
from transform     import run_transform
from float_fetcher import fetch_float
from analyze       import run_analyze_shares, run_analyze_float
from report        import run_report


def log_run(status: str, rows: int, duration: float, error: str = ""):
    LOGS_DIR.mkdir(exist_ok=True)
    write_header = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp", "status", "rows", "duration_sec", "error"])
        writer.writerow([datetime.now().isoformat(), status, rows, f"{duration:.1f}", error])


def run():
    print("=" * 55)
    print("  FINRA Short Interest Pipeline")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    start   = datetime.now()
    rows    = 0

    try:
        # ── 1. EXTRACT ────────────────────────────────────────
        df_raw = run_extract()
        if df_raw is None:
            print("\n✅ Pipeline al día — nada nuevo que procesar.")
            log_run("SKIPPED", 0, (datetime.now() - start).total_seconds())
            return

        # ── 2. VALIDATE ───────────────────────────────────────
        df_raw = run_validate(df_raw)

        # ── 3. TRANSFORM ──────────────────────────────────────
        df = run_transform(df_raw)
        rows = len(df)

        # ── 4A. ANALYZE — Shares ──────────────────────────────
        run_analyze_shares(df)

        # ── 4B. FLOAT + ANALYZE — Float % ────────────────────
        tickers    = df["ticker"].unique().tolist()
        df_float   = fetch_float(tickers)
        run_analyze_float(df, df_float)

        # ── 5. REPORT ─────────────────────────────────────────
        run_report()

        duration = (datetime.now() - start).total_seconds()
        log_run("SUCCESS", rows, duration)

        print("\n" + "=" * 55)
        print(f"  ✅ Pipeline completado en {duration:.1f}s")
        print(f"  📊 {rows:,} tickers procesados")
        print(f"  🌐 docs/index.html listo para GitHub Pages")
        print("=" * 55)

    except Exception as e:
        duration = (datetime.now() - start).total_seconds()
        err_msg  = traceback.format_exc()
        log_run("ERROR", rows, duration, str(e))
        print(f"\n❌ Pipeline falló: {e}")
        print(err_msg)
        sys.exit(1)


if __name__ == "__main__":
    run()
