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

Consult `REGISTER_MAP.md` for register source and scale. Temperatures are signed int16 tenths °C. Note the source pages have different granularity for energy totals than a precision-preserving telemetry interface; this prototype stores those registers as integer quantities and intentionally documents loss of decimals. `?s=1,0` contains actual flow/return/outdoor/Warmwasser readings; `?s=1,1` includes VD/NHZ runtime and energy totals. `?s=4,1` has comfort (val22) and eco (val23) setpoints. The uploaded `?s=4,2` contains upper target settings, not measured temperatures.

## Writes and trace findings

Both uploaded DevTools files contain 51 requests each, all GETs; neither records a write call. They do contain `scripts.js`, whose `saveChange()` implementation specifies `$.post('./save.php', {data: dataString})`; `dataString` is JSON from `#werte.serializeArray()`, sent as form-encoded parameter `data`. For page 4,1 the fields are `val22` (comfort) and `val23` (eco), so this proxy sends both: e.g. `data=[{"name":"val22","value":"48,0"},{"name":"val23","value":"42,0"}]`. The uploaded increase/decrease HARs do NOT provide an observed body, POST method, response, or hidden anti-CSRF fields. The POST method/endpoint and serialization are inferred from the captured JS source, not observed in the captured interaction. Verify against your ISG before relying on writes; error handling and the exact response vary by firmware.

For Betriebsart, root page `isg.html` exposes a hidden `val1` input (current `2`) and `val1s` radio options `0`–`5`. The proxy constructs `save.php` data with `[{"name":"val1","value":"N"}]`; exact mode write behavior is likewise inferred (no mode-change trace supplied). This must be validated locally. Do not enable writes until tested; writes to heating operating mode can change plant operation.

No username/password was exchanged in traces. Configure credentials only if the local ISG requires Basic Auth. Sessions/cookies from browser traces were empty.

## Integrations / compatibility caveat

HA example is `home-assistant-modbus.yaml`. The proxy holds unsigned wire words for signed temperatures; configure `data_type: int16`, scale `0.1`.

The requested EVCC `stiebel-eltron`/WPM template speaks the manufacturer's actual register layout, not the custom 500/800/1500 map here. The supplied premise says this WPMme has no native Modbus; a web-scraping proxy cannot pretend to be a complete WPMsystem/WPM3 Modbus server. `evcc.yaml` therefore shows EVCC's Stiebel WPMsystem template only as a reference for a real WPM Modbus endpoint and points it at a placeholder, NOT this proxy. Native EVCC heatpump compatibility is not implemented; a custom EVCC template/integration or genuine Modbus mapping is needed.

## Self-test

Run `python selftest.py`. It mocks the web session with the uploaded `isg-1_0.html`, tests parsing/scaling and missing-register sentinel, then runs an ephemeral pymodbus TCP server and verifies a client read and write. Write is intercepted in a fake session: no live ISG is contacted.
