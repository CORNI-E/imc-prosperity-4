"""
IMC Prosperity 4 — Log Analysis Tools
======================================
Scripts used to analyze simulation logs and extract microstructure insights.

Key findings that shaped our strategy:
  1. Spike bots are informed → taking is negative EV
  2. Pepper Root has constant upward drift → trend capture opportunity
  3. Implied vol is flat at ~24% across all VEV strikes
"""

import json
import math
import statistics
from typing import Dict, List, Tuple


def parse_activities(log_path: str) -> Tuple[List[str], Dict]:
    """Parse a Prosperity log file into activity lines and metadata."""
    with open(log_path, 'r') as f:
        data = json.load(f)
    activities = data.get('activitiesLog', '').strip().split('\n')
    return activities[1:], data


def analyze_spreads(lines: List[str], product: str) -> Dict:
    """Compute bid-ask spread statistics for a product."""
    spreads = []
    for line in lines:
        parts = line.split(';')
        if len(parts) < 16 or parts[2] != product:
            continue
        b1 = float(parts[3]) if parts[3] else None
        a1 = float(parts[9]) if parts[9] else None
        if b1 and a1:
            spreads.append(a1 - b1)
    if not spreads:
        return {}
    return {
        'mean': statistics.mean(spreads),
        'median': statistics.median(spreads),
        'min': min(spreads),
        'max': max(spreads),
        'count': len(spreads),
    }


def analyze_price_drift(lines: List[str], product: str) -> Dict:
    """Detect directional price drift for a product."""
    mids = []
    for line in lines:
        parts = line.split(';')
        if len(parts) < 16 or parts[2] != product:
            continue
        mid = float(parts[15]) if parts[15] else 0
        if mid > 0:
            mids.append(mid)
    if len(mids) < 10:
        return {}
    n = len(mids)
    first_half = mids[n // 2] - mids[0]
    second_half = mids[-1] - mids[n // 2]
    total = mids[-1] - mids[0]
    return {
        'start': mids[0], 'mid': mids[n // 2], 'end': mids[-1],
        'first_half_drift': first_half,
        'second_half_drift': second_half,
        'total_drift': total,
        'is_trending': abs(total) > 20 and first_half * second_half > 0,
    }


def analyze_spike_profitability(lines: List[str], product: str,
                                 threshold: int = 5) -> Dict:
    """Test whether trading against spike bots is profitable.

    Key finding: spike bots are INFORMED. Average PnL per spike
    trade is -1.1, only 33% profitable. This explains why aggressive
    versions (V4-V6) lost money.
    """
    book = {}
    for line in lines:
        parts = line.split(';')
        if len(parts) < 16 or parts[2] != product:
            continue
        ts = int(parts[1])
        b1 = float(parts[3]) if parts[3] else None
        a1 = float(parts[9]) if parts[9] else None
        book[ts] = (b1, a1)

    sorted_ts = sorted(book.keys())
    outcomes = []

    for i in range(1, len(sorted_ts)):
        ts, prev_ts = sorted_ts[i], sorted_ts[i - 1]
        curr_bid, curr_ask = book[ts]
        prev_bid, prev_ask = book[prev_ts]
        if None in (curr_bid, curr_ask, prev_bid, prev_ask):
            continue

        is_bid_spike = curr_bid > prev_bid + threshold
        is_ask_spike = curr_ask < prev_ask - threshold

        if not (is_bid_spike or is_ask_spike):
            continue

        future_mids = []
        for j in range(1, 6):
            if i + j < len(sorted_ts):
                fb, fa = book[sorted_ts[i + j]]
                if fb and fa:
                    future_mids.append((fb + fa) / 2)
        if not future_mids:
            continue

        avg_future = statistics.mean(future_mids)
        if is_bid_spike:
            outcomes.append(curr_bid - avg_future)
        else:
            outcomes.append(avg_future - curr_ask)

    if not outcomes:
        return {}
    return {
        'total_spikes': len(outcomes),
        'mean_pnl': statistics.mean(outcomes),
        'pct_profitable': sum(1 for x in outcomes if x > 0) /
                          len(outcomes) * 100,
        'conclusion': 'INFORMED (negative EV)' if
                      statistics.mean(outcomes) < 0 else 'SAFE to trade',
    }


def implied_volatility(S: float, K: float, T: float,
                        market_price: float) -> float:
    """Binary search for Black-Scholes implied volatility."""
    def norm_cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def bs_call(sigma):
        if T <= 0 or sigma <= 0:
            return max(0, S - K)
        d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * norm_cdf(d1) - K * norm_cdf(d2)

    lo, hi = 0.01, 3.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if bs_call(mid) > market_price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def analyze_iv_surface(lines: List[str], ve_product: str,
                        strikes: Dict[str, int], tte_days: int) -> Dict:
    """Analyze implied volatility across strikes.

    Key finding: IV is FLAT at ~24% — no smile, no skew.
    The market uses Black-Scholes with a fixed sigma.
    """
    T = tte_days / 365.0
    by_ts = {}
    for line in lines:
        parts = line.split(';')
        if len(parts) < 16:
            continue
        ts = int(parts[1])
        if ts not in by_ts:
            by_ts[ts] = {}
        by_ts[ts][parts[2]] = float(parts[15]) if parts[15] else 0

    mid_ts = sorted(by_ts.keys())[len(by_ts) // 2]
    if ve_product not in by_ts[mid_ts]:
        return {}

    S = by_ts[mid_ts][ve_product]
    results = {}
    for name, K in strikes.items():
        if name in by_ts[mid_ts]:
            price = by_ts[mid_ts][name]
            if price > max(0, S - K) + 0.5:
                iv = implied_volatility(S, K, T, price)
                results[name] = {'price': price, 'iv_pct': f'{iv*100:.1f}%'}

    ivs = [implied_volatility(S, s['price'], T, s['price'])
           for s in results.values()] if results else []
    return {
        'spot': S, 'tte_days': tte_days, 'strikes': results,
        'is_flat': (max(ivs) - min(ivs) < 0.05) if ivs else False,
    }


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python log_analysis.py <log_file.json>")
        sys.exit(1)

    lines, data = parse_activities(sys.argv[1])
    print(f"Profit: {data.get('profit', 'N/A')}\n")

    products = sorted(set(p.split(';')[2] for p in lines
                          if len(p.split(';')) >= 3) - {'XIRECS'})

    for product in products:
        print(f"{'=' * 50}\n  {product}\n{'=' * 50}")
        sp = analyze_spreads(lines, product)
        if sp:
            print(f"  Spread: mean={sp['mean']:.1f}, median={sp['median']:.0f}")
        dr = analyze_price_drift(lines, product)
        if dr:
            print(f"  Drift: {dr['start']:.0f} -> {dr['end']:.0f} "
                  f"({dr['total_drift']:+.0f}), trending={dr['is_trending']}")
        sk = analyze_spike_profitability(lines, product)
        if sk:
            print(f"  Spikes: {sk['total_spikes']} found, "
                  f"mean PnL={sk['mean_pnl']:.2f}, "
                  f"{sk['pct_profitable']:.0f}% profitable")
            print(f"  -> {sk['conclusion']}")
        print()
