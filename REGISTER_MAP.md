# Register map (holding registers)

Addresses below are exposed as Modbus holding-register addresses (client address field equals the listed number, proxy slave/unit id 1). Temperatures are signed 16-bit two's-complement tenths of °C. Unsupported/unavailable data reads `0xFFFF`: check this raw sentinel before signed conversion/scaling, as it would otherwise look like -0.1 °C. That exact temperature cannot be distinguished from missing data. Failed polls retain previously known values; there is no freshness register. Non-temperature decimals/large energy counters are currently converted to integer values (see limitations).

| Address | Field | Source page(s) | Encoding / notes |
|---:|---|---|---|
| 500 | Außentemperatur | `?s=1,0` | °C × 10, signed |
| 501 | Vorlauf Ist WP | `?s=1,0` | °C × 10, signed |
| 502 | Rücklauf Ist | `?s=1,0` | °C × 10, signed |
| 503 | Warmwasser Ist | `?s=1,0` | °C × 10, signed |
| 504 | Heizkreis 1 Isttemperatur | `?s=1,0` | °C × 10, signed |
| 505 | Heizkreis 2 Isttemperatur | `?s=1,0` | °C × 10, signed |
| 506 | Vorlauf Ist NHZ | `?s=1,0` | °C × 10, signed |
| 507 | Verflüssigertemperatur | `?s=1,0` | °C × 10, signed |
| 508 | Heizkreis 1 Solltemperatur | `?s=1,0` or `?s=4,0` | °C × 10, signed; settings overwrite when returned by 4_0 |
| 509 | Heizkreis 2 Solltemperatur | `?s=1,0` or `?s=4,0` | °C × 10, signed |
| 800 | Warmwasser Komfort Soll (R/W) | `?s=4,1`, key `val22` | °C × 10 signed; valid write 100–600 (10–60 °C); posts only val22 |
| 801 | Warmwasser Eco Soll (R/W) | `?s=4,1`, key `val23` | °C × 10 signed; valid write 100–600 (10–60 °C); posts only val23 |
| 810 | Obere Warmwasser Solltemperatur | `?s=4,2`, key `60312` | °C × 10, signed; distinct from comfort target |
| 1500 | Betriebsart (R/W) | `/` | 0 Bereitschaft, 1 Programm, 2 Komfort, 3 Eco, 4 Warmwasser, 5 Notbetrieb |
| 1520 | VD Heizen Laufzeit | `?s=1,1` | integer hours |
| 1521 | VD Warmwasser Laufzeit | `?s=1,1` | integer hours |
| 1522 | NHZ 1 Laufzeit | `?s=1,1` | integer hours |
| 1523 | NHZ 2 Laufzeit | `?s=1,1` | integer hours |
| 1524 | VD Heizen Tag | `?s=1,1` | integer kWh (display decimal discarded) |
| 1525 | VD Heizen Summe | `?s=1,1` | integer MWh (display precision discarded) |
| 1526 | VD Warmwasser Tag | `?s=1,1` | integer kWh (display decimal discarded) |
| 1527 | VD Warmwasser Summe | `?s=1,1` | integer MWh (display precision discarded) |
| 1528 | NHZ Heizen Summe | `?s=1,1` | integer MWh (display precision discarded) |
| 1530 | Leistung WP | `?s=1,0` | integer percent |
| 1531 | Lstg PU Pumpe | `?s=1,0` | integer percent |

The input files expose no labeled tank-top sensor, discrete compressor-on state, or independent energy meter. `?s=4,2` supplies editable upper setpoints (including `OBERE WARMWASSER SOLLTEMP.`), not the actual tank measurement. No fictitious status registers are added. Registers 800 and 1500 use the conventional-looking addresses requested; only the mapped proxy's self-consistency is guaranteed, not compatibility with native Stiebel Modbus register definitions.

801 was unused in the existing map and implementation; all existing addresses, including 800 and 1500, are preserved. All other registers are read-only. Write one register per request (FC06 or FC16 with count 1); multiple-register writes are rejected before any POST because the HTTP interface cannot provide an atomic transaction. Invalid writes and failed/unconfirmed POSTs leave the cache and register unchanged and yield Modbus exception `0x04` (Slave Device Failure) with pymodbus 3.6.9. Only a successful HTTP status and JSON `{"success":true}` update the local image. This is not device readback: a timeout can occur after the ISG applied a change. See README for the inferred protocol and real-device verification requirements.
