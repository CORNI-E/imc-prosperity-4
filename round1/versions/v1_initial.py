import json
import math
import numpy as np
from typing import Any, List
from datamodel import Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState


class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]], conversions: int, trader_data: str) -> None:
        base_length = len(self.to_json([self.compress_state(state, ""), self.compress_orders(orders), conversions, "", ""]))
        max_item_length = (self.max_log_length - base_length) // 3
        print(self.to_json([
            self.compress_state(state, self.truncate(state.traderData, max_item_length)),
            self.compress_orders(orders), conversions,
            self.truncate(trader_data, max_item_length),
            self.truncate(self.logs, max_item_length),
        ]))
        self.logs = ""

    def compress_state(self, state: TradingState, trader_data: str) -> list[Any]:
        return [state.timestamp, trader_data, self.compress_listings(state.listings),
                self.compress_order_depths(state.order_depths), self.compress_trades(state.own_trades),
                self.compress_trades(state.market_trades), state.position, self.compress_observations(state.observations)]

    def compress_listings(self, listings: dict[Symbol, Listing]) -> list[list[Any]]:
        return [[l.symbol, l.product, l.denomination] for l in listings.values()]

    def compress_order_depths(self, order_depths: dict[Symbol, OrderDepth]) -> dict[Symbol, list[Any]]:
        return {s: [od.buy_orders, od.sell_orders] for s, od in order_depths.items()}

    def compress_trades(self, trades: dict[Symbol, list[Trade]]) -> list[list[Any]]:
        compressed = []
        for arr in trades.values():
            for t in arr:
                compressed.append([t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp])
        return compressed

    def compress_observations(self, observations: Observation) -> list[Any]:
        co = {}
        for product, obs in observations.conversionObservations.items():
            co[product] = [obs.bidPrice, obs.askPrice, obs.transportFees, obs.exportTariff,
                           obs.importTariff, obs.sugarPrice, obs.sunlightIndex]
        return [observations.plainValueObservations, co]

    def compress_orders(self, orders: dict[Symbol, list[Order]]) -> list[list[Any]]:
        compressed = []
        for arr in orders.values():
            for o in arr:
                compressed.append([o.symbol, o.price, o.quantity])
        return compressed

    def to_json(self, value: Any) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        return value if len(value) <= max_length else value[:max_length - 3] + "..."


logger = Logger()


class Trader:
    """
    Round 1 Strategy – Prosperity 3
    ================================
    Products:
      INTARIAN_PEPPER_ROOT (limit 80) – stable, like RAINFOREST_RESIN
        → Pure market making around dynamic mid-price + inventory skew
      ASH_COATED_OSMIUM (limit 80) – volatile with hidden pattern
        → Market making + z-score mean reversion + inventory skew

    Key principles:
      - ALL prices computed dynamically from the order book (zero hardcoded values)
      - State persisted via traderData JSON between ticks
      - Inventory skew to avoid large directional exposure
      - Aggressive taking of mispriced orders
      - Penny-the-spread quoting when spread is wide enough
    """

    LIMIT = 80  # Position limit for both products

    def __init__(self):
        self.orders = {}
        self.conversions = 0

        # Per-product tracking (reset each tick)
        self.buy_orders_sent = {}
        self.sell_orders_sent = {}
        self.position = {}

        # Osmium state (persisted via traderData)
        self.osmium_prices = []
        self.osmium_price_diffs = []

        # Hyperparams for Osmium mean reversion
        self.OSMIUM_WINDOW = 30
        self.OSMIUM_VOL_WINDOW = 50

    # =================================================================
    #  ORDER HELPERS
    # =================================================================
    def buy(self, product, price, qty, msg=""):
        if qty <= 0:
            return
        self.orders[product].append(Order(product, int(price), qty))
        self.buy_orders_sent[product] = self.buy_orders_sent.get(product, 0) + qty
        if msg:
            logger.print(msg)

    def sell(self, product, price, qty, msg=""):
        if qty <= 0:
            return
        self.orders[product].append(Order(product, int(price), -qty))
        self.sell_orders_sent[product] = self.sell_orders_sent.get(product, 0) + qty
        if msg:
            logger.print(msg)

    def max_buy(self, product):
        pos = self.position.get(product, 0)
        sent = self.buy_orders_sent.get(product, 0)
        return max(0, self.LIMIT - pos - sent)

    def max_sell(self, product):
        pos = self.position.get(product, 0)
        sent = self.sell_orders_sent.get(product, 0)
        return max(0, pos + self.LIMIT - sent)

    # =================================================================
    #  TAKE MISPRICED ORDERS
    # =================================================================
    def take_asks(self, state, product, max_price, depth=3):
        """Buy from sellers offering at or below max_price."""
        od = state.order_depths[product]
        for ask, neg_vol in sorted(od.sell_orders.items())[:depth]:
            if ask > max_price:
                break
            vol = -neg_vol
            qty = min(vol, self.max_buy(product))
            if qty > 0:
                self.buy(product, ask, qty, f"TAKE BUY {product} {qty}x@{ask}")

    def take_bids(self, state, product, min_price, depth=3):
        """Sell into buyers bidding at or above min_price."""
        od = state.order_depths[product]
        for bid, vol in sorted(od.buy_orders.items(), reverse=True)[:depth]:
            if bid < min_price:
                break
            qty = min(vol, self.max_sell(product))
            if qty > 0:
                self.sell(product, bid, qty, f"TAKE SELL {product} {qty}x@{bid}")

    # =================================================================
    #  INTARIAN_PEPPER_ROOT – Pure Market Making
    #  
    #  This product is "steady" – the value doesn't move much.
    #  Strategy: quote both sides around mid, take mispriced orders,
    #  use inventory skew to stay close to flat.
    # =================================================================
    def trade_pepper_root(self, state):
        product = 'INTARIAN_PEPPER_ROOT'
        if product not in state.order_depths:
            return

        od = state.order_depths[product]
        if not od.buy_orders or not od.sell_orders:
            return

        best_bid = max(od.buy_orders.keys())
        best_ask = min(od.sell_orders.keys())
        mid = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        pos = self.position.get(product, 0)

        logger.print(f"PEPPER: mid={mid:.1f} spread={spread} pos={pos}")

        # 1) Take anything mispriced
        self.take_asks(state, product, mid - 0.01, depth=3)
        self.take_bids(state, product, mid + 0.01, depth=3)

        # 2) Inventory skew: shift fair value to encourage unwinding
        skew = -pos * 0.05
        adj_mid = mid + skew

        # 3) Post MM quotes
        our_buy = int(math.floor(adj_mid)) - 1
        our_sell = int(math.ceil(adj_mid)) + 1

        # Penny the spread if it's wide enough
        if spread >= 4:
            our_buy = max(our_buy, best_bid + 1)
            our_sell = min(our_sell, best_ask - 1)

        # Safety: never cross the mid
        our_buy = min(our_buy, int(math.floor(mid)))
        our_sell = max(our_sell, int(math.ceil(mid)))

        remaining_buy = self.max_buy(product)
        remaining_sell = self.max_sell(product)

        if remaining_buy > 0:
            self.buy(product, our_buy, remaining_buy,
                     f"PEPPER MM BUY {remaining_buy}x@{our_buy}")
        if remaining_sell > 0:
            self.sell(product, our_sell, remaining_sell,
                      f"PEPPER MM SELL {remaining_sell}x@{our_sell}")

    # =================================================================
    #  ASH_COATED_OSMIUM – MM + Mean Reversion
    #
    #  This product is volatile with a "hidden pattern" (likely
    #  mean-reverting given negative autocorrelation observed in
    #  similar products). Strategy:
    #    - Compute z-score vs rolling mean
    #    - At extreme z-scores: take aggressively (mean reversion)
    #    - At normal z-scores: market make with inventory skew
    #    - Bias quotes toward mean reversion direction
    # =================================================================
    def trade_osmium(self, state):
        product = 'ASH_COATED_OSMIUM'
        if product not in state.order_depths:
            return

        od = state.order_depths[product]
        if not od.buy_orders or not od.sell_orders:
            return

        best_bid = max(od.buy_orders.keys())
        best_ask = min(od.sell_orders.keys())
        mid = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        pos = self.position.get(product, 0)

        # --- Update rolling history ---
        if len(self.osmium_prices) > 0:
            diff = mid - self.osmium_prices[-1]
            self.osmium_price_diffs.append(diff)
            self.osmium_price_diffs = self.osmium_price_diffs[-self.OSMIUM_VOL_WINDOW:]

        self.osmium_prices.append(mid)
        self.osmium_prices = self.osmium_prices[-max(self.OSMIUM_WINDOW, self.OSMIUM_VOL_WINDOW):]

        # --- Compute z-score ---
        z = 0.0
        mean_price = mid
        if len(self.osmium_prices) >= self.OSMIUM_WINDOW:
            window_prices = self.osmium_prices[-self.OSMIUM_WINDOW:]
            mean_price = float(np.mean(window_prices))
            std = float(np.std(window_prices))
            if std > 0.5:
                z = (mid - mean_price) / std

        # --- Compute volatility ---
        volatility = 0.0
        if len(self.osmium_price_diffs) >= 10:
            volatility = float(np.std(self.osmium_price_diffs))

        logger.print(f"OSMIUM: mid={mid:.1f} mean={mean_price:.1f} z={z:.2f} vol={volatility:.2f} pos={pos}")

        # --- 1) Take mispriced orders (always) ---
        self.take_asks(state, product, mid - 0.01, depth=3)
        self.take_bids(state, product, mid + 0.01, depth=3)

        # --- 2) Mean reversion: aggressive taking at extreme z-scores ---
        if z < -2.0:
            self.take_asks(state, product, mid + 1, depth=3)
            logger.print(f"OSMIUM: STRONG BUY z={z:.2f}")
        elif z > 2.0:
            self.take_bids(state, product, mid - 1, depth=3)
            logger.print(f"OSMIUM: STRONG SELL z={z:.2f}")

        # --- 3) Compute adjusted mid with inventory skew + z-score bias ---
        # Inventory skew: if long, lower fair → sell more
        inv_skew = -pos * 0.08
        # Z-score bias: if z < 0 (below mean), shift up → buy more  
        z_bias = -z * 0.5
        adj_mid = mid + inv_skew + z_bias

        # --- 4) Post MM quotes ---
        our_buy = int(math.floor(adj_mid)) - 1
        our_sell = int(math.ceil(adj_mid)) + 1

        if spread >= 4:
            our_buy = max(our_buy, best_bid + 1)
            our_sell = min(our_sell, best_ask - 1)

        # Safety: don't cross the mid
        our_buy = min(our_buy, int(math.floor(mid)))
        our_sell = max(our_sell, int(math.ceil(mid)))

        # --- 5) Position-dependent sizing ---
        pos_ratio = pos / self.LIMIT  # -1 to +1
        buy_mult = max(0.2, 1.0 - max(0, pos_ratio))
        sell_mult = max(0.2, 1.0 + min(0, pos_ratio))

        remaining_buy = int(self.max_buy(product) * buy_mult)
        remaining_sell = int(self.max_sell(product) * sell_mult)

        if remaining_buy > 0:
            self.buy(product, our_buy, remaining_buy,
                     f"OSMIUM MM BUY {remaining_buy}x@{our_buy}")
        if remaining_sell > 0:
            self.sell(product, our_sell, remaining_sell,
                      f"OSMIUM MM SELL {remaining_sell}x@{our_sell}")

    # =================================================================
    #  MAIN
    # =================================================================
    def reset(self, state):
        self.orders = {}
        self.conversions = 0
        self.buy_orders_sent = {}
        self.sell_orders_sent = {}
        self.position = dict(state.position) if state.position else {}
        for product in state.order_depths:
            self.orders[product] = []

    def run(self, state: TradingState):
        # Restore persisted state
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                self.osmium_prices = saved.get("op", [])
                self.osmium_price_diffs = saved.get("od", [])
            except Exception:
                pass

        self.reset(state)

        # Execute strategies
        self.trade_pepper_root(state)
        self.trade_osmium(state)

        # Persist state
        trader_data = json.dumps({
            "op": self.osmium_prices[-self.OSMIUM_VOL_WINDOW:],
            "od": self.osmium_price_diffs[-self.OSMIUM_VOL_WINDOW:],
        })

        logger.flush(state, self.orders, self.conversions, trader_data)
        return self.orders, self.conversions, trader_data
