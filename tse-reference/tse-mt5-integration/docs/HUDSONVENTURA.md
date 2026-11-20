# How to get Hudsonventura MT5 Docker docs & endpoints

## Official sources

| Resource | URL |
|----------|-----|
| Docker Hub image | https://hub.docker.com/r/hudsonventura/mt5 |
| GitHub repo + README | https://github.com/hudsonventura/MT5_Docker |
| docker-compose example | https://raw.githubusercontent.com/hudsonventura/MT5_Docker/refs/heads/main/src/docker-compose.yml |
| MQL5 compiler compose | https://raw.githubusercontent.com/hudsonventura/MT5_Docker/refs/heads/main/src/docker-compose-mt5-compiler.yml |
| MetaTrader .ini docs | https://www.metatrader5.com/en/terminal/help/start_advanced/start |

## What Hudsonventura actually provides

`hudsonventura/mt5` runs **MetaTrader 5 inside Docker (Wine)** with:

- **VNC** on port `5901`
- **noVNC (browser)** on port `6901`
- **MQL5 folder mount** (Experts, Include, Files, …)
- **Optional auto-login** via `MQL5/Files/mt5.ini`
- **MQL5 compiler** via `/build.sh` (CI-friendly)

It does **not** expose a Liberatti-style REST trading API (`/order/open`, `/copy_rates_from_pos`, …).

So the correct integration with our architecture is **file-based**:

```
TSE Python engine
      │
Signal Output Interface  →  state/signals_canonical.jsonl
      │
shared volume / MQL5/Files/
      │
MQL5 EA inside Hudsonventura  →  places / manages orders
```

## Quick start (terminal only)

```bash
mkdir -p .data/MQL5
wget -O docker-compose.mt5.yml \
  https://raw.githubusercontent.com/hudsonventura/MT5_Docker/refs/heads/main/src/docker-compose.yml

# edit VNC_PW, then:
docker compose -f docker-compose.mt5.yml up -d

# browser access:
# http://localhost:6901/vnc.html?password=YOUR_PASSWORD
```

On first run the image downloads and installs MT5 (~2 minutes).

## Auto-login (.ini)

Create `.data/MQL5/Files/mt5.ini`:

```ini
[Common]
Login=123456
Password=YourPassword
Server=YourBroker-Server
```

Mount is already defined in their compose file under `MQL5/`.

## Compile an EA (no GUI)

```bash
mkdir -p mql5/Experts mql5/Include
# put YourExpert.mq5 into mql5/Experts/

wget -O docker-compose-mt5-compiler.yml \
  https://raw.githubusercontent.com/hudsonventura/MT5_Docker/refs/heads/main/src/docker-compose-mt5-compiler.yml

docker compose -f docker-compose-mt5-compiler.yml up
# compiled .ex5 appears next to the .mq5
```

## Implications for this TSE repo

1. **Candle data** for `scan_once.py` must come from somewhere else (CSV via `KB_M30_CSV`, a separate data bridge, or a different MT5 API image). Hudsonventura alone does not feed Python rates.
2. **Order execution** should be done by an MQL5 EA that reads the canonical JSONL (or a simplified signal file) from `MQL5/Files/`.
3. **Equivalence audit** in this repo still validates that every TSE signal is preserved before anything is handed to that EA.

If you later need a REST bridge in front of MT5, that is a different product (e.g. meta-docker, mt5linux, mt5-docker-api). Hudsonventura is the terminal + compiler environment only.

---

## File-drop trade path (implemented)

### How it works

1. Run `hudsonventura/mt5` with the MQL5 folder mounted (`docker-compose.hudsonventura.yml`).
2. Signal Interface + `mt5_execute.py` write:
   - `state/signals_canonical.jsonl` (audit / equivalence)
   - `mql5/Files/tse_signals.csv` and `.data/MQL5/Files/tse_signals.csv` (EA input)
3. EA `TSE_SignalExecutor.mq5` polls the CSV and calls normal MQL5 trade functions.
4. Design A: SL only, time-exit after `horizon_bars` × M30.
5. Design B: SL + TP from the signal.

### CSV format (`tse_signals.csv`)

```
signal_id,instrument,direction,design,entry,sl,tp,horizon_bars
TSE-A-EURUSD-...,EURUSD,BUY,A,1.08750,1.08420,,48
TSE-B-EURUSD-...,EURUSD,BUY,B,1.08750,1.08420,1.09300,48
```

Design A has an **empty** `tp` field (never `0.0`).

### Compile the EA

```bash
docker compose -f docker-compose.hudsonventura.yml --profile compile up mt5-compiler
# .ex5 appears next to the .mq5 in mql5/Experts/
```

### Attach the EA

1. `docker compose -f docker-compose.hudsonventura.yml up -d`
2. Browser: `http://localhost:6901/vnc.html?password=change_me`
3. Log into your broker (or use `MQL5/Files/mt5.ini`).
4. Open the symbol chart (e.g. EURUSD M30).
5. Drag `TSE_SignalExecutor` onto the chart.
6. Keep **InpDryRun = true** until demo behaviour looks correct, then set false.

### Generate a drop-file from Python

```bash
PYTHONPATH=. python -m signal_interface.exporter --demo --count 4 --out state/signals_canonical.jsonl
# then run the demonstration executor (writes the CSV):
PYTHONPATH=. python mt5_execute.py
# or only the CSV writer:
PYTHONPATH=. python -c "
from signal_interface.exporter import _demo_signals
from signal_interface.ea_drop import write_ea_csv
from pathlib import Path
write_ea_csv(_demo_signals(4), Path('mql5/Files/tse_signals.csv'))
write_ea_csv(_demo_signals(4), Path('.data/MQL5/Files/tse_signals.csv'))
print('written')
"
```
