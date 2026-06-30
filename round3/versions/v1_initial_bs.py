import json
import math
from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict


class Trader:
    """
    Round 3 – Prosperity 4 – "Gloves Off"
    =======================================
    3 asset classes:
      1. HYDROGEL_PACK: delta-1 MM, mean-reverting ~10000
      2. VELVETFRUIT_EXTRACT: delta-1 MM, mean-reverting ~5250
      3. VEV_XXXX (x10): call options on VE, BS-priced with sigma=0.24
    
    Key insight from data: implied vol is FLAT at ~24% across all strikes.
    → BS with sigma=0.24 gives accurate fair values for all VEVs.
    """

    POSITION_LIMITS = {
        'HYDROGEL_PACK': 200,
        'VELVETFRUIT_EXTRACT': 200,
    }
    # Each VEV has limit 300
    VEV_LIMIT = 300

    VEV_STRIKES = {
        'VEV_4000': 4000, 'VEV_4500': 4500,
        'VEV_5000': 5000, 'VEV_5100': 5100, 'VEV_5200': 5200,
        'VEV_5300': 5300, 'VEV_5400': 5400, 'VEV_5500': 5500,
        'VEV_6000': 6000, 'VEV_6500': 6500,
    }

    SIGMA = 0.24        # implied vol from data analysis
    TTE_DAYS = 5        # Round 3 = day 3, TTE = 7 - 3 + 1 = 5 days
    RISK_FREE = 0.0

    # ── Black-Scholes helpers ──
    @staticmethod
    def norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    @staticmethod
    def bs_call(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
        if T <= 0 or sigma <= 0:
            return max(0.0, S - K)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * Trader.norm_cdf(d1) - K * math.exp(-r * T) * Trader.norm_cdf(d2)

    @staticmethod
    def bs_delta(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
        if T <= 0 or sigma <= 0:
            return 1.0 if S > K else 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        return Trader.norm_cdf(d1)

    def run(self, state: TradingState) -> tuple:
        result = {}

        # ── Restore memory ──
        try:
            memory = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            memory = {}

        ema = memory.get('ema', {})
        ema_vol = memory.get('ema_vol', {})
        prev_mid = memory.get('prev_mid', {})

        # ── Get VE mid for options pricing ──
        ve_mid = None
        if 'VELVETFRUIT_EXTRACT' in state.order_depths:
            od = state.order_depths['VELVETFRUIT_EXTRACT']
            if od.sell_orders and od.buy_orders:
                ve_best_ask = min(od.sell_orders.keys())
                ve_best_bid = max(od.buy_orders.keys())
                ve_mid = (ve_best_bid + ve_best_ask) / 2.0

        # Use EMA of VE if available for smoother pricing
        if ve_mid is not None:
            alpha_ve = 0.15
            if 'VE_ema' not in memory:
                memory['VE_ema'] = ve_mid
            else:
                memory['VE_ema'] = ve_mid * alpha_ve + memory['VE_ema'] * (1 - alpha_ve)
            ve_fair = memory['VE_ema']
        else:
            ve_fair = memory.get('VE_ema', 5250.0)

        # TTE in years (decreases within the day: tick 0 = full day, tick 9999 = almost next day)
        T = self.TTE_DAYS / 365.0

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            position = state.position.get(product, 0)

            if not order_depth.sell_orders or not order_depth.buy_orders:
                result[product] = orders
                continue

            best_ask = min(order_depth.sell_orders.keys())
            best_bid = max(order_depth.buy_orders.keys())
            vol_ask = abs(order_depth.sell_orders[best_ask])
            vol_bid = order_depth.buy_orders[best_bid]
            total_vol = vol_bid + vol_ask
            wmid = (best_bid * vol_ask + best_ask * vol_bid) / total_vol if total_vol > 0 else (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid

            # ── Determine product type and fair value ──
            if product in self.VEV_STRIKES:
                # OPTIONS: price with BS
                K = self.VEV_STRIKES[product]
                limit = self.VEV_LIMIT

                if K <= 4500:
                    # Deep ITM: fair ≈ VE - strike (intrinsic value)
                    fair = max(0, ve_fair - K)
                else:
                    # ATM/OTM: use Black-Scholes
                    fair = self.bs_call(ve_fair, K, T, self.SIGMA, self.RISK_FREE)

                # For VEVs, use wider base spread for deep ITM, tighter for OTM
                if K <= 4500:
                    base_spread = 2.0  # deep ITM, wide natural spread
                elif K <= 5200:
                    base_spread = 1.0  # ATM
                else:
                    base_spread = 0.5  # OTM, tight natural spread

            else:
                # DELTA-1 products: V3-style MM
                limit = self.POSITION_LIMITS.get(product, 200)

                # EMA fair
                alpha = 0.15
                if product not in ema:
                    ema[product] = wmid
                else:
                    ema[product] = wmid * alpha + ema[product] * (1 - alpha)
                fair = ema[product]

                # Volatility EMA
                alpha_vol = 0.1
                if product in prev_mid:
                    abs_change = abs(wmid - prev_mid[product])
                    if product not in ema_vol:
                        ema_vol[product] = abs_change
                    else:
                        ema_vol[product] = abs_change * alpha_vol + ema_vol[product] * (1 - alpha_vol)

                volatility = ema_vol.get(product, 1.0)
                prev_mid[product] = wmid

                # Adaptive base spread
                base_spread = max(1.0, min(3.0, 0.8 + volatility * 0.5))

            # ── Market signals ──
            imbalance = (vol_bid - vol_ask) / total_vol if total_vol > 0 else 0
            inv_ratio = position / limit if limit > 0 else 0

            # Adjusted fair
            imbalance_weight = 1.0
            skew_weight = 3.0
            exp_skew = inv_ratio * (1 + 2 * inv_ratio * inv_ratio)
            adj_fair = fair + (imbalance * imbalance_weight) - (exp_skew * skew_weight)

            # ──────────────────────────────────────────────────
            # LAYER 1: TAKING
            # ──────────────────────────────────────────────────
            buy_budget = limit - position
            sell_budget = position + limit

            for ask_price in sorted(order_depth.sell_orders.keys()):
                if ask_price >= adj_fair:
                    break
                if buy_budget <= 0:
                    break
                ask_vol = abs(order_depth.sell_orders[ask_price])
                take_qty = min(ask_vol, buy_budget)
                if take_qty > 0:
                    orders.append(Order(product, ask_price, take_qty))
                    buy_budget -= take_qty

            for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                if bid_price <= adj_fair:
                    break
                if sell_budget <= 0:
                    break
                bid_vol = order_depth.buy_orders[bid_price]
                take_qty = min(bid_vol, sell_budget)
                if take_qty > 0:
                    orders.append(Order(product, bid_price, -take_qty))
                    sell_budget -= take_qty

            # ──────────────────────────────────────────────────
            # LAYER 2: MM QUOTES
            # ──────────────────────────────────────────────────
            penny_bid = best_bid + 1
            penny_ask = best_ask - 1

            theo_bid = int(round(adj_fair - base_spread))
            theo_ask = int(round(adj_fair + base_spread))

            bid_price_1 = min(penny_bid, theo_bid)
            ask_price_1 = max(penny_ask, theo_ask)

            # Don't cross mid
            mid_int_floor = int(wmid)
            mid_int_ceil = mid_int_floor + (1 if wmid != int(wmid) else 0)
            bid_price_1 = min(bid_price_1, mid_int_floor)
            ask_price_1 = max(ask_price_1, mid_int_ceil if mid_int_ceil > mid_int_floor else mid_int_floor + 1)

            if bid_price_1 >= ask_price_1:
                bid_price_1 = mid_int_floor
                ask_price_1 = mid_int_floor + 1

            # For options: ensure prices stay non-negative
            if product in self.VEV_STRIKES:
                bid_price_1 = max(0, bid_price_1)
                ask_price_1 = max(1, ask_price_1)

            buy_size_1 = int(buy_budget * 0.6)
            sell_size_1 = int(sell_budget * 0.6)

            if buy_size_1 > 0:
                orders.append(Order(product, bid_price_1, buy_size_1))
                buy_budget -= buy_size_1

            if sell_size_1 > 0:
                orders.append(Order(product, ask_price_1, -sell_size_1))
                sell_budget -= sell_size_1

            # ──────────────────────────────────────────────────
            # LAYER 3: DEEPER QUOTES
            # ──────────────────────────────────────────────────
            bid_price_2 = bid_price_1 - 1
            ask_price_2 = ask_price_1 + 1

            if product in self.VEV_STRIKES:
                bid_price_2 = max(0, bid_price_2)

            if buy_budget > 0:
                orders.append(Order(product, bid_price_2, buy_budget))

            if sell_budget > 0:
                orders.append(Order(product, ask_price_2, -sell_budget))

            result[product] = orders

        # ── Save memory ──
        memory['ema'] = ema
        memory['ema_vol'] = ema_vol
        memory['prev_mid'] = prev_mid
        trader_data = json.dumps(memory)

        return result, 0, trader_data
