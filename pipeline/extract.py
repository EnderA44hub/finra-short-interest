"""
extract.py -- Descarga el bulk file de short interest desde la API publica de FINRA.
Solo extrae: ticker · date · short_interest_shares · exchange

Dataset actual (post abril 2021):
  POST https://api.finra.org/data/group/otcMarket/name/equityShortInterestStandardized

Campos relevantes:
  securitiesInformationProcessorSymbolIdentifier -> ticker
  settlementDate                                 -> date
  currentShortPositionQuantity                   -> short_interest_shares
  marketClassCode                                -> market       (siempre "OTC" — no sirve)
  issuerServicesGroupExchangeCode                -> exchange     ← el que realmente distingue NYSE/NASDAQ
"""

import json
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, date, timedelta

ROOT     = Path(__file__).parent.parent
RAW_DIR  = ROOT / "data" / "raw"
REGISTRY = ROOT / "data" / "registry.json"

FINRA_URL = "https://api.finra.org/data/group/otcMarket/name/equityShortInterestStandardized"

HEADERS = {
    "Content-Type": "application/json",
    "Accept":        "application/json",
}

# Mapeo de campos FINRA -> nombres internos
FIELD_CANDIDATES = {
    # ticker
    "securitiesInformationProcessorSymbolIdentifier": "ticker",
    "symbolCode":            "ticker",
    "issueSymbolIdentifier": "ticker",
    # fecha
    "settlementDate":        "date",
    # short interest
    "currentShortPositionQuantity": "short_interest_shares",
    "totalShortInterest":           "short_interest_shares",
    "currentShortInterest":         "short_interest_shares",
    "currentShortShareNumber":      "short_interest_shares",
    # mercado (marketClassCode siempre vale "OTC" — no usar para filtrar)
    "marketClassCode":       "market",
    "marketCategoryCode":    "market",
    # exchange real — distingue NYSE / NASDAQ / OTC
    "issuerServicesGroupExchangeCode": "exchange",
}


# ── registry helpers ──────────────────────────────────────────────────────────

def _load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _save_registry(reg: dict):
    REGISTRY.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def _already_downloaded(date_str: str) -> bool:
    return date_str in _load_registry()["processed_dates"]


# ── descubrir campos disponibles ──────────────────────────────────────────────

def discover_fields() -> tuple[dict, list]:
    """Hace una peticion de 1 fila para descubrir que campos devuelve FINRA."""
    payload = {"limit": 1}
    resp = requests.post(FINRA_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    sample = resp.json()

    if not sample:
        raise ValueError("FINRA devolvio lista vacia en discovery")

    available = set(sample[0].keys())
    print(f"   Campos disponibles en FINRA: {sorted(available)}")

    mapping = {}
    seen_internal = set()
    for finra_field, internal_name in FIELD_CANDIDATES.items():
        if finra_field in available and internal_name not in seen_internal:
            mapping[finra_field] = internal_name
            seen_internal.add(internal_name)

    print(f"   Mapeo activo: {mapping}")

    required = {"ticker", "date", "short_interest_shares"}
    missing = required - set(mapping.values())
    if missing:
        raise ValueError(
            f"No se encontraron campos requeridos: {missing}. "
            f"Campos FINRA disponibles: {sorted(available)}"
        )
    return mapping, list(available)


# ── obtener fecha más reciente ────────────────────────────────────────────────

def fetch_latest_date(all_fields: list) -> str:
    """
    FINRA no soporta sortFields ni GREATER_THAN.
    Prueba cada día hacia atrás con EQUAL hasta encontrar una fecha con datos.
    """
    for days_back in range(0, 46):
        candidate = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        payload = {
            "limit": 1,
            "fields": ["settlementDate"] if "settlementDate" in all_fields else [],
            "compareFilters": [{
                "fieldName":   "settlementDate",
                "fieldValue":  candidate,
                "compareType": "EQUAL",
            }],
        }
        try:
            resp = requests.post(FINRA_URL, headers=HEADERS, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data and data[0].get("settlementDate") == candidate:
                    print(f"   ✓ Fecha más reciente encontrada: {candidate}")
                    return candidate
        except Exception:
            continue

    raise ValueError("No se encontró ninguna fecha reciente en FINRA (últimos 45 días)")

    resp = requests.post(FINRA_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        raise ValueError("FINRA no devolvio datos recientes (ultimos 2 años)")

    dates = sorted(
        {r["settlementDate"] for r in data if r.get("settlementDate")},
        reverse=True,
    )

    if not dates:
        raise ValueError("No se encontraron settlementDates en el sample")

    print(f"   Fechas recientes encontradas: {dates[:5]}")
    return dates[0]   # la más reciente


# ── bulk download ─────────────────────────────────────────────────────────────

def download_bulk(settlement_date: str, field_map: dict) -> pd.DataFrame:
    """Descarga todos los tickers para una fecha de settlement."""
    all_rows = []
    offset   = 0
    limit    = 5000

    finra_fields = list(field_map.keys())

    # Asegurar que exchange se descarga si está disponible
    if "issuerServicesGroupExchangeCode" not in finra_fields:
        finra_fields.append("issuerServicesGroupExchangeCode")

    print(f"   Descargando FINRA -- fecha: {settlement_date}")

    while True:
        payload = {
            "limit":  limit,
            "offset": offset,
            "fields": finra_fields,
            "compareFilters": [
                {
                    "fieldName":   "settlementDate",
                    "fieldValue":  settlement_date,
                    "compareType": "EQUAL",
                }
            ],
        }

        resp = requests.post(FINRA_URL, headers=HEADERS, json=payload, timeout=60)

        if resp.status_code == 400:
            print(f"   [!] 400 en descarga offset={offset}. Reintentando sin fields...")
            payload.pop("fields", None)
            resp = requests.post(FINRA_URL, headers=HEADERS, json=payload, timeout=60)

        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break

        all_rows.extend(batch)
        print(f"     ... {len(all_rows):,} filas")

        if len(batch) < limit:
            break
        offset += limit

    print(f"   Total: {len(all_rows):,} filas")

    df = pd.DataFrame(all_rows)

    # Aplicar mapeo dinámico
    rename = {k: v for k, v in field_map.items() if k in df.columns}

    # market: tomar el que esté disponible
    for mkt_field in ("marketClassCode", "marketCategoryCode"):
        if mkt_field in df.columns and "market" not in rename.values():
            rename[mkt_field] = "market"
            break

    # exchange: campo clave para filtrar NYSE/NASDAQ
    if "issuerServicesGroupExchangeCode" in df.columns and "exchange" not in rename.values():
        rename["issuerServicesGroupExchangeCode"] = "exchange"

    df = df.rename(columns=rename)

    keep = [c for c in ["ticker", "date", "short_interest_shares", "market", "exchange"]
            if c in df.columns]
    df = df[keep].copy()

    df["date"]                  = pd.to_datetime(df["date"])
    df["short_interest_shares"] = pd.to_numeric(df["short_interest_shares"], errors="coerce")
    df = df.dropna(subset=["ticker", "date", "short_interest_shares"])

    # Mostrar distribución de exchanges para diagnóstico
    if "exchange" in df.columns:
        print(f"\n   Distribución de exchanges (top 15):")
        print(df["exchange"].value_counts().head(15).to_string())
    elif "market" in df.columns:
        print(f"\n   Distribución de market:")
        print(df["market"].value_counts().head(10).to_string())

    return df


# ── guardar y registrar ───────────────────────────────────────────────────────

def save_raw(df: pd.DataFrame, settlement_date: str) -> Path:
    out = RAW_DIR / f"{settlement_date}.parquet"
    df.to_parquet(out, index=False)
    print(f"   Guardado: data/raw/{settlement_date}.parquet  ({len(df):,} filas)")
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
    print("\n[EXTRACT] Verificando FINRA...")

    field_map, all_fields = discover_fields()
    latest = fetch_latest_date(all_fields)
    print(f"   Fecha más reciente en FINRA: {latest}")

    if not latest:
        raise ValueError("No se pudo determinar la fecha más reciente de FINRA")

    if _already_downloaded(latest):
        print(f"   Ya tenemos {latest} -- nada que descargar.")
        path = RAW_DIR / f"{latest}.parquet"
        return pd.read_parquet(path) if path.exists() else None

    df = download_bulk(latest, field_map)
    save_raw(df, latest)
    mark_downloaded(latest)

    return df


if __name__ == "__main__":
    df = run_extract()
    if df is not None:
        print("\nPrimeras filas:")
        print(df.head(10).to_string())
        print(f"\nShape: {df.shape}")
        print(f"Columnas: {list(df.columns)}")
