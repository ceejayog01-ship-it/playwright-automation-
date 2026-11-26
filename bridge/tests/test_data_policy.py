"""Enforceable data policy: Yahoo Finance is the only market-price provider; Headway supplies no candles or prices."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BANNED = re.compile(r"twelve.?data|dukascopy|bootstrap_native|ingest-native|ingest-m1|headway_native|headway_bootstrap", re.I)
CODE_DIRS = [ROOT / "bridge" / "tsebridge", ROOT / "scripts", ROOT / ".github" / "workflows", ROOT / "headway" / "headway-mt5-web-automation" / "src"]
CODE_EXT = {".py", ".sh", ".yml", ".yaml", ".ts"}


def code_files():
    for d in CODE_DIRS:
        for p in d.rglob("*"):
            if p.is_file() and p.suffix in CODE_EXT and "node_modules" not in p.parts:
                yield p
    yield ROOT / "tse-reference" / "tse-mt5-integration" / "requirements.txt"


def test_no_other_market_data_provider_remains_in_code_or_workflows():
    offenders = [str(p.relative_to(ROOT)) for p in code_files() if BANNED.search(p.read_text(errors="ignore"))]
    assert offenders == []


def test_removed_files_stay_removed():
    for rel in ("scripts/bootstrap_dukascopy.py", "bootstrap_dukascopy.py", "scripts/bootstrap_yfinance.py", "apply_yfinance_headway_patch.py",
                "headway/headway-mt5-web-automation/src/history.ts", "headway/headway-mt5-web-automation/src/ticks.ts",
                "headway/headway-mt5-web-automation/src/chartprobe.ts", "headway/headway-mt5-web-automation/src/probe.ts",
                "headway/headway-mt5-web-automation/src/chartparse.ts", "headway/headway-mt5-web-automation/src/nativeRates.ts",
                "headway/headway-mt5-web-automation/src/quotes.ts", ".github/workflows/chart-probe.yml", ".github/workflows/demo-trade-test.yml"):
        assert not (ROOT / rel).exists(), rel


def test_headway_cli_has_no_candle_or_price_modes():
    idx = (ROOT / "headway" / "headway-mt5-web-automation" / "src" / "index.ts").read_text()
    for banned in ("fetchNativeM30", "collectSamples", "aggregateTicks", "readQuotes", "waitForQuote", "writeQuotesFile", "chartprobe"):
        assert banned not in idx, banned
    m = re.search(r"--mode <mode>', '([^']+)'", idx)
    assert m and set(x.strip() for x in m.group(1).split("|")) == {"login", "order", "strategy-order", "monitor"}


def test_headway_src_reads_no_bid_ask_prices():
    for p in (ROOT / "headway" / "headway-mt5-web-automation" / "src").glob("*.ts"):
        txt = p.read_text()
        assert not re.search(r"\bquote\.(bid|ask)\b|\bq\.(bid|ask)\b|parseQuotes|readQuotes", txt), p.name


def test_scheduled_workflow_uses_yfinance_and_stays_demo_locked_with_one_order_cap():
    wf = (ROOT / ".github" / "workflows" / "market-cycle.yml").read_text()
    assert "Yahoo Finance" in wf and "YFINANCE_API_KEY" not in wf and "TWELVE_DATA_API_KEY" not in wf
    assert "ACCOUNT_MODE: demo" in wf and "BRIDGE_EXPECTED_SERVER: Headway-Demo" in wf and 'MAX_ORDERS_PER_CYCLE: "1"' in wf
    sh = (ROOT / "scripts" / "run_cycle.sh").read_text()
    assert "fetch_market_data.py" in sh and "MAX_N=1" in sh and "--mode=history" not in sh and "--quotes" not in sh
