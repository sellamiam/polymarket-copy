"""
Conservative depth-aware paper fill simulation for Polymarket CLOB.

Walks full order-book levels for the desired quantity. Does not treat the
whale's historical print as executable when the live book is unavailable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

CLOB_BASE = "https://clob.polymarket.com"


@dataclass
class BookLevel:
    price: float
    size: float  # shares available at this level


@dataclass
class FillResult:
    ok: bool
    side: str  # BUY or SELL
    requested_qty: float
    filled_qty: float
    avg_price: float
    worst_price: float
    notional: float
    fee_usdc: float
    levels_consumed: int
    partial: bool
    rejected_reason: Optional[str] = None
    book_top: Optional[float] = None
    book_depth_shares: float = 0.0
    levels: List[Dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def fetch_order_book(token_id: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Fetch full CLOB order book for token_id."""
    try:
        url = f"{CLOB_BASE}/book"
        res = requests.get(url, params={"token_id": token_id}, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"Error fetching order book for {token_id}: {e}")
    return None


def _parse_levels(raw_levels: Sequence[Any], ascending: bool) -> List[BookLevel]:
    levels: List[BookLevel] = []
    for item in raw_levels or []:
        try:
            if isinstance(item, dict):
                price = float(item.get("price"))
                size = float(item.get("size") or item.get("quantity") or 0)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price = float(item[0])
                size = float(item[1])
            else:
                continue
            if price > 0 and size > 0:
                levels.append(BookLevel(price=price, size=size))
        except (TypeError, ValueError):
            continue
    levels.sort(key=lambda L: L.price, reverse=not ascending)
    # For bids we want highest first (descending); asks lowest first (ascending)
    if ascending:
        levels.sort(key=lambda L: L.price)
    else:
        levels.sort(key=lambda L: L.price, reverse=True)
    return levels


def extract_asks(book: Dict[str, Any]) -> List[BookLevel]:
    return _parse_levels(book.get("asks") or book.get("sells") or [], ascending=True)


def extract_bids(book: Dict[str, Any]) -> List[BookLevel]:
    return _parse_levels(book.get("bids") or book.get("buys") or [], ascending=False)


def walk_book(
    levels: List[BookLevel],
    qty: float,
    *,
    side: str,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    tick_size: float = 0.01,
    min_order_size: float = 0.0,
    allow_partial: bool = True,
) -> FillResult:
    """
    Walk price levels until qty is filled or book is exhausted.

    BUY walks asks (ascending price); SELL walks bids (descending price).
    slippage_bps is applied adversely after the walk (worsens fill).
    fee_bps charged on notional.
    """
    side_u = side.upper()
    requested = float(qty)
    if requested <= 0:
        return FillResult(
            ok=False,
            side=side_u,
            requested_qty=requested,
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=0,
            partial=False,
            rejected_reason="non_positive_qty",
        )

    if min_order_size > 0 and requested < min_order_size:
        return FillResult(
            ok=False,
            side=side_u,
            requested_qty=requested,
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=0,
            partial=False,
            rejected_reason=f"below_min_order_size({min_order_size})",
            book_depth_shares=sum(L.size for L in levels),
        )

    if not levels:
        return FillResult(
            ok=False,
            side=side_u,
            requested_qty=requested,
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=0,
            partial=False,
            rejected_reason="empty_book",
        )

    remaining = requested
    filled = 0.0
    cost = 0.0
    worst = 0.0
    consumed = 0
    detail: List[Dict[str, float]] = []
    top = levels[0].price
    depth = sum(L.size for L in levels)

    for level in levels:
        if remaining <= 1e-12:
            break
        take = min(level.size, remaining)
        if take <= 0:
            continue
        # Snap price to tick adversely
        px = level.price
        if tick_size and tick_size > 0:
            if side_u == "BUY":
                px = math.ceil(px / tick_size - 1e-12) * tick_size
            else:
                px = math.floor(px / tick_size + 1e-12) * tick_size
            px = max(tick_size, min(1.0 - tick_size if tick_size < 1 else 1.0, px))

        filled += take
        cost += take * px
        worst = px
        remaining -= take
        consumed += 1
        detail.append({"price": px, "size": take})

    if filled <= 0:
        return FillResult(
            ok=False,
            side=side_u,
            requested_qty=requested,
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=0,
            partial=False,
            rejected_reason="no_liquidity",
            book_top=top,
            book_depth_shares=depth,
        )

    partial = filled + 1e-9 < requested
    if partial and not allow_partial:
        return FillResult(
            ok=False,
            side=side_u,
            requested_qty=requested,
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=consumed,
            partial=True,
            rejected_reason="insufficient_depth_no_partial",
            book_top=top,
            book_depth_shares=depth,
            levels=detail,
        )

    avg = cost / filled if filled else 0.0
    # Adverse slippage cushion on average price
    slip = float(slippage_bps) / 10000.0
    if side_u == "BUY":
        avg = avg * (1.0 + slip)
        worst = worst * (1.0 + slip)
    else:
        avg = avg * (1.0 - slip)
        worst = worst * (1.0 - slip)

    # Clamp to (0, 1]
    avg = max(1e-6, min(1.0, avg))
    worst = max(1e-6, min(1.0, worst))
    notional = filled * avg
    fee = notional * (float(fee_bps) / 10000.0)

    return FillResult(
        ok=True,
        side=side_u,
        requested_qty=requested,
        filled_qty=filled,
        avg_price=avg,
        worst_price=worst,
        notional=notional,
        fee_usdc=fee,
        levels_consumed=consumed,
        partial=partial,
        rejected_reason=None,
        book_top=top,
        book_depth_shares=depth,
        levels=detail,
    )


def simulate_buy_usdc(
    token_id: str,
    usdc_budget: float,
    *,
    book: Optional[Dict[str, Any]] = None,
    fee_bps: float = 0.0,
    slippage_bps: float = 25.0,
    tick_size: float = 0.01,
    min_order_size: float = 1.0,
    allow_partial: bool = True,
    fetch_timeout: float = 5.0,
) -> FillResult:
    """
    Buy as many shares as usdc_budget allows by walking the ask book.
    Two-pass: estimate qty from top, then walk; refine if needed.
    Never falls back to an external whale print.
    """
    if usdc_budget <= 0:
        return FillResult(
            ok=False,
            side="BUY",
            requested_qty=0.0,
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=0,
            partial=False,
            rejected_reason="non_positive_budget",
        )

    if book is None:
        book = fetch_order_book(token_id, timeout=fetch_timeout)
    if book is None:
        return FillResult(
            ok=False,
            side="BUY",
            requested_qty=0.0,
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=0,
            partial=False,
            rejected_reason="book_unavailable",
        )

    asks = extract_asks(book)
    if not asks:
        return FillResult(
            ok=False,
            side="BUY",
            requested_qty=0.0,
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=0,
            partial=False,
            rejected_reason="empty_asks",
            book_depth_shares=0.0,
        )

    # Walk spending budget across ask levels
    remaining_usdc = float(usdc_budget)
    filled = 0.0
    cost = 0.0
    worst = 0.0
    consumed = 0
    detail: List[Dict[str, float]] = []
    top = asks[0].price
    depth = sum(L.size for L in asks)
    fee_rate = float(fee_bps) / 10000.0
    slip = float(slippage_bps) / 10000.0

    for level in asks:
        if remaining_usdc <= 1e-9:
            break
        px = level.price
        if tick_size and tick_size > 0:
            px = math.ceil(px / tick_size - 1e-12) * tick_size
            px = max(tick_size, min(1.0, px))
        # Price after adverse cushion for budget planning
        px_adv = min(1.0, px * (1.0 + slip))
        # Include fee in effective cost per share
        eff = px_adv * (1.0 + fee_rate)
        if eff <= 0:
            continue
        max_shares_budget = remaining_usdc / eff
        take = min(level.size, max_shares_budget)
        if take < 1e-12:
            continue
        if min_order_size > 0 and filled == 0 and take < min_order_size and level.size >= min_order_size:
            # Can't afford min size at this level
            if remaining_usdc < min_order_size * eff:
                break
        leg_cost = take * px_adv
        leg_fee = leg_cost * fee_rate
        total_leg = leg_cost + leg_fee
        if total_leg > remaining_usdc + 1e-9:
            take = remaining_usdc / eff
            leg_cost = take * px_adv
            leg_fee = leg_cost * fee_rate
            total_leg = leg_cost + leg_fee
        filled += take
        cost += leg_cost
        worst = px_adv
        remaining_usdc -= total_leg
        consumed += 1
        detail.append({"price": px_adv, "size": take})

    if filled <= 0:
        return FillResult(
            ok=False,
            side="BUY",
            requested_qty=usdc_budget / max(top, 1e-6),
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=0,
            partial=False,
            rejected_reason="insufficient_liquidity_or_budget",
            book_top=top,
            book_depth_shares=depth,
        )

    if min_order_size > 0 and filled < min_order_size:
        return FillResult(
            ok=False,
            side="BUY",
            requested_qty=filled,
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=consumed,
            partial=True,
            rejected_reason=f"below_min_order_size({min_order_size})",
            book_top=top,
            book_depth_shares=depth,
            levels=detail,
        )

    avg = cost / filled if filled else 0.0
    fee_usdc = cost * fee_rate
    notional = cost
    # Partial relative to ideal top-of-book size
    ideal_qty = usdc_budget / max(top * (1.0 + slip) * (1.0 + fee_rate), 1e-9)
    partial = filled + 1e-6 < ideal_qty * 0.99

    return FillResult(
        ok=True,
        side="BUY",
        requested_qty=ideal_qty,
        filled_qty=filled,
        avg_price=avg,
        worst_price=worst,
        notional=notional,
        fee_usdc=fee_usdc,
        levels_consumed=consumed,
        partial=partial,
        book_top=top,
        book_depth_shares=depth,
        levels=detail,
    )


def simulate_sell_qty(
    token_id: str,
    quantity: float,
    *,
    book: Optional[Dict[str, Any]] = None,
    fee_bps: float = 0.0,
    slippage_bps: float = 25.0,
    tick_size: float = 0.01,
    min_order_size: float = 0.0,
    allow_partial: bool = True,
    fetch_timeout: float = 5.0,
) -> FillResult:
    """Sell `quantity` shares by walking the bid book. No whale-price fallback."""
    if book is None:
        book = fetch_order_book(token_id, timeout=fetch_timeout)
    if book is None:
        return FillResult(
            ok=False,
            side="SELL",
            requested_qty=float(quantity),
            filled_qty=0.0,
            avg_price=0.0,
            worst_price=0.0,
            notional=0.0,
            fee_usdc=0.0,
            levels_consumed=0,
            partial=False,
            rejected_reason="book_unavailable",
        )
    bids = extract_bids(book)
    return walk_book(
        bids,
        quantity,
        side="SELL",
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        tick_size=tick_size,
        min_order_size=min_order_size,
        allow_partial=allow_partial,
    )


def liquidation_bid_value(
    token_id: str,
    quantity: float,
    *,
    book: Optional[Dict[str, Any]] = None,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> Tuple[Optional[float], Optional[FillResult]]:
    """Executable liquidation value for open quantity at bid depth."""
    result = simulate_sell_qty(
        token_id,
        quantity,
        book=book,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        allow_partial=True,
    )
    if not result.ok:
        return None, result
    net = result.notional - result.fee_usdc
    return net, result
