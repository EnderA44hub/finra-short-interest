"""
extract.py -- Descarga el bulk CSV de short interest desde FINRA.

IMPORTANTE: El endpoint API otcMarket/equityShortInterestStandardized
es SOLO para acciones OTC. NYSE/NASDAQ NO aparecen ahí.

Desde junio 2021, FINRA publica los datos consolidados (NYSE + NASDAQ + OTC)
como archivos CSV públicos en:
  https://cdn.finra.org/equity/otcmarket/biweekly/shrt{YYYYMMDD}.csv
"""

import io
import json
import requests
import pandas as pd
from pathlib import Path
from datetime import date, timedelta, datetime

ROOT     = Path(__file__).parent.parent
RAW_DIR  = ROOT / "data" / "raw"
REGISTRY = ROOT / "data" / "registry.json"

FINRA_CSV_URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{date}.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":     "*/*",
}


# ── registry helpers ──────────────────────────────────────────────────────────

def _load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _save_registry(reg: dict):
    REGISTRY.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def _already_downloaded(date_str: str) -> bool:
    return date_str in _load_registry()["processed_dates"]


# ── encontrar el CSV más reciente ─────────────────────────────────────────────

def find_latest_date() -> str:
    """
    FINRA publica el CSV ~día 15 y ~último día hábil de cada mes.
    Prueba fechas hacia atrás hasta encontrar un archivo disponible.
    """
    today = date.today()
    print(f"   Buscando CSV más reciente (desde {today})...")

    for days_back in range(0, 46):
        candidate = today - timedelta(days=days_back)
        date_str  = candidate.strftime("%Y%m%d")
        url       = FINRA_CSV_URL.format(date=date_str)

        try:
            # HEAD request para verificar sin descargar
            resp = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                result = candidate.strftime("%Y-%m-%d")
                print(f"   ✓ CSV encontrado: {result}")
                return result
        except Exception:
            pass

        try:
            # Fallback: GET con Range para no descargar el archivo completo
            h = {**HEADERS, "Range": "bytes=0-200"}
            resp = requests.get(url, headers=h, timeout=10)
            if resp.status_code in (200, 206):
                result = candidate.strftime("%Y-%m-%d")
                print(f"   ✓ CSV encontrado: {result}")
                return result
        except Exception:
            continue

    raise ValueError("No se encontró CSV de FINRA en los últimos 45 días")


# ── descargar y parsear el CSV ────────────────────────────────────────────────

def download_csv(settlement_date: str) -> pd.DataFrame:
    """Descarga el CSV bulk de FINRA y lo normaliza."""
    date_nodash = settlement_date.replace("-", "")
    url         = FINRA_CSV_URL.format(date=date_nodash)

    print(f"   Descargando: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=120)
    resp.raise_for_status()

    content = resp.content

    # Intentar parsear como pipe-delimited (formato FINRA estándar)
    for sep in ("|", ",", "\t"):
        try:
            df = pd.read_csv(
                io.BytesIO(content),
                sep=sep,
                encoding="utf-8",
                on_bad_lines="skip",
                dtype=str,
            )
            if len(df.columns) >= 3:
                break
        except Exception:
            continue

    print(f"   Columnas del CSV: {list(df.columns)}")
    print(f"   Total filas:      {len(df):,}")

    # ── Mapear columnas al formato interno ────────────────────────────────────
    col = {c.strip(): c for c in df.columns}   # lookup case-insensitive
    rename = {}

    # ticker / symbol
    for candidate in ["symbolCode", "Symbol", "symbol", "SYMBOL", "Ticker",
                       "SecuritySymbol", "IssueSymbol", "Issue Symbol",
                       "securitiesInformationProcessorSymbolIdentifier"]:
        if candidate in col:
            rename[col[candidate]] = "ticker"
            break

    # short interest
    for candidate in ["currentShortPositionQuantity", "CurrentShortInterest",
                       "Current Short Interest", "ShortInterest", "Short Interest",
                       "ShortPosition", "currentShortInterest", "SHORT_INTEREST",
                       "TotalShortInterest", "currentShortShareNumber"]:
        if candidate in col:
            rename[col[candidate]] = "short_interest_shares"
            break

    # market / exchange
    for candidate in ["Market", "market", "Exchange", "exchange",
                       "MarketClassCode", "ExchangeCode"]:
        if candidate in col:
            rename[col[candidate]] = "market"
            break

    # days to cover (para Squeeze Screen)
    for candidate in ["daysToCoverQuantity", "DaysToCover", "Days to Cover"]:
        if candidate in col:
            rename[col[candidate]] = "days_to_cover"
            break

    # volumen promedio diario
    for candidate in ["averageDailyVolumeQuantity", "AverageDailyVolume",
                       "Average Daily Volume"]:
        if candidate in col:
            rename[col[candidate]] = "avg_daily_volume"
            break

    if "ticker" not in rename.values():
        raise ValueError(
            f"No se encontró columna de ticker. Columnas disponibles: {list(df.columns)}"
        )
    if "short_interest_shares" not in rename.values():
        raise ValueError(
            f"No se encontró columna de short interest. Columnas: {list(df.columns)}"
        )

    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(settlement_date)

    keep = [c for c in ["ticker", "date", "short_interest_shares", "market",
                         "days_to_cover", "avg_daily_volume"]
            if c in df.columns]
    df = df[keep].copy()

    df["ticker"]                = df["ticker"].astype(str).str.upper().str.strip()
    df["short_interest_shares"] = pd.to_numeric(
        df["short_interest_shares"].astype(str).str.replace(",", ""),
        errors="coerce"
    )
    for numcol in ("days_to_cover", "avg_daily_volume"):
        if numcol in df.columns:
            df[numcol] = pd.to_numeric(
                df[numcol].astype(str).str.replace(",", ""), errors="coerce"
            )
    df = df.dropna(subset=["ticker", "short_interest_shares"])
    df = df[df["ticker"].str.match(r"^[A-Z]")]  # descartar filas de metadata

    if "market" in df.columns:
        print(f"\n   Distribución de mercados (top 10):")
        print(df["market"].value_counts().head(10).to_string())

    return df


# ── guardar y registrar ───────────────────────────────────────────────────────

def save_raw(df: pd.DataFrame, settlement_date: str) -> Path:
    out = RAW_DIR / f"{settlement_date}.parquet"
    df.to_parquet(out, index=False)
    print(f"\n   Guardado: data/raw/{settlement_date}.parquet  ({len(df):,} filas)")
    return out


def mark_downloaded(settlement_date: str):
    reg = _load_registry()
    if settlement_date not in reg["processed_dates"]:
        reg["processed_dates"].append(settlement_date)
        reg["processed_dates"].sort(reverse=True)
    reg["last_updated"] = datetime.now().isoformat()
    _save_registry(reg)


# ── entry point ───────────────────────────────────────────────────────────────

def run_extract():
    print("\n[EXTRACT] Buscando datos de FINRA...")

    latest = find_latest_date()

    if _already_downloaded(latest):
        print(f"   Ya tenemos {latest} -- nada que descargar.")
        path = RAW_DIR / f"{latest}.parquet"
        return pd.read_parquet(path) if path.exists() else None

    df = download_csv(latest)
    save_raw(df, latest)
    mark_downloaded(latest)

    return df


if __name__ == "__main__":
    df = run_extract()
    if df is not None:
        print("\nMuestra:")
        print(df.head(15).to_string())
        print(f"\nShape: {df.shape}")
