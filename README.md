# QGEG DeepGrid LV

A digital twin framework for low-voltage distribution networks, built on OpenDSS and Python.

## Status

| Component | Status |
|---|---|
| Repository and environment | Working |
| Feeder models (3 networks, see below) | Working |
| Snapshot power flow | Working |
| Digital twin core: asset database and network topology | Working |
| Time-series (daily) simulation | Working |
| LV monitoring and alerts (simulated measurements) | Working |
| Asset health bands, transformer ageing, reliability (assumed fault rates) | Working |
| DER scenarios and PV hosting capacity (simulated) | Working |
| Forecasting | Not started |
| Dashboard | Not started |
| Validation | Not started |

## Networks

| Network | Source | Transformer | Customers | Feeders |
|---|---|---|---|---|
| european_lv | IEEE European LV Test Feeder (published benchmark) | 800 kVA, 11/0.416 kV | 55 | 1 |
| zimbabwe_urban | Synthetic, representative high-density suburb | 315 kVA, 11/0.42 kV | 128 | 4 |
| zimbabwe_rural | Synthetic, representative rural village, farm and business centre | 100 kVA, 11/0.42 kV | 58 | 3 |

The two Zimbabwean networks are synthetic. They are not real ZETDC feeders. Equipment values are approximate typical figures and all load profiles are synthetic. Simulations use 230/400 V, 50 Hz nominal. See the README in each opendss/ folder.

## Setup

    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt

## Usage

    python src\build_zim_rural.py             # rebuild the synthetic feeders (optional)
    python src\build_zim_urban.py
    python src\load_feeder.py zimbabwe_rural  # snapshot power flow
    python src\twin_core.py zimbabwe_rural    # asset database and topology

Outputs are written to data/assets/<network>/ and models/<network>/network.graphml.



