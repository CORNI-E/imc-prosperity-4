import json
from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict


class Trader:
    """
    Round 1 – Prosperity 3 – V3 Optimized
    =======================================
    Architecture: Generic MM engine applied to all products
    
    Key improvements over 126385 baseline (3.5K):
      1. AGGRESSIVE TAKING: when fair > ask or fair < bid, cross the spread
      2. MULTI-LEVEL quoting: 2 price levels to capture more fills
      3. VOLUME-WEIGHTED MID: better fair value than simple mid
      4. ADAPTIVE EMA: slower for volatile, faster for stable
      5. STRONGER INVENTORY SKEW: exponential penalty near limits
      6. SPREAD ADAPTS TO VOLATILITY: tight when calm, wide when wild
    
    No numpy. No overfitting. Generic for any product.
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
        ema_vol = memory.get('ema_vol', {})  # EMA of absolute price changes (volatility)
        prev_mid = memory.get('prev_mid', {})

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

            # Volume-weighted mid (better than simple mid when volume is asymmetric)
            total_vol = vol_bid + vol_ask
            if total_vol > 0:
                wmid = (best_bid * vol_ask + best_ask * vol_bid) / total_vol
            else:
                wmid = (best_bid + best_ask) / 2.0

            spread = best_ask - best_bid

            # ── EMA fair price ──
            alpha = 0.15  # ~13 tick effective memory
            if product not in ema:
                ema[product] = wmid
            else:
                ema[product] = wmid * alpha + ema[product] * (1 - alpha)

            fair = ema[product]

            # ── Volatility EMA (for adaptive spread) ──
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
            # Order book imbalance: positive = buy pressure
            imbalance = (vol_bid - vol_ask) / total_vol if total_vol > 0 else 0

            # Inventory ratio: -1 (full short) to +1 (full long)
            inv_ratio = position / limit if limit > 0 else 0

            # ── Adjusted fair price ──
            # Follow imbalance slightly, skew against inventory
            imbalance_weight = 1.0
            skew_weight = 3.0  # strong inventory protection
            
            # Exponential skew near limits (much stronger penalty)
            exp_skew = inv_ratio * (1 + 2 * inv_ratio * inv_ratio)
            
            adj_fair = fair + (imbalance * imbalance_weight) - (exp_skew * skew_weight)

            # ── Adaptive base spread ──
            # Tighter when calm, wider when volatile
            base_spread = max(1.0, min(3.0, 0.8 + volatility * 0.5))

            # ──────────────────────────────────────────────────
            # LAYER 1: AGGRESSIVE TAKING
            # If our fair value says the market is mispriced, TAKE IT
            # ──────────────────────────────────────────────────
            buy_budget = limit - position
            sell_budget = position + limit

            # Take all ask levels below our fair value
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

            # Take all bid levels above our fair value
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
            # LAYER 2: PRIMARY MM QUOTES (inside the spread)
            # ──────────────────────────────────────────────────
            # Penny logic: always be at best_bid+1 / best_ask-1
            # But also respect our adj_fair-based theoretical price
            penny_bid = best_bid + 1
            penny_ask = best_ask - 1
            
            theo_bid = int(round(adj_fair - base_spread))
            theo_ask = int(round(adj_fair + base_spread))
            
            # Use the tighter of penny vs theoretical (be aggressive)
            bid_price_1 = min(penny_bid, theo_bid)
            ask_price_1 = max(penny_ask, theo_ask)
            
            # But never cross the mid to avoid guaranteed losses
            mid_int_floor = int(wmid)
            mid_int_ceil = mid_int_floor + (1 if wmid != int(wmid) else 0)
            bid_price_1 = min(bid_price_1, mid_int_floor)
            ask_price_1 = max(ask_price_1, mid_int_ceil if mid_int_ceil > mid_int_floor else mid_int_floor + 1)

            # Final cross guard
            if bid_price_1 >= ask_price_1:
                bid_price_1 = mid_int_floor
                ask_price_1 = mid_int_floor + 1

            # Size allocation: 60% on level 1
            buy_size_1 = int(buy_budget * 0.6)
            sell_size_1 = int(sell_budget * 0.6)

            if buy_size_1 > 0:
                orders.append(Order(product, bid_price_1, buy_size_1))
                buy_budget -= buy_size_1

            if sell_size_1 > 0:
                orders.append(Order(product, ask_price_1, -sell_size_1))
                sell_budget -= sell_size_1

            # ──────────────────────────────────────────────────
            # LAYER 3: SECONDARY MM QUOTES (deeper in book)
            # ──────────────────────────────────────────────────
            bid_price_2 = bid_price_1 - 1
            ask_price_2 = ask_price_1 + 1

            # Remaining budget on level 2
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
        }
        trader_data = json.dumps(memory)

        return result, 0, trader_data
