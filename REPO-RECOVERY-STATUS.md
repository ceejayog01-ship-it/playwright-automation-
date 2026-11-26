# Repository recovery status

The original repository ZIP was supplied by the user and merged with the Yahoo Finance / Headway update package.

- Original TSE scanner and reference integration preserved.
- Original Headway Playwright login, account, order, and broker-confirmation source preserved, with updated execution/position modules overlaid.
- Yahoo Finance via `yfinance` is the sole external market-price provider in the active cycle.
- Headway quote/candle collection is not used in the active cycle; Headway is used for authentication/account access, execution, and broker confirmations.
- Dukascopy bootstrap code and its test were removed.
- Supabase-removal patch artifacts, nested ZIPs, and obsolete patch installer files were removed.
- Active market-cycle workflow uses the scheduled cycle and remains locked to the demo account.

This is a source-level reconstruction. A live Yahoo download, GitHub Actions run, Headway login, broker-confirmed order/close, and Telegram delivery still require validation in the user's GitHub environment. Do not treat a workflow green check as proof of broker execution.
