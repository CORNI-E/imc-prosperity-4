# IMC Prosperity 4 — Algorithmic Trading

**Team GSTD** · Team Lead: Edgar Cornillet  
Télécom SudParis — Institut Polytechnique de Paris

## Results

| Round | Algo Score | Rank | Submitted Strategy |
|-------|-----------|------|-------------------|
| Round 1 | **100,994** XIRECs | 325 | V9d — MM + Trend Capture with Confidence Gate |
| Round 3 | TBD | — | V9 — MM + BS Options Pricing + Trend Capture |

## Repository Structure

```
imc-prosperity-4/
├── round1/
│   ├── submitted/          # Final submitted code
│   │   └── trader.py       # V9d — MM + trend capture on Pepper Root
│   └── versions/           # Version history
│       ├── v1_initial.py           # First algo (SMA trend following)
│       ├── v3_baseline.py          # Optimized MM (EMA + WMID + skew)
│       ├── v9_trend_mild.py        # + trend bias ×3
│       ├── v9b_trend_moderate.py   # + trend bias ×5, skew=2
│       ├── v9c_trend_aggressive.py # + trend bias ×7, skew=1.5
│       └── v9d_trend_confidence_gate.py  # + confidence gate (SUBMITTED)
├── round3/
│   ├── submitted/          # Final submitted code
│   │   └── trader.py       # V9 — MM + BS + trend on VE/HP
│   └── versions/           # Version history
│       ├── v1_initial_bs.py        # BS pricing, hardcoded sigma (-18,626!)
│       ├── v2_implied_vol.py       # Dynamic IV (-1,495)
│       ├── v3_no_taking.py         # Taking disabled (+1,803)
│       ├── v5_delta_hedge.py       # Delta hedging (-337, failed)
│       ├── v6_optimized.py         # Parameter tuning (+1,819)
│       ├── v8_hydrogel_trend.py    # + trend on Hydrogel (+1,657)
│       └── v9_full_trend.py        # + trend on VE+HP (SUBMITTED)
├── analysis/
│   └── log_analysis.py     # Log analysis & microstructure tools
├── docs/
│   ├── IMC_Prosperity4_Strategy.pdf      # Strategy document (LaTeX)
│   └── IMC_Prosperity4_Strategy_Overview.md
└── README.md
```

## Strategy Overview

### Round 1 — Market Making + Trend Capture

**Products:** Intarian Pepper Root (limit 80) · Ash Coated Osmium (limit 80)

The algorithm combines a **passive market maker** (V3) with **conditional trend capture** (V9d):

1. **Fair value estimation** — EMA of volume-weighted mid price (α = 0.15)
2. **Inventory management** — Exponential skew: `pos/limit × (1 + 2 × (pos/limit)²)`
3. **Multi-level quoting** — Penny + theoretical price, 60/40 budget split
4. **Adaptive spread** — Widens with volatility, tightens when calm
5. **Trend capture** — Fast/slow EMA crossover, ×7 bias with confidence gate

**Key discovery:** Spike bots are **informed** (average PnL of -1.1 per spike trade, only 33% profitable). Aggressive taking loses money → pure passive strategy is optimal.

**Core innovation:** The **confidence gate** (`|trend| / volatility`) enables aggressive positioning when the trend is clear and automatically falls back to baseline MM when the market is choppy. This eliminates overfitting risk.

### Round 3 — Options Market Making

**Products:** Hydrogel Pack · Velvetfruit Extract · 10 VEV Vouchers (call options)

1. **Dynamic implied vol** — Backed out from the most liquid ATM VEVs each tick
2. **Black-Scholes pricing** — Option fair values recalculated every tick
3. **No taking** — Adverse selection confirmed on all products
4. **Trend capture** — Applied to Hydrogel (×7) and VE (×5) with confidence gate

**Key discovery:** Implied volatility is **flat at ~24%** across all strikes — no smile, no skew. The market uses BS with a fixed sigma, giving us accurate fair values.

## Score Progression (Round 1)

```
V1  (SMA trend)        :   2,400  ■■
V3  (optimized MM)     :   4,883  ■■■■■
V9  (trend ×3)         :   5,775  ■■■■■■
V9b (trend ×5)         :   7,664  ■■■■■■■■
V9c (trend ×7)         :   9,202  ■■■■■■■■■
V9d (+ conf. gate)     :   9,202  ■■■■■■■■■  ← submitted
Competition result     : 100,994  (×10 days)
```

## Key Insights

1. **Data over intuition.** Every decision was validated empirically. "Clever" versions (V4-V6) all lost money. Log analysis revealed that spike bots were informed — a discovery that shaped the entire strategy.

2. **Adverse selection is the market maker's real enemy.** Being more aggressive isn't better. The conservative V3 beat every aggressive variant because it avoided trading against informed bots.

3. **Conditional risk management beats static risk management.** The confidence gate dynamically adapts risk to the market regime — more sophisticated than a fixed stop-loss.

4. **Market microstructure determines strategy.** The same products on different markets (spread of 3 vs spread of 16) require fundamentally different approaches. Textbook strategies don't work without understanding specific microstructure.

## Tech Stack

- **Language:** Python 3
- **Concepts:** Market Making, EMA, Black-Scholes, Implied Volatility, Adverse Selection, Trend Following, Inventory Management, Auction Optimization
- **Analysis:** Pandas, JSON/CSV log parsing, auction simulation

## Author

Edgar Cornillet — [edgar.cornillet@telecom-sudparis.eu](mailto:edgar.cornillet@telecom-sudparis.eu)  
2nd year Engineering Student, Télécom SudParis (Institut Polytechnique de Paris)  
Specialization: Statistical Modelling and Applications (MSA)
