"""
extract.py -- Descarga el bulk file de short interest desde la API publica de FINRA.
Solo extrae: ticker - date - short_interest_shares

Dataset actual (post abril 2021):
  POST https://api.finra.org/data/group/otcMarket/name/equityShortInterestStandardized

Campos relevantes:
  symbolCode            -> ticker
  settlementDate        -> date
  totalShortInterest    -> short_interest_shares
  marketClassCode       -> market (para filtrar NYSE/NASDAQ)
"""

import json
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

ROOT     = Path(__file__).parent.parent
RAW_DIR  = ROOT / "data" / "raw"
REGISTRY = ROOT / "data" / "registry.json"

# Endpoint actual -- sin autenticacion para datos publicos
FINRA_URL = "https://api.finra.org/data/group/otcMarket/name/equityShortInterestStandardized"

HEADERS = {
    "Content-Type": "application/json",
    "Accept":        "application/json",
}

# Mapeo de campos FINRA -> nombres internos
# Nombres reales confirmados del dataset equityShortInterestStandardized (2021+)
FIELD_CANDIDATES = {
    # ticker
    "securitiesInformationProcessorSymbolIdentifier": "ticker",
    "symbolCode":            "ticker",           # fallback
    "issueSymbolIdentifier": "ticker",           # fallback doc vieja
    # fecha
    "settlementDate":        "date",
    # short interest
    "currentShortPositionQuantity": "short_interest_shares",
    "totalShortInterest":           "short_interest_shares",  # fallback
    "currentShortInterest":         "short_interest_shares",  # fallback
    "currentShortShareNumber":      "short_interest_shares",  # fallback v1
    # mercado
    "marketClassCode":       "market",
    "marketCategoryCode":    "market",           # fallback
}


# -- registry helpers ----------------------------------------------------------

def _load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _save_registry(reg: dict):
    REGISTRY.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def _already_downloaded(date_str: str) -> bool:
    return date_str in _load_registry()["processed_dates"]


# -- descubrir campos disponibles ----------------------------------------------

def discover_fields() -> dict:
    """
    Hace una peticion de 1 fila para descubrir que campos devuelve FINRA.
    Retorna el mapeo {campo_finra: nombre_interno} solo con campos presentes.
    """
    payload = {"limit": 1}
    resp = requests.post(FINRA_URL, headers=HEADERS, json=payload, timeout=30)

    if resp.status_code == 400:
        print(f"   [!] 400 en discovery. Body: {resp.text[:300]}")
        resp.raise_for_status()

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
        raise ValueError(f"No se encontraron campos requeridos: {missing}. "
                         f"Campos FINRA disponibles: {sorted(available)}")
    return mapping, list(available)


# -- obtener fecha mas reciente ------------------------------------------------

def fetch_latest_date(all_fields: list) -> str:
    """
    Obtiene la fecha de settlement mas reciente disponible.
    Usa una peticion simple de 1 registro sin sortFields (restringido por FINRA).
    """
    payload = {
        "limit": 1,
        "fields": ["settlementDate"] if "settlementDate" in all_fields else [],
    }
    resp = requests.post(FINRA_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("FINRA no devolvio datos al consultar fecha")
    return data[0].get("settlementDate", "")


# -- bulk download -------------------------------------------------------------

def download_bulk(settlement_date: str, field_map: dict) -> pd.DataFrame:
    """
    Descarga todos los tickers para una fecha de settlement usando paginacion GET.
    """
    all_rows = []
    offset   = 0
    limit    = 5000

    finra_fields = list(field_map.keys())
    # Agregar market si no esta en el mapeo principal
    if "marketClassCode" not in finra_fields and "marketCategoryCode" not in finra_fields:
        finra_fields.append("marketClassCode")

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
            print(f"   [!] 400 en descarga offset={offset}. Body: {resp.text[:300]}")
            # Si falla con fields especificos, intentar sin fields
            if "fields" in payload:
                print("   Reintentando sin especificar fields...")
                payload.pop("fields")
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

    # Aplicar mapeo dinamico
    rename = {k: v for k, v in field_map.items() if k in df.columns}
    # market: tomar el que este disponible
    for mkt_field in ("marketClassCode", "marketCategoryCode"):
        if mkt_field in df.columns and "market" not in rename.values():
            rename[mkt_field] = "market"
            break

    df = df.rename(columns=rename)

    keep = [c for c in ["ticker", "date", "short_interest_shares", "market"] if c in df.columns]
    df   = df[keep].copy()

    df["date"]                  = pd.to_datetime(df["date"])
    df["short_interest_shares"] = pd.to_numeric(df["short_interest_shares"], errors="coerce")
    df = df.dropna(subset=["ticker", "date", "short_interest_shares"])

    return df


# -- guardar y registrar -------------------------------------------------------

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


# -- entry point ---------------------------------------------------------------

def run_extract():
    print("\n[EXTRACT] Verificando FINRA...")

    # 1. Descubrir campos disponibles en este dataset
    field_map, all_fields = discover_fields()

    # 2. Obtener la fecha mas reciente
    latest = fetch_latest_date(all_fields)
    print(f"   Fecha mas reciente en FINRA: {latest}")

    if not latest:
        raise ValueError("No se pudo determinar la fecha mas reciente de FINRA")

    if _already_downloaded(latest):
        print(f"   Ya tenemos {latest} -- nada que descargar.")
        path = RAW_DIR / f"{latest}.parquet"
        return pd.read_parquet(path) if path.exists() else None

    # 3. Descargar bulk
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
        if "market" in df.columns:
            print(f"Mercados: {df['market'].value_counts().to_dict()}")
