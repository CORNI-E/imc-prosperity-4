import json
from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict


class Trader:
    """
    V9 – V3 + Pepper Root Trend Capture
    =====================================
    Log analysis proved:
      - Osmium spikes are INFORMED bots → catching them LOSES money
      - V3's conservative MM is already optimal for Osmium
      - Pepper Root drifts +101 over 100K ticks → capturable trend
    
    V9 = V3 unchanged for Osmium
       + trend-following position bias for Pepper Root
    
    Pepper Root trend capture:
      - Track EMA slope (fast EMA - slow EMA)
      - If uptrend: shift adj_fair UP → buy more, sell less → accumulate long
      - If downtrend: shift adj_fair DOWN → sell more, buy less
      - The drift profit adds to MM profit
    """

    POSITION_LIMITS = {
        'INTARIAN_PEPPER_ROOT': 80,
        'ASH_COATED_OSMIUM': 80,
    }

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
        # NEW: slow EMA for trend detection on Pepper Root
        ema_slow = memory.get('ema_slow', {})

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            position = state.position.get(product, 0)
            limit = self.POSITION_LIMITS.get(product, 80)

            if not order_depth.sell_orders or not order_depth.buy_orders:
                result[product] = orders
                continue

            # ── Book analysis ──
            best_ask = min(order_depth.sell_orders.keys())
            best_bid = max(order_depth.buy_orders.keys())
            
            vol_ask = abs(order_depth.sell_orders[best_ask])
            vol_bid = order_depth.buy_orders[best_bid]

            total_vol = vol_bid + vol_ask
            if total_vol > 0:
                wmid = (best_bid * vol_ask + best_ask * vol_bid) / total_vol
            else:
                wmid = (best_bid + best_ask) / 2.0

            spread = best_ask - best_bid

            # ── EMA fair price (fast) ──
            alpha = 0.15
            if product not in ema:
                ema[product] = wmid
            else:
                ema[product] = wmid * alpha + ema[product] * (1 - alpha)
            fair = ema[product]

            # ── Slow EMA for trend detection ──
            alpha_slow_val = 0.02  # ~50 tick memory
            if product not in ema_slow:
                ema_slow[product] = wmid
            else:
                ema_slow[product] = wmid * alpha_slow_val + ema_slow[product] * (1 - alpha_slow_val)

            # ── Volatility EMA ──
            alpha_vol = 0.1
            if product in prev_mid:
                abs_change = abs(wmid - prev_mid[product])
                if product not in ema_vol:
                    ema_vol[product] = abs_change
                else:
                    ema_vol[product] = abs_change * alpha_vol + ema_vol[product] * (1 - alpha_vol)
            
            volatility = ema_vol.get(product, 1.0)
            prev_mid[product] = wmid

            # ── Market signals ──
            imbalance = (vol_bid - vol_ask) / total_vol if total_vol > 0 else 0
            inv_ratio = position / limit if limit > 0 else 0

            # ── Trend signal (Pepper Root only) ──
            # fast EMA - slow EMA: positive = uptrend, negative = downtrend
            trend_bias = 0.0
            if product == 'INTARIAN_PEPPER_ROOT':
                trend = fair - ema_slow[product]
                # Normalize: trend of +5 → strong uptrend
                # Clamp to [-1, +1]
                trend_norm = max(-1.0, min(1.0, trend / 5.0))
                # Bias adj_fair upward when trending up
                # This makes us buy more (adj_fair above mid → more buys fill)
                # and sell less (adj_fair above mid → fewer sells)
                trend_bias = trend_norm * 3.0  # up to ±3 ticks of bias

            # ── Adjusted fair price ──
            imbalance_weight = 1.0
            skew_weight = 3.0
            
            exp_skew = inv_ratio * (1 + 2 * inv_ratio * inv_ratio)
            
            adj_fair = fair + (imbalance * imbalance_weight) - (exp_skew * skew_weight) + trend_bias

            # ── Adaptive base spread ──
            base_spread = max(1.0, min(3.0, 0.8 + volatility * 0.5))

            # ──────────────────────────────────────────────────
            # LAYER 1: TAKING (same as V3)
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
            # LAYER 2: MM QUOTES (same as V3)
            # ──────────────────────────────────────────────────
            penny_bid = best_bid + 1
            penny_ask = best_ask - 1
            
            theo_bid = int(round(adj_fair - base_spread))
            theo_ask = int(round(adj_fair + base_spread))
            
            bid_price_1 = min(penny_bid, theo_bid)
            ask_price_1 = max(penny_ask, theo_ask)
            
            mid_int_floor = int(wmid)
            mid_int_ceil = mid_int_floor + (1 if wmid != int(wmid) else 0)
            bid_price_1 = min(bid_price_1, mid_int_floor)
            ask_price_1 = max(ask_price_1, mid_int_ceil if mid_int_ceil > mid_int_floor else mid_int_floor + 1)

            if bid_price_1 >= ask_price_1:
                bid_price_1 = mid_int_floor
                ask_price_1 = mid_int_floor + 1

            buy_size_1 = int(buy_budget * 0.6)
            sell_size_1 = int(sell_budget * 0.6)

            if buy_size_1 > 0:
                orders.append(Order(product, bid_price_1, buy_size_1))
                buy_budget -= buy_size_1

            if sell_size_1 > 0:
                orders.append(Order(product, ask_price_1, -sell_size_1))
                sell_budget -= sell_size_1

            # ──────────────────────────────────────────────────
            # LAYER 3: DEEPER QUOTES (same as V3)
            # ──────────────────────────────────────────────────
            bid_price_2 = bid_price_1 - 1
            ask_price_2 = ask_price_1 + 1

            if buy_budget > 0:
                orders.append(Order(product, bid_price_2, buy_budget))

            if sell_budget > 0:
                orders.append(Order(product, ask_price_2, -sell_budget))

            result[product] = orders

        # ── Save memory ──
        memory = {
            'ema': ema,
            'ema_vol': ema_vol,
            'prev_mid': prev_mid,
            'ema_slow': ema_slow,
        }
        trader_data = json.dumps(memory)

        return result, 0, trader_data
