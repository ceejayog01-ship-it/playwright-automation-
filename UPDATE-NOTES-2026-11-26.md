# Update notes — 2026-11-26

## Headway login behavior
- Removed exact visible server-label matching and the `server_label_missing` abort.
- Login now submits the configured credentials on the configured mode endpoint, then requires the login form to disappear and a terminal workspace to appear before downstream execution.
- The bridge no longer compares the server text or exact terminal host. Demo/real mode is still gated by the configured account mode and endpoint path.
- No credentials, account number, or exact server label are logged or compared.

## Stale result and data contracts
- The cycle removes prior auth/order/monitor result JSON before a new cycle so an old result cannot be ingested as current.
- Strategy-order result ingestion checks schema, kind, signal id, outcome and confirmation fields before copying it into the bridge ledger.
- Yahoo Finance remains the only price/candle provider. Headway quote files are not used.

## Required frozen strategy data missing from supplied archive
The supplied ZIP did not include `tse-reference/tse-mt5-integration/data/strategies.json` or `edges.json`. These are the actual frozen strategy definitions/mappings and cannot be recreated safely from code. The workflow now fails early with an explicit missing-input message rather than logging a scanner traceback or pretending a strategy scan ran. Restore these exact original files to enable signal generation.

## Verification
- Python bridge tests: 89 passed.
- Node policy test passed. Full Node automation test/build could not be completed in this environment because the `playwright` package was not available after dependency installation timed out.
- No live Headway login or broker order was tested.
