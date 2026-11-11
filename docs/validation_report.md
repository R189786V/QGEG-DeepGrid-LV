# Validation report: QGEG LV Digital Twin

**15 of 15 checks passed.**

The network models are *verified* (they correctly solve the intended physics and are built as intended). They are not *validated* against field measurements, which were not available.

## Checks

| check                                                                | network        | result   | detail                                                                                                            |
|:---------------------------------------------------------------------|:---------------|:---------|:------------------------------------------------------------------------------------------------------------------|
| Hand-calculation benchmark                                           | test circuit   | PASS     | voltage hand 0.96792 vs OpenDSS 0.96792 pu (diff 0.000000); losses hand 1.3178 vs OpenDSS 1.3177 kW (diff 0.003%) |
| Energy balance (supply = loads + losses), every 15 min               | zimbabwe_rural | PASS     | worst mismatch 0.00344% of supply; all steps converged: True                                                      |
| Energy balance (supply = loads + losses), every 15 min               | zimbabwe_urban | PASS     | worst mismatch 0.01077% of supply; all steps converged: True                                                      |
| Energy balance (supply = loads + losses), every 15 min               | european_lv    | PASS     | worst mismatch 0.01435% of supply; all steps converged: True                                                      |
| Physical scaling (half load: higher voltage, about 1/4 losses)       | zimbabwe_rural | PASS     | min voltage 0.8999 -> 0.9720 pu; loss ratio 0.241 (theory about 0.25)                                             |
| Physical scaling (half load: higher voltage, about 1/4 losses)       | zimbabwe_urban | PASS     | min voltage 0.9537 -> 0.9995 pu; loss ratio 0.234 (theory about 0.25)                                             |
| Physical scaling (half load: higher voltage, about 1/4 losses)       | european_lv    | PASS     | min voltage 1.0264 -> 1.0381 pu; loss ratio 0.254 (theory about 0.25)                                             |
| Topology and data integrity                                          | zimbabwe_rural | PASS     | connected True, radial True, customers 58 (OpenDSS 58), missing 0, unreachable 0                                  |
| Topology and data integrity                                          | zimbabwe_urban | PASS     | connected True, radial True, customers 128 (OpenDSS 128), missing 0, unreachable 0                                |
| Topology and data integrity                                          | european_lv    | PASS     | connected True, radial True, customers 55 (OpenDSS 55), missing 0, unreachable 0                                  |
| Reproducibility (rebuild gives identical files)                      | zimbabwe_rural | PASS     | hash before 97e5499107bc..., after 97e5499107bc...                                                                |
| Reproducibility (rebuild gives identical files)                      | zimbabwe_urban | PASS     | hash before 4047929adaf9..., after 4047929adaf9...                                                                |
| Forecast beats the last-week baseline in every fold                  | city_north     | PASS     | skill by fold: 61.3%, 59.2%, 57.3%                                                                                |
| Forecast beats the last-week baseline in every fold                  | branxton       | PASS     | skill by fold: 62.9%, 61.5%, 52.8%                                                                                |
| Forecast beats the last-week baseline in every fold (level-adjusted) | kogarah        | PASS     | skill by fold: 60.9%, 58.5%, 47.0%                                                                                |

## Forecast cross-validation (rolling origin, real Ausgrid data)

Each fold trains only on data before it and tests on the following two months.

| substation   | fold                 |   train_intervals | model             |   mae_mw |   mape_pct |   bias_mw |   skill_vs_naive_7d_pct |
|:-------------|:---------------------|------------------:|:------------------|---------:|-----------:|----------:|------------------------:|
| city_north   | Nov 2025 to Dec 2025 |             51624 | naive_7d          |    4.141 |       9.01 |     0.741 |                     0   |
| city_north   | Nov 2025 to Dec 2025 |             51624 | xgb_weather       |    1.602 |       3.41 |     1.015 |                    61.3 |
| city_north   | Nov 2025 to Dec 2025 |             51624 | xgb_weather_level |    1.481 |       3.25 |     0.076 |                    64.2 |
| city_north   | Jan 2026 to Feb 2026 |             57478 | naive_7d          |    5.675 |      12.47 |    -1.692 |                     0   |
| city_north   | Jan 2026 to Feb 2026 |             57478 | xgb_weather       |    2.314 |       6.17 |     0.29  |                    59.2 |
| city_north   | Jan 2026 to Feb 2026 |             57478 | xgb_weather_level |    2.865 |       7.15 |    -0.63  |                    49.5 |
| city_north   | Mar 2026 to Apr 2026 |             62757 | naive_7d          |    3.543 |       7.82 |     0.782 |                     0   |
| city_north   | Mar 2026 to Apr 2026 |             62757 | xgb_weather       |    1.511 |       3.74 |     0.091 |                    57.3 |
| city_north   | Mar 2026 to Apr 2026 |             62757 | xgb_weather_level |    1.554 |       3.85 |    -0.205 |                    56.1 |
| kogarah      | Nov 2025 to Dec 2025 |             51755 | naive_7d          |    5.815 |      18.52 |     0.353 |                     0   |
| kogarah      | Nov 2025 to Dec 2025 |             51755 | xgb_weather       |    2.11  |       6.38 |    -0.442 |                    63.7 |
| kogarah      | Nov 2025 to Dec 2025 |             51755 | xgb_weather_level |    2.274 |       6.99 |    -0.449 |                    60.9 |
| kogarah      | Jan 2026 to Feb 2026 |             57611 | naive_7d          |    6.414 |      17.11 |    -1.415 |                     0   |
| kogarah      | Jan 2026 to Feb 2026 |             57611 | xgb_weather       |    3.419 |       8.96 |    -3.143 |                    46.7 |
| kogarah      | Jan 2026 to Feb 2026 |             57611 | xgb_weather_level |    2.659 |       7.16 |    -0.854 |                    58.5 |
| kogarah      | Mar 2026 to Apr 2026 |             63275 | naive_7d          |    8.462 |      15.81 |    -2.301 |                     0   |
| kogarah      | Mar 2026 to Apr 2026 |             63275 | xgb_weather       |   15.721 |      25.61 |   -15.442 |                   -85.8 |
| kogarah      | Mar 2026 to Apr 2026 |             63275 | xgb_weather_level |    4.487 |       8    |    -1.952 |                    47   |
| branxton     | Nov 2025 to Dec 2025 |             51493 | naive_7d          |    2.3   |     nan    |     0.068 |                     0   |
| branxton     | Nov 2025 to Dec 2025 |             51493 | xgb_weather       |    0.854 |     nan    |    -0.006 |                    62.9 |
| branxton     | Nov 2025 to Dec 2025 |             51493 | xgb_weather_level |    0.986 |     nan    |    -0.05  |                    57.1 |
| branxton     | Jan 2026 to Feb 2026 |             57335 | naive_7d          |    1.977 |     nan    |    -0.291 |                     0   |
| branxton     | Jan 2026 to Feb 2026 |             57335 | xgb_weather       |    0.761 |     nan    |     0.049 |                    61.5 |
| branxton     | Jan 2026 to Feb 2026 |             57335 | xgb_weather_level |    0.975 |     nan    |     0.156 |                    50.7 |
| branxton     | Mar 2026 to Apr 2026 |             62998 | naive_7d          |    1.18  |     nan    |     0.24  |                     0   |
| branxton     | Mar 2026 to Apr 2026 |             62998 | xgb_weather       |    0.557 |     nan    |    -0.021 |                    52.8 |
| branxton     | Mar 2026 to Apr 2026 |             62998 | xgb_weather_level |    0.61  |     nan    |    -0.039 |                    48.3 |

Note: european_lv has 905 of 905 lines at the OpenDSS default 400 A rating.

## Limitations

- No field measurements of voltage or current: the network models are verified (they solve the intended physics correctly) but not validated against a real network. Field validation needs a utility partner.
- The two Zimbabwean networks are synthetic. Equipment values are approximate typical figures, not ZETDC data, and load profiles are synthetic hourly shapes.
- OpenDSS loads switch from constant power to constant impedance below 0.95 pu (its default). Customers at very low voltage therefore draw slightly less than nominal power, which softens the worst undervoltage results.
- European feeder lines without a published rating use OpenDSS's default 400 A, so its line loading % is indicative.
- DER, EV and flexibility studies use one simulated clear-sky day and hypothetical sizes and locations.
- Forecasts use actual (reanalysis) weather, i.e. a perfect weather forecast, so with-weather accuracy is optimistic.
- Reliability indices use assumed fault rates and cover LV faults only (no MV faults or load shedding).
- Asset health is stress-based (loading and voltage), not based on asset age, inspections or condition data.
- Harmonics, flicker and transient events are not modelled (they need measured spectra or time-domain models).
- Agents are deterministic rules tested on one day; they are decision support and require operator approval.