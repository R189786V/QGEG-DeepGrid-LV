# Decision support report: zimbabwe_rural

**Situation (simulated):** 50% of homes with 5 kW rooftop PV and 30% with a 7 kW EV.
All figures come from re-running the OpenDSS network model. Networks are synthetic.

## Problem (do nothing)
- Violation-hours: 14.75 (undervoltage 2.5 h, overvoltage 8.25 h, transformer overload 2.0 h, line overload 2.0 h)
- Customers outside 0.94-1.1 pu: 36 (borehole_pump, h004, h007, h008, h009, h011, h012, h013, h014, h015, h017, h018 ...)
- Voltage range 0.7598 to 1.2234 pu; transformer peak 167.5%; line peak 122.1%

## BEST AVAILABLE (no combination removes every violation)
**EV slow + smart battery + volt-watt + rebalance phases + pump to daytime**
**Status:** verified in simulation only; not applied. Requires operator approval before any field action.
- Severity score (1 = 1% beyond a limit for 1 hour): 273.84 -> 1.38
- Violation-hours: 14.75 -> 3.0
- Customers out of limits: 36 -> 12
- Voltage range: 0.9412 to 1.1132 pu
- Transformer peak: 167.5% -> 65.5%
- Solar curtailed: 0.7%; losses 48.6 kWh/day

## Tested: 48 combinations, 0 feasible

| combination                                                                 | valid   | feasible   |   severity |   violation_hours |   customers_out_of_limits |   v_min_pu |   v_max_pu |   max_tx_loading_pct |   max_line_pct |   curtail_pct |   losses_kwh |
|:----------------------------------------------------------------------------|:--------|:-----------|-----------:|------------------:|--------------------------:|-----------:|-----------:|---------------------:|---------------:|--------------:|-------------:|
| EV slow + smart battery + volt-watt + rebalance phases + pump to daytime    | True    | False      |       1.38 |              3    |                        12 |     0.9412 |     1.1132 |                 65.5 |           36.7 |           0.7 |         48.6 |
| EV managed + smart battery + volt-watt + rebalance phases + pump to daytime | True    | False      |       1.38 |              3    |                        12 |     0.9412 |     1.1132 |                 68.8 |           39.4 |           0.7 |         52.1 |
| EV slow + smart battery + rebalance phases + pump to daytime                | True    | False      |       3.96 |              4.75 |                        14 |     0.9418 |     1.1292 |                 65.5 |           39.7 |           0   |         49.8 |
| EV managed + smart battery + rebalance phases + pump to daytime             | True    | False      |       3.96 |              4.75 |                        14 |     0.9418 |     1.1292 |                 68.8 |           39.7 |           0   |         53.3 |
| EV slow + smart battery + volt-watt + pump to daytime                       | True    | False      |       4.48 |              7.25 |                        15 |     0.9275 |     1.1161 |                 66.1 |           49.9 |           0.7 |         50.4 |
| EV managed + smart battery + volt-watt + pump to daytime                    | True    | False      |       4.59 |              8    |                        15 |     0.9249 |     1.1161 |                 69.1 |           52.1 |           0.7 |         54.1 |
| EV slow + smart battery + volt-watt + rebalance phases                      | True    | False      |       6.6  |             13.5  |                        14 |     0.9325 |     1.1146 |                 72.5 |           35.6 |           2.3 |         57.4 |
| EV slow + smart battery + volt-watt                                         | True    | False      |       8.66 |             14.5  |                        17 |     0.9275 |     1.1162 |                 72.8 |           49.9 |           1.9 |         59.8 |
| EV managed + volt-watt + rebalance phases + pump to daytime                 | True    | False      |      12.75 |             13.25 |                        15 |     0.9311 |     1.1148 |                 67.3 |           42.6 |           7.6 |         68.1 |
| EV slow + volt-watt + rebalance phases + pump to daytime                    | True    | False      |      12.87 |             13.5  |                        15 |     0.9311 |     1.1148 |                 78.1 |           42.6 |           7.6 |         65.4 |

## Alternatives for the operator
- Best without moving the pump: **EV slow + smart battery + volt-watt + rebalance phases** (severity 6.6, violation-hours 13.5, customers out of limits 14, transformer 72.5%)
- Best without field work (no pump move, no rebalancing): **EV slow + smart battery + volt-watt** (severity 8.66, violation-hours 14.5, customers out of limits 17, transformer 72.8%)


## Caveats
- Rule-based controls on a synthetic network with hypothetical DER and EVs; one clear-sky day.
- Every EV option delivers the same energy per car (14.0 kWh). pump equivalent full-load hours: night 7.0, daytime option 7.0.
- Moving irrigation to daytime increases evaporation losses and needs the farmer's agreement.
- Phase rebalancing requires a field crew to reconnect service cables.
- Volt-watt settles voltage just above 1.10 pu by design (control band 253-260 V).