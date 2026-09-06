---
name: momentum-trader
description: Swing / momentum skill for the Trading Agent OS. Require uptrend, constructive RSI, and specialist consensus before a buy.
metadata: {"navin":{"emoji":"📊","category":"trading"}}
---

# Momentum Trader

Only candidates in an uptrend (price above SMA50, SMA50 above SMA200 when present). Skip overbought RSI chases. Consensus 4/5 and confidence floor still apply. Size from the risk engine, never from conviction prose.
