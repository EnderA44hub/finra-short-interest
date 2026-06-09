# FINRA Short Interest Monitor

Detecta automáticamente qué tickers NYSE/NASDAQ tienen su **short interest en máximos históricos** o cerca de ellos, publicando una página interactiva en GitHub Pages.

## Qué hace

- Descarga el bulk file de FINRA 2x al mes (settlement dates)
- Acumula serie histórica de short interest en shares y en float %
- Detecta ATH 🔴 y Near High 🟠 por ticker
- Genera `docs/index.html` con gráficos estilo Koyfin
- Se auto-actualiza vía GitHub Actions

## Setup

```bash
git clone https://github.com/TU-USER/finra-short-interest
cd finra-short-interest

# Crear estructura de carpetas
python setup.py

# Instalar dependencias
pip install -r requirements.txt

# Correr el pipeline manualmente
python pipeline/run_pipeline.py
```

## Activar GitHub Pages

1. Ir a **Settings → Pages**
2. Source: `Deploy from a branch`
3. Branch: `main` / folder: `/docs`
4. La URL será: `https://TU-USER.github.io/finra-short-interest/`

## Estructura

```
finra-short-interest/
├── setup.py                    ← crear carpetas (ejecutar 1 vez)
├── requirements.txt
├── pipeline/
│   ├── extract.py              ← descarga FINRA
│   ├── validate.py             ← quality checks
│   ├── transform.py            ← filtro NYSE/NASDAQ
│   ├── analyze.py              ← ATH / Near High
│   ├── float_fetcher.py        ← Yahoo Finance float
│   ├── report.py               ← genera index.html
│   └── run_pipeline.py         ← orquestador
├── data/
│   ├── history/                ← serie histórica acumulada ✅ en git
│   ├── latest/                 ← snapshot actual ✅ en git
│   ├── registry.json           ← control de fechas ✅ en git
│   ├── raw/                    ← bulk FINRA ❌ en .gitignore
│   └── processed/              ❌ en .gitignore
├── docs/
│   └── index.html              ← GitHub Pages ✅ en git
└── .github/workflows/
    └── pipeline.yml            ← cron 2x al mes
```

## Flags

| Flag | Criterio |
|------|----------|
| 🔴 ATH | Short interest == máximo de toda la historia |
| 🟠 Near High | Short interest >= percentil 95% histórico |
