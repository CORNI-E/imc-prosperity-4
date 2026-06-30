# IMC Prosperity 4 — Algorithmic Trading Strategy
## Interview Preparation Document — Edgar Cornillet, Team Lead

---

## 1. Competition Context

IMC Prosperity 4 is an international algorithmic trading competition organized by IMC Trading. Participants design Python algorithms to trade products on simulated real-time markets, competing against bots with various behaviors. The goal is to maximize Profit & Loss (PnL) across multiple trading days.

In Round 1, two products were available: **Intarian Pepper Root** (position limit: 80) and **Ash Coated Osmium** (limit: 80). We had 3 days of historical data for similar products (Rainforest Resin, Kelp, Squid Ink).

**Final result: 100,994 XIRECs, rank 325 on the algorithmic leaderboard.**

---

## 2. Baseline Architecture: the V3 Market Maker

### 2.1 Market Making Principle

A market maker provides liquidity by simultaneously posting buy orders (bids) and sell orders (asks). Profit comes from the difference between buy and sell prices — the **captured spread**. It is a non-directional strategy: we don't bet on market direction, we profit from order flow.

### 2.2 Technical Components

Our V3 algorithm was built on five pillars:

**a) Fair Value via EMA (Exponential Moving Average)**
We estimate "fair value" through an EMA of the volume-weighted mid price (WMID). The alpha of 0.15 balances responsiveness (tracking moves) and stability (filtering noise). WMID provides a more accurate mid-price than a simple bid/ask average because it accounts for volume asymmetry in the order book.

**b) Inventory Management via Exponential Skew**
When we accumulate a long or short position, we're exposed to directional risk. The inventory skew adjusts our fair value to push us toward flat. The formula `inv_ratio × (1 + 2 × inv_ratio²)` is **exponential**: the penalty is mild for small positions but spikes near the limits, preventing extreme positions that would lock us out.

**c) Multi-Level Quoting (Penny + Theoretical)**
We post orders at two price levels. Level 1 (60% of budget) uses the minimum of the "penny" price (best_bid + 1) and the theoretical price (adj_fair ± spread). Level 2 (40%) posts 1 tick deeper. This approach maximizes fill probability while capturing spread.

**d) Volatility-Adaptive Spread**
The base_spread widens when volatility increases (volatile market → more risk → we demand more compensation) and tightens when it decreases. Volatility is measured via an EMA of absolute price changes.

**e) Aggressive Taking Layer**
When the order book shows a price clearly mispriced relative to our fair value (ask < adj_fair), we "take" liquidity immediately instead of waiting passively.

### 2.3 V3 Performance

Score: **~4,883** on the test sample (1K ticks). This was our solid, generic starting point applied uniformly to both products.

---

## 3. Log Analysis: the Methodological Breakthrough

### 3.1 The Data-Driven Approach

The real progress came from analyzing **simulation logs** (128578.log). Instead of modifying the algorithm based on intuition, we extracted quantitative insights from every trade, every tick, every order book movement.

### 3.2 Key Discoveries

**Discovery 1: Real spreads are 5× wider than historical data.**
Historical data showed spreads of 2–3 ticks. Actual data: Pepper Root at 13, Osmium at 16. This fundamentally changed the economics of each trade.

**Discovery 2: Spike bots are informed — taking is negative EV.**
We analyzed all 71 "spikes" (moments when bid/ask jumped by >5 ticks). Measuring the average PnL 5 ticks after each spike trade: **-1.1 per trade, only 33% profitable**. Bots placing aggressive orders *knew* the price was about to move in their direction. Trading against them was a losing proposition.

This discovery explained why our aggressive versions (V4, V5, V6) all lost money: they caught more spikes, but each spike had negative expected value.

**Discovery 3: Pepper Root has a constant drift of +101 ticks over 100K ticks.**
Plotting Pepper Root's price revealed a linear, constant upward drift (+52 first half, +49 second half). Osmium was perfectly stable around 10,000. This was the only unexploited optimization lever.

**Discovery 4: Position utilization was only 12%.**
Maximum position reached: ~10 out of a limit of 80. The bottleneck wasn't our capacity but the frequency at which bots crossed our prices (142 trades in 100K ticks).

---

## 4. The Innovation: Trend Capture with Confidence Gate (V9d)

### 4.1 The Concept

The core idea: on a trending product, we can capture directional movement **on top of** market making profit by biasing our fair value in the trend direction. When Pepper Root rises, we shift adj_fair upward → we buy more easily and sell less → we accumulate a long position → we profit from the drift.

### 4.2 Technical Implementation

**Trend detection:** Two EMAs at different speeds — a fast one (alpha=0.15) and a slow one (alpha=0.02, ~50 tick memory). The difference `fast_EMA - slow_EMA` provides the trend signal. Positive = uptrend, negative = downtrend.

**The Confidence Gate (key innovation):** We don't want to react to noise. We compute `trend_confidence = |trend| / volatility`. A strong trend in a calm market yields high confidence; a weak trend in a volatile market yields low confidence. Both bias and skew adapt:

| Regime | Confidence | Bias | Skew | Behavior |
|--------|-----------|------|------|----------|
| Strong trend, calm | 1.0 | ×7.0 | 1.5 | Aggressive, accumulates position |
| No trend | 0.0 | ×0.0 | 3.0 | Falls back to V3 baseline |
| Weak trend, choppy | 0.25 | ×1.2 | 2.6 | Cautious |

**Adaptive skew:** When confidence is high, we reduce the inventory skew (3.0 → 1.5) to allow holding a large directional position. When confidence is low, skew remains strong for protection.

### 4.3 Score Progression

| Version | Score | Change | Key Insight |
|---------|-------|--------|------------|
| V3 | 4,883 | — | Generic MM, baseline |
| V9 (×3) | 5,775 | +18% | First trend bias, conservative |
| V9b (×5) | 7,664 | +57% | Stronger bias, reduced skew |
| V9c (×7) | 9,202 | +88% | Maximum bias, position 78/80 |
| V9d (×7 + gate) | 9,202 | +88% | Same performance + choppy market protection |

V9d is identical to V9c on a trending market but automatically protects on a trendless market. This is the version we submitted.

### 4.4 Overfitting Risk Management

The main risk was calibrating parameters (×7, skew 1.5) on a single sample. Our protections:

- **The confidence gate**: without a detected trend, the bias drops to 0 and performance reverts to V3 (~4,800). The floor is guaranteed.
- **Per-product separation**: Osmium has zero trend bias (identical to V3). Only Pepper Root has trend capture, as it's the only product with documented drift.
- **Qualitative validation**: In-game tips from Orin confirmed "slow growth creates structure" for Pepper Root, validating our drift hypothesis.

---

## 5. Manual Challenge: Auction Optimization

### 5.1 The Problem

Two products auctioned (Dryland Flax, Ember Mushroom) with fixed order books. We submit last → our orders influence the clearing price. After the auction, the Merchant Guild buys back at a fixed price.

### 5.2 Methodology

We coded an **auction simulator** that, for every possible (price, volume) pair:
1. Recalculates the clearing price (maximum traded volume, tie-break by highest price)
2. Computes our fill (accounting for priority — we're last)
3. Calculates net profit (clearing price vs buyback price)

### 5.3 Results

- **Dryland Flax:** BUY at 29, volume 5,000 → pushes clearing from 28 to 29, profit 5,000
- **Ember Mushroom:** BUY at 18, volume 35,000 → pushes clearing from 15 to 18, profit 66,500

The key insight for Ember Mushroom: by placing a large buy order, we **shift** the clearing price upward (from 15 to 18), while remaining below the buyback (20). This is a direct application of Orin's advice: "volume tips the scale."

---

## 6. Key Takeaways (Interview Talking Points)

### What I learned about quantitative trading:

1. **Data over intuition.** Every decision was validated by data. "Clever" versions (V4 = dual EMA, V5 = size bias, V6 = aggressive penny) all lost money. It was the log analysis that revealed spike bots were informed — a discovery that shaped the entire strategy.

2. **Adverse selection is the market maker's real enemy.** In a market with informed participants, being more aggressive is not better. The "boring" V3 beat every aggressive variant because it avoided trading against bots that knew something we didn't.

3. **Conditional risk management beats static risk management.** The confidence gate dynamically adapts risk to the market regime. This is more sophisticated than a fixed stop-loss or a static position limit.

4. **Market microstructure determines strategy.** The same products on different markets (spreads of 3 vs spreads of 16) require radically different approaches. You can't apply a textbook strategy without understanding the specific microstructure.

5. **Team leadership.** As team lead of 4 people, I coordinated data analysis, strategic decisions, and choices between competing versions under tight time constraints (48h per round).
