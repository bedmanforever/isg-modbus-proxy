# ISG Servicewelt v12.2.3 → Modbus TCP proxy

Scrapes the Stiebel-Eltron ISG web UI and serves a local holding-register image. Python 3.11; pymodbus 3.6.9. It is an unofficial web-UI adapter, not native Modbus on the WPM.

## Run

```sh
cp .env.example .env  # edit ISG_URL if needed
# docker compose reads .env automatically
docker compose up --build -d
```

Set `ISG_URL` (default `http://isg`), `POLL_INTERVAL` seconds (default 30), `ISG_USERNAME` and `ISG_PASSWORD` if HTTP basic auth is enabled, and optionally `MODBUS_PORT` (container default 502). The browser HAR has no auth cookies or auth exchanges; credentials are optional, conditional on local setup. TCP/502 must be reachable from HA/EVCC; use a LAN firewall, as Modbus TCP has no authentication.

Files: `proxy.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `home-assistant-modbus.yaml`, `evcc.yaml`, `REGISTER_MAP.md`.

## Data extraction / mappings

Actual status values in `?s=1,0` and counters in `?s=1,1` are rendered as table labels and text, including comma decimals and units. Editable settings in `?s=4,0/1/2` are populated by inline `jsvalues[id].val` scripts. Dashboard mode is hidden input `val1`. The poller only commits fields parsed on successful requests; individual HTTP/parse errors warn and do not terminate the process. Unknown or missing registers return `0xFFFF`.

Consult `REGISTER_MAP.md` for register source and scale. Temperatures are signed int16 tenths °C (e.g. wire word `0xFFA4` = -9.2 °C). Check raw `0xFFFF` for missing data before conversion: signed decoding alone turns it into -0.1 °C, indistinguishable from that real temperature. Failed polls retain previous values, so readings may be stale. Note the source pages have different granularity for energy totals than a precision-preserving telemetry interface; this prototype stores those registers as integer quantities and intentionally documents loss of decimals. `?s=1,0` contains actual flow/return/outdoor/Warmwasser readings; `?s=1,1` includes VD/NHZ runtime and energy totals. `?s=4,1` has comfort (val22) and eco (val23) setpoints. The uploaded `?s=4,2` contains upper target settings, not measured temperatures.

## Writes and trace findings

Both uploaded DevTools files contain 51 requests each, all GETs; neither records a write call. They do contain `scripts.js`, whose `saveChange()` implementation specifies `$.post('./save.php', {data: dataString})`; `dataString` is JSON from `#werte.serializeArray()`, sent as form-encoded parameter `data`. Register **800** now sends only comfort `val22`, e.g. `data=[{"name":"val22","value":"48,0"}]`. New register **801** sends only Eco `val23`, e.g. `data=[{"name":"val23","value":"42,0"}]`. Both accept raw **100–600**, representing **10–60 °C** in 0.1 °C steps. 801 was free in the previous map and implementation; existing addresses remain unchanged.

The uploaded increase/decrease HARs do NOT provide an observed body, POST method, response, or hidden anti-CSRF fields. The POST method/endpoint and serialization are inferred from the captured JS source, not observed in the captured interaction. **Single-field submissions and JSON success responses have not been verified on live hardware.** Verify on your firmware that writing 800 changes only comfort, writing 801 changes only Eco, the other target remains unchanged, and the next poll reads back each change. Do not rely on writes until this is confirmed.

For Betriebsart, root page `isg.html` exposes a hidden `val1` input (current `2`) and `val1s` radio options `0`–`5`. Register **1500** retains its existing `save.php` payload `[{"name":"val1","value":"N"},{"name":"val1s","value":"N"}]`; exact mode write behavior is likewise inferred (no mode-change trace supplied). This must be validated locally. Do not enable writes until tested; writes to heating operating mode can change plant operation.

Writes are validated before any POST or local mutation. Only registers 800, 801 and 1500 are writable, one register per request (FC06 or FC16 count 1); multi-register writes are rejected without posting to avoid partial transactions. HTTP errors, timeouts, rejected writes (even with a warning), malformed responses, and responses without explicit JSON `{"success":true}` leave local registers/cache unchanged. With pymodbus 3.6.9 these failures and invalid writes produce Modbus exception **0x04**, not a successful echo. `DataBlock.setValues()` still returns `None` on success; error return values would be ignored by this pymodbus version, so failures raise instead. A confirmed write updates the cache and register immediately, but is not physical readback. A timeout may happen after a change was applied remotely; re-read from the ISG before retrying.

No username/password was exchanged in traces. Configure credentials only if the local ISG requires Basic Auth. Sessions/cookies from browser traces were empty.

## Integrations / compatibility caveat

HA example is `home-assistant-modbus.yaml`. The proxy holds unsigned wire words for signed temperatures; configure `data_type: int16`, scale `0.1`, and filter the missing sentinel. HA's `nan_value: -1` matches both signs before scaling, so the example also omits +0.1 °C; use a separate raw-value/template filter if that distinction matters. Example switches write fixed comfort/Eco targets; their off values are lower targets, not heater-off commands. Example client timeouts exceed the proxy's 10-second HTTP write timeout to allow a Modbus exception response.

EVCC's Stiebel WPM templates speak the manufacturer's actual register layout, not the custom map here. This proxy implements neither native WPM Modbus nor EVCC's heater power interface. Adding `setmaxpower` does not supply the missing device capabilities or create a heat-pump UI integration. No compressor state or watts are inferred: registers 1530/1531 are percentages, not power meters.

`evcc.yaml` contains **custom plugin recipe fragments, not a runnable EVCC configuration or native heat-pump device/UI integration**. The `temp` getter reads signed register 503 and explicitly rejects the missing sentinel. A supported custom-charger `enable` setter forwards a normal/boost DHW comfort target through a JavaScript float output to a Modbus `writeholding` plugin (FC06, scale 10). It changes only val22; use address 801 to control Eco instead. “Disable” lowers the target, not heater off; operating mode/schedules still determine heating. There is no generic `settemp` attribute here, and `limittemp` is a getter, not a setter.

These fragments need an otherwise valid custom charger/integration with real implementations of its required control/status capabilities; the proxy alone cannot supply them. If those capabilities are unavailable, use HA or another external automation to write the targets instead of configuring a fake EVCC charger. Do not copy the fragments as a complete `chargers:` entry. Replace the host/port, select appropriate normal/boost temperatures, and check plugin support in your installed EVCC version. The recipe was checked against [EVCC Modbus getters/setters](https://github.com/evcc-io/evcc/blob/6b1f9584286fd4ab9a702b1e77e5aede8e967300/plugin/modbus.go#L138-L248), [JavaScript transformations](https://github.com/evcc-io/evcc/blob/6b1f9584286fd4ab9a702b1e77e5aede8e967300/plugin/transformation.go#L102-L110), and [custom charger attributes](https://github.com/evcc-io/evcc/blob/6b1f9584286fd4ab9a702b1e77e5aede8e967300/charger/charger.go#L32-L77); no live EVCC/ISG end-to-end verification was performed.

## Self-test

Install `requirements.txt`, then run `python selftest.py`. Self-contained synthetic HTML and fake HTTP responses test parsing/scaling, signed temperatures, the missing sentinel, separate val22/val23 payloads, limits, mode compatibility, and unchanged cache/registers on failures. An ephemeral local pymodbus TCP server verifies reads, FC06/FC16 writes and failure exceptions. No live ISG is contacted; these tests do not validate the inferred `save.php` protocol or physical heat-pump behavior.
