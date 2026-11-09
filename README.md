# QGEG DeepGrid LV

A digital twin framework for low-voltage distribution networks, built on OpenDSS and Python.

## Status

| Component | Status |
|---|---|
| Repository and environment | Working |
| OpenDSS power flow test | Working |
| LV network model | Not started |
| DER models (PV, battery, EV) | Not started |
| Forecasting | Not started |
| Dashboard | Not started |
| Validation | Not started |

## Setup

    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    python src\test_opendss.py
