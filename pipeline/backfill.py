"""
backfill.py — Descarga datos históricos de FINRA para poblar history_shares.parquet.

Cubre desde junio 2021 (cuando FINRA empezó a incluir NYSE/NASDAQ)
hasta la fecha más reciente disponible.

Uso:
    python pipeline/backfill.py

Solo actualiza history_shares.parquet (Pipeline A).
El Pipeline B (float %) no se backfillea porque el float de Yahoo
solo da el valor actual, no el histórico por fecha.
"""

import io
import json
import time
import calendar
import requests
import pandas as pd
from pathlib import Path
from datetime import date, timedelta, datetime

ROOT         = Path(__file__).parent.parent
RAW_DIR      = ROOT / "data" / "raw"
HISTORY_PATH = ROOT / "data" / "history" / "history_shares.parquet"
REGISTRY     = ROOT / "data" / "registry.json"

FINRA_CSV_URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{date}.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":     "*/*",
}

# Sufijos OTC del 5to carácter (mismo filtro que transform.py)
OTC_FIFTH_LETTER = {"F", "Y", "E", "Q", "K"}


# ── Heurística de filtrado NYSE/NASDAQ ────────────────────────────────────────

def is_likely_listed(ticker: str) -> bool:
    if not isinstance(ticker, str):
        return False
    t = ticker.upper().strip().replace(".", "").replace("-", "")
    if not t.isalpha():
        return False
    n = len(t)
    if n <= 4:
        return True
    if n == 5:
        return t[-1] not in OTC_FIFTH_LETTER
    return False


# ── Fechas candidatas ─────────────────────────────────────────────────────────

def generate_candidate_dates(start: date, end: date) -> list[date]:
    """
    FINRA publica alrededor del 15 y del último día hábil de cada mes.
    Genera candidatos ±3 días de esas ventanas para cada mes en el rango.
    """
    candidates = []
    year, month = start.year, start.month

    while date(year, month, 1) <= end:
        last_day = calendar.monthrange(year, month)[1]

        # Ventana mid-month: días 13–17
        for d in range(13, 18):
            try:
                candidates.append(date(year, month, d))
            except ValueError:
                pass

        # Ventana end-of-month: últimos 5 días
        for d in range(max(24, last_day - 4), last_day + 1):
            try:
                candidates.append(date(year, month, d))
            except ValueError:
                pass

        month += 1
        if month > 12:
            month = 1
            year += 1

    return sorted(c for c in candidates if start <= c <= end)


# ── Detección de fechas disponibles ──────────────────────────────────────────

def find_available_dates(candidates: list[date], already_have: set[str]) -> list[date]:
    """
    Hace HEAD requests a FINRA para encontrar qué fechas tienen CSV disponible.
    Saltea las fechas que ya están en el historial.
    """
    available = []
    total     = len(candidates)

    print(f"\n[BACKFILL] Buscando fechas disponibles en FINRA ({total} candidatos)...")

    for i, d in enumerate(candidates, 1):
        date_str = d.strftime("%Y-%m-%d")

        if date_str in already_have:
            continue   # ya la tenemos

        date_nodash = d.strftime("%Y%m%d")
        url         = FINRA_CSV_URL.format(date=date_nodash)

        try:
            resp = requests.head(url, headers=HEADERS, timeout=8, allow_redirects=True)
            if resp.status_code == 200:
                available.append(d)
                print(f"   ✓ {date_str}  ({len(available)} encontradas, {i}/{total})")
            time.sleep(0.1)   # respetar el servidor de FINRA
        except Exception:
            # Fallback: GET con Range si HEAD no responde
            try:
                h = {**HEADERS, "Range": "bytes=0-100"}
                resp = requests.get(url, headers=h, timeout=8)
                if resp.status_code in (200, 206):
                    available.append(d)
                    print(f"   ✓ {date_str}  ({len(available)} encontradas)")
            except Exception:
                pass

    print(f"\n   Total encontradas: {len(available)}")
    return available


# ── Descarga y parseo de un CSV ───────────────────────────────────────────────

def download_and_parse(d: date) -> pd.DataFrame | None:
    """Descarga el CSV de FINRA para una fecha y retorna df filtrado."""
    date_nodash = d.strftime("%Y%m%d")
    url         = FINRA_CSV_URL.format(date=date_nodash)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"   ✗ Error descargando {d}: {e}")
        return None

    content = resp.content

    # Parsear CSV (probar pipe y coma)
    df = None
    for sep in ("|", ",", "\t"):
        try:
            tmp = pd.read_csv(
                io.BytesIO(content), sep=sep,
                encoding="utf-8", on_bad_lines="skip", dtype=str,
            )
            if len(tmp.columns) >= 3:
                df = tmp
                break
        except Exception:
            continue

    if df is None:
        print(f"   ✗ No se pudo parsear {d}")
        return None

    # Mapear columnas
    rename = {}
    for c in df.columns:
        cl = c.strip()
        if cl in ("symbolCode", "Symbol", "symbol", "Ticker",
                   "securitiesInformationProcessorSymbolIdentifier"):
            rename[c] = "ticker"
        elif cl in ("currentShortPositionQuantity", "CurrentShortInterest",
                    "ShortInterest", "currentShortInterest", "TotalShortInterest",
                    "currentShortShareNumber"):
            rename[c] = "short_interest_shares"

    if "ticker" not in rename.values() or "short_interest_shares" not in rename.values():
        print(f"   ✗ Columnas no reconocidas en {d}: {list(df.columns)}")
        return None

    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(d.strftime("%Y-%m-%d"))

    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["short_interest_shares"] = pd.to_numeric(
        df["short_interest_shares"].astype(str).str.replace(",", ""),
        errors="coerce",
    )

    df = df[["ticker", "date", "short_interest_shares"]].dropna()
    df = df[df["ticker"].apply(is_likely_listed)]
    df["short_interest_shares"] = df["short_interest_shares"].astype("int64")
    df = df.drop_duplicates()

    return df


# ── Actualizar history_shares.parquet ────────────────────────────────────────

def append_to_history(new_df: pd.DataFrame):
    df = new_df.rename(columns={"short_interest_shares": "value"})
    if HISTORY_PATH.exists():
        existing = pd.read_parquet(HISTORY_PATH)
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df
    combined = combined.drop_duplicates(subset=["ticker", "date"])
    combined = combined.sort_values(["ticker", "date"])
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(HISTORY_PATH, index=False)
    combined = combined.sort_values(["ticker", "date"])
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(HISTORY_PATH, index=False)


def load_registry_dates() -> set[str]:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return set(reg.get("processed_dates", []))


def mark_date(date_str: str):
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if date_str not in reg["processed_dates"]:
        reg["processed_dates"].append(date_str)
        reg["processed_dates"].sort(reverse=True)
    reg["last_updated"] = datetime.now().isoformat()
    REGISTRY.write_text(json.dumps(reg, indent=2), encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────────────────────

def run_backfill(start_year: int = 2021, start_month: int = 6):
    print("=" * 60)
    print(" BACKFILL — FINRA Short Interest Histórico")
    print(f" Desde: {start_year}-{start_month:02d}  |  Hasta: hoy")
    print("=" * 60)

    start        = date(start_year, start_month, 1)
    end          = date.today() - timedelta(days=1)
    already_have = load_registry_dates()

    candidates = generate_candidate_dates(start, end)
    available  = find_available_dates(candidates, already_have)

    if not available:
        print("\nNo hay fechas nuevas para descargar.")
        return

    print(f"\n[BACKFILL] Descargando {len(available)} archivos históricos...\n")

    ok, fail = 0, 0
    for i, d in enumerate(available, 1):
        date_str = d.strftime("%Y-%m-%d")
        print(f"   [{i:3d}/{len(available)}] {date_str}", end="  ")

        df = download_and_parse(d)

        if df is None or len(df) == 0:
            print("✗ sin datos")
            fail += 1
            continue

        append_to_history(df)
        mark_date(date_str)
        print(f"✓  {len(df):,} tickers")
        ok += 1

        time.sleep(0.3)   # pausa entre descargas

    print("\n" + "=" * 60)
    print(f" Completado: {ok} OK · {fail} fallidos")
    rows = pd.read_parquet(HISTORY_PATH).shape[0] if HISTORY_PATH.exists() else 0
    print(f" history_shares.parquet: {rows:,} filas totales")
    print("=" * 60)
    print("\n Siguiente paso: corré el pipeline normal para regenerar el index.html")
    print("   python pipeline/run_pipeline.py")


if __name__ == "__main__":
    run_backfill(start_year=2021, start_month=6)
