# Project rules

Use neutral aliases only in records. Do not write personal names, identity mappings, credential contents or identity-bearing local paths.

The user authorized a real-data website update on 2026-10-15 using existing local data, after the new provider's full delivery proved unavailable. The default homepage is a retrospective daily evidence study of the local July 2026 data: 5,210 stock codes, 23 dates, and 119,652 stock-days. Preserve the actual limited date coverage; the requested latest-year scope remains a future data requirement. Do not join the provider's limited 2025 exports to 2026 events. Do not fetch AWS or restart completed research work.

The independent `demo.html` page retains the original eight-stock intraday replay. Its reasons and probabilities are invented examples; keep the concise Demo labels visible in each popup, reason list and export. Never describe them as calibrated estimates, verified news or causal findings. Never mix fictional reasons into the real-data homepage or its exports.

Real-data output reports measured returns, a clearly labeled sample comparison, historical volume/spread baselines, missingness and price-reference diagnostics. These are descriptive evidence, not causal estimates or reason probabilities. Full-day observations are retrospective and cannot be represented as information available intraday. Only earlier observations may form historical baselines; incomplete history remains missing. The source's final snapshot price is not independently confirmed as the exchange close. No news, historical industries or corporate-action validation has been added.

For the retained Demo, only finished five-minute close observations trigger events. Default basis is current close relative to the highest previously completed close of the same day; alternate basis is reference previous close. Strict decline greater than 5%, with 1e-12 tolerance. Emit when crossing the threshold, not on every bar that remains below it. A recovery permits a new crossing. The next-event navigation is retrospective case selection, not prediction. The real-data homepage uses the separate daily event definition documented in docs/REAL_DATA_METHOD.md.

Use relative static paths for GitHub Pages. Do not add server authentication to a public static page. Never commit .runtime/, accounts, keys, raw archives or unrelated market data.

Run `npm test`, `python -m unittest discover -s tests -p test_real_study.py`, `python tools/check_real_browser.py`, and `python tools/check_browser.py` for material data/UI changes. Pass the actual HTTPS root URL to browser checkers to verify publication; the legacy checker targets `demo.html`. Browser/runtime records stay ignored. Publishing must derive the existing target from origin, verify the authorized credential and repository push permission, use an explicit file allowlist, and never create or change an unrelated repository.
