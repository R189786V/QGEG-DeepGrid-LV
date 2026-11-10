# Decision support report: zimbabwe_urban

**Situation (simulated):** 50% of homes with 5 kW rooftop PV and 30% with a 7 kW EV.
All figures come from re-running the OpenDSS network model. Networks are synthetic.

## Problem (do nothing)
- Violation-hours: 12.0 (undervoltage 2.0 h, overvoltage 6.0 h, transformer overload 2.0 h, line overload 2.0 h)
- Customers outside 0.94-1.1 pu: 55 (h006, h007, h009, h011, h014, h015, h017, h018, h019, h020, h023, h024 ...)
- Voltage range 0.8631 to 1.1407 pu; transformer peak 174.5%; line peak 152.5%

## BEST AVAILABLE (no combination removes every violation)
**EV managed + smart battery**
**Status:** verified in simulation only; not applied. Requires operator approval before any field action.
- Severity score (1 = 1% beyond a limit for 1 hour): 273.64 -> 0.14
- Violation-hours: 12.0 -> 0.25
- Customers out of limits: 55 -> 5
- Voltage range: 0.9712 to 1.1055 pu
- Transformer peak: 174.5% -> 67.7%
- Solar curtailed: 0.0%; losses 80.2 kWh/day

## Tested: 12 combinations, 0 feasible

| combination                                 | valid   | feasible   |   severity |   violation_hours |   customers_out_of_limits |   v_min_pu |   v_max_pu |   max_tx_loading_pct |   max_line_pct |   curtail_pct |   losses_kwh |
|:--------------------------------------------|:--------|:-----------|-----------:|------------------:|--------------------------:|-----------:|-----------:|---------------------:|---------------:|--------------:|-------------:|
| EV managed + smart battery                  | True    | False      |       0.14 |              0.25 |                         5 |     0.9712 |     1.1055 |                 67.7 |           55.1 |           0   |         80.2 |
| EV slow + smart battery                     | True    | False      |       0.14 |              0.25 |                         5 |     0.9712 |     1.1055 |                 74.9 |           57.1 |           0   |         79.9 |
| EV managed + smart battery + volt-watt      | True    | False      |       0.14 |              0.25 |                         5 |     0.9712 |     1.1055 |                 67.7 |           55.1 |           0   |         80.1 |
| EV slow + smart battery + volt-watt         | True    | False      |       0.14 |              0.25 |                         5 |     0.9712 |     1.1055 |                 74.9 |           57.1 |           0   |         79.9 |
| EV managed + volt-watt                      | True    | False      |       4.94 |              6    |                        17 |     0.9593 |     1.1087 |                 84   |           62.6 |           2.2 |        122.7 |
| EV slow + volt-watt                         | True    | False      |       4.95 |              6    |                        18 |     0.9593 |     1.1087 |                 86.7 |           63   |           2.2 |        123.4 |
| EV managed                                  | True    | False      |      16.92 |              6    |                        28 |     0.9593 |     1.1407 |                 84   |           70.1 |           0   |        129.8 |
| EV slow                                     | True    | False      |      16.92 |              6    |                        28 |     0.9593 |     1.1407 |                 86.7 |           70.1 |           0   |        130.5 |
| EV uncontrolled + smart battery             | True    | False      |     129.23 |              6.25 |                        27 |     0.8992 |     1.1055 |                134.3 |          133.2 |           0   |        110   |
| EV uncontrolled + smart battery + volt-watt | True    | False      |     129.23 |              6.25 |                        27 |     0.8992 |     1.1055 |                134.3 |          133.2 |           0   |        109.9 |

## Alternatives for the operator
- None needed: the recommended combination requires no field work.

## Caveats
- Rule-based controls on a synthetic network with hypothetical DER and EVs; one clear-sky day.
- Every EV option delivers the same energy per car (14.0 kWh).
- Moving irrigation to daytime increases evaporation losses and needs the farmer's agreement.
- Phase rebalancing requires a field crew to reconnect service cables.
- Volt-watt settles voltage just above 1.10 pu by design (control band 253-260 V).