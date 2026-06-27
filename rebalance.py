from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Position:
    symbol: str
    shares: float
    price: float
    annual_volatility: float

    @property
    def current_value(self) -> float:
        return self.shares * self.price


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an inverse-volatility portfolio rebalance plan.")
    parser.add_argument("--input", type=Path, required=True, help="CSV with symbol, shares, price, annual_volatility")
    parser.add_argument("--cash", type=float, default=0.0, help="Optional additional cash to allocate")
    parser.add_argument(
        "--min-trade-value",
        type=float,
        default=0.0,
        help="Suppress trade recommendations smaller than this dollar threshold and report the residual drift",
    )
    parser.add_argument(
        "--concentration-threshold",
        type=float,
        default=0.35,
        help="Warn when current or target portfolio weight exceeds this fraction.",
    )
    parser.add_argument(
        "--max-target-weight",
        type=float,
        help="Optional hard cap on any target portfolio weight as a fraction, with the remainder redistributed.",
    )
    parser.add_argument(
        "--max-trade-notional",
        type=float,
        help="Optional cap on total absolute trade dollars; the plan will scale toward the target until it fits this budget.",
    )
    parser.add_argument(
        "--share-rounding",
        choices=("fractional", "half", "whole"),
        default="fractional",
        help="Round actionable share deltas to brokerage-friendly increments.",
    )
    parser.add_argument("--output-plan", type=Path, help="Optional CSV output path for the generated trade plan")
    parser.add_argument("--summary-output", type=Path, help="Optional JSON path for high-level rebalance totals")
    return parser.parse_args()


def read_positions(csv_path: Path) -> list[Position]:
    rows: list[Position] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "shares", "price", "annual_volatility"}
        missing = required.difference(set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

        for index, row in enumerate(reader, start=2):
            try:
                symbol = str(row["symbol"]).strip().upper()
                shares = float(row["shares"])
                price = float(row["price"])
                annual_volatility = float(row["annual_volatility"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid numeric value at row {index}.") from exc

            if not symbol:
                raise ValueError(f"Empty symbol at row {index}.")
            if price <= 0:
                raise ValueError(f"Price must be > 0 at row {index}.")
            if annual_volatility <= 0:
                raise ValueError(f"annual_volatility must be > 0 at row {index}.")

            rows.append(Position(symbol=symbol, shares=shares, price=price, annual_volatility=annual_volatility))

    if not rows:
        raise ValueError("Input CSV is empty.")
    return rows


def compute_targets(
    positions: list[Position],
    extra_cash: float,
    min_trade_value: float = 0.0,
    max_target_weight: float | None = None,
    max_trade_notional: float | None = None,
    share_rounding: str = "fractional",
) -> list[dict[str, float | str]]:
    if extra_cash < 0:
        raise ValueError("cash cannot be negative.")
    if min_trade_value < 0:
        raise ValueError("min-trade-value cannot be negative.")
    if max_target_weight is not None and not 0 < max_target_weight <= 1:
        raise ValueError("max-target-weight must be between 0 and 1.")
    if max_trade_notional is not None and max_trade_notional < 0:
        raise ValueError("max-trade-notional cannot be negative.")
    if share_rounding not in {"fractional", "half", "whole"}:
        raise ValueError("share-rounding must be fractional, half, or whole.")

    total_current_value = sum(item.current_value for item in positions)
    total_target_value = total_current_value + extra_cash

    inv_vol = [1.0 / item.annual_volatility for item in positions]
    inv_vol_sum = sum(inv_vol)
    raw_target_weights = [component / inv_vol_sum for component in inv_vol]
    target_weights = raw_target_weights

    if max_target_weight is not None:
        if max_target_weight * len(positions) < 1 - 1e-9:
            raise ValueError("max-target-weight is too small to allocate the full portfolio across the input positions.")

        target_weights = [0.0 for _ in positions]
        uncapped = set(range(len(positions)))
        remaining_weight = 1.0

        while uncapped:
            inv_pool = sum(inv_vol[index] for index in uncapped)
            if inv_pool <= 0:
                equal_share = remaining_weight / len(uncapped)
                for index in uncapped:
                    target_weights[index] = equal_share
                break

            capped_this_round = []
            for index in list(uncapped):
                proposed = remaining_weight * (inv_vol[index] / inv_pool)
                if proposed > max_target_weight + 1e-9:
                    target_weights[index] = max_target_weight
                    remaining_weight -= max_target_weight
                    capped_this_round.append(index)

            if not capped_this_round:
                for index in uncapped:
                    target_weights[index] = remaining_weight * (inv_vol[index] / inv_pool)
                break

            for index in capped_this_round:
                uncapped.remove(index)

    pre_budget_rows: list[dict[str, float | str]] = []
    for item, raw_target_weight, target_weight in zip(positions, raw_target_weights, target_weights):
        target_value = total_target_value * target_weight
        value_delta = target_value - item.current_value
        share_delta = value_delta / item.price

        pre_budget_rows.append(
            {
                "symbol": item.symbol,
                "shares": item.shares,
                "price": item.price,
                "annual_volatility": item.annual_volatility,
                "current_value": item.current_value,
                "current_weight": item.current_value / total_current_value if total_current_value else 0.0,
                "raw_target_weight": raw_target_weight,
                "target_weight": target_weight,
                "target_value": target_value,
                "value_delta": value_delta,
                "share_delta": share_delta,
                "cap_applied": "yes" if max_target_weight is not None and target_weight + 1e-9 < raw_target_weight else "no",
            }
        )

    raw_trade_notional = sum(abs(float(row["value_delta"])) for row in pre_budget_rows)
    trade_budget_scale = 1.0
    if max_trade_notional is not None and raw_trade_notional > 0 and raw_trade_notional > max_trade_notional:
        trade_budget_scale = max_trade_notional / raw_trade_notional

    deployed_cash = extra_cash * trade_budget_scale
    recommended_total_value = total_current_value + deployed_cash
    plan: list[dict[str, float | str]] = []
    for row in pre_budget_rows:
        budgeted_value_delta = float(row["value_delta"]) * trade_budget_scale
        budgeted_share_delta = budgeted_value_delta / float(row["price"])
        if abs(budgeted_value_delta) < min_trade_value:
            actionable_share_delta = 0.0
        else:
            actionable_share_delta = round_share_delta(budgeted_share_delta, share_rounding)
        actionable_value_delta = actionable_share_delta * float(row["price"])
        recommended_target_value = float(row["current_value"]) + budgeted_value_delta
        recommended_target_weight = (
            recommended_target_value / recommended_total_value if recommended_total_value else 0.0
        )
        rounded_target_value = float(row["current_value"]) + actionable_value_delta
        rounded_target_weight = rounded_target_value / recommended_total_value if recommended_total_value else 0.0

        plan.append(
            {
                **row,
                "recommended_target_value": recommended_target_value,
                "recommended_target_weight": recommended_target_weight,
                "rounded_target_value": rounded_target_value,
                "rounded_target_weight": rounded_target_weight,
                "budgeted_value_delta": budgeted_value_delta,
                "budgeted_share_delta": budgeted_share_delta,
                "actionable_value_delta": actionable_value_delta,
                "actionable_share_delta": actionable_share_delta,
                "trade_action": "BUY" if actionable_value_delta > 0 else "SELL" if actionable_value_delta < 0 else "HOLD",
                "trade_budget_scale": trade_budget_scale,
                "unused_cash": extra_cash - deployed_cash,
                "weight_drift_pct": (recommended_target_weight - float(row["current_weight"])) * 100.0,
                "rounded_weight_drift_pct": (rounded_target_weight - float(row["current_weight"])) * 100.0,
                "rounding_mode": share_rounding,
                "rounding_value_slippage": actionable_value_delta - budgeted_value_delta,
            }
        )

    return plan


def round_share_delta(share_delta: float, mode: str) -> float:
    if mode == "fractional":
        return share_delta

    step = 0.5 if mode == "half" else 1.0
    rounded = round(abs(share_delta) / step) * step
    return rounded if share_delta >= 0 else -rounded


def print_report(
    plan: list[dict[str, float | str]],
    extra_cash: float,
    min_trade_value: float,
    concentration_threshold: float,
    max_target_weight: float | None,
    max_trade_notional: float | None,
    share_rounding: str,
) -> None:
    total_current = sum(float(row["current_value"]) for row in plan)
    deployed_cash = extra_cash * (float(plan[0]["trade_budget_scale"]) if plan else 1.0)
    total_target = total_current + deployed_cash
    trade_notional = sum(abs(float(row["actionable_value_delta"])) for row in plan)
    suppressed_value = sum(
        abs(float(row["budgeted_value_delta"]) - float(row["actionable_value_delta"])) for row in plan
    )
    rounding_slippage = sum(float(row["rounding_value_slippage"]) for row in plan)
    total_buys = sum(max(float(row["actionable_value_delta"]), 0.0) for row in plan)
    total_sells = sum(max(-float(row["actionable_value_delta"]), 0.0) for row in plan)

    print("Portfolio Risk Rebalancer")
    print("=" * 28)
    print(f"Current portfolio value: ${total_current:,.2f}")
    print(f"Extra cash:             ${extra_cash:,.2f}")
    print(f"Deployed cash:          ${deployed_cash:,.2f}")
    print(f"Recommended value:      ${total_target:,.2f}")
    print(f"Trade notional:         ${trade_notional:,.2f}")
    print(f"Buy flow:               ${total_buys:,.2f}")
    print(f"Sell flow:              ${total_sells:,.2f}")
    print(f"Share rounding:         {share_rounding}")
    if min_trade_value > 0:
        print(f"Trade threshold:        ${min_trade_value:,.2f}")
        print(f"Suppressed drift:       ${suppressed_value:,.2f}")
    if share_rounding != "fractional":
        print(f"Rounding slippage:      ${rounding_slippage:,.2f}")
    print(f"Concentration watch:    {concentration_threshold * 100:.1f}%")
    if max_target_weight is not None:
        print(f"Target weight cap:      {max_target_weight * 100:.1f}%")
    if max_trade_notional is not None:
        scale = float(plan[0]["trade_budget_scale"]) if plan else 1.0
        print(f"Trade budget:           ${max_trade_notional:,.2f}")
        print(f"Budget scale applied:   {scale * 100:.1f}%")
        print(f"Unused cash:            ${extra_cash - deployed_cash:,.2f}")
    print()

    header = (
        f"{'Symbol':<8} {'Action':<6} {'CurWgt':>8} {'TgtWgt':>8} {'Cap':>5} {'Drift':>9} {'RecValue':>12} "
        f"{'Action$':>12} {'ActionSh':>12}"
    )
    print(header)
    print("-" * len(header))

    for row in sorted(plan, key=lambda item: str(item["symbol"])):
        print(
            f"{str(row['symbol']):<8} "
            f"{str(row['trade_action']):<6} "
            f"{float(row['current_weight']) * 100:>7.2f}% "
            f"{float(row['rounded_target_weight']) * 100:>9.2f}% "
            f"{str(row['cap_applied']):>5} "
            f"{float(row['rounded_weight_drift_pct']):>8.2f}% "
            f"{float(row['rounded_target_value']):>12.2f} "
            f"{float(row['actionable_value_delta']):>12.2f} "
            f"{float(row['actionable_share_delta']):>12.4f}"
        )

    concentration_rows = [
        row
        for row in plan
        if float(row["current_weight"]) >= concentration_threshold or float(row["recommended_target_weight"]) >= concentration_threshold
    ]
    if concentration_rows:
        print("\nConcentration watch:")
        for row in sorted(concentration_rows, key=lambda item: float(item["recommended_target_weight"]), reverse=True):
            print(
                f"  {row['symbol']}: current {float(row['current_weight']) * 100:.2f}% -> "
                f"target {float(row['rounded_target_weight']) * 100:.2f}%"
            )


def write_plan_csv(plan: list[dict[str, float | str]], output_path: Path) -> None:
    fieldnames = [
        "symbol",
        "shares",
        "price",
        "annual_volatility",
        "current_value",
        "current_weight",
        "raw_target_weight",
        "target_weight",
        "target_value",
        "recommended_target_weight",
        "recommended_target_value",
        "rounded_target_weight",
        "rounded_target_value",
        "value_delta",
        "share_delta",
        "budgeted_value_delta",
        "budgeted_share_delta",
        "actionable_value_delta",
        "actionable_share_delta",
        "trade_action",
        "cap_applied",
        "trade_budget_scale",
        "unused_cash",
        "weight_drift_pct",
        "rounded_weight_drift_pct",
        "rounding_mode",
        "rounding_value_slippage",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in plan:
            writer.writerow(row)


def write_summary_json(
    plan: list[dict[str, float | str]],
    output_path: Path,
    *,
    extra_cash: float,
    concentration_threshold: float,
    max_target_weight: float | None,
    max_trade_notional: float | None,
) -> None:
    total_current = sum(float(row["current_value"]) for row in plan)
    total_target = total_current + extra_cash
    concentration_watch = [
        {
            "symbol": str(row["symbol"]),
            "current_weight": round(float(row["current_weight"]), 4),
            "target_weight": round(float(row["rounded_target_weight"]), 4),
        }
        for row in plan
        if float(row["current_weight"]) >= concentration_threshold or float(row["rounded_target_weight"]) >= concentration_threshold
    ]
    payload = {
        "current_portfolio_value": round(total_current, 2),
        "recommended_portfolio_value": round(sum(float(row["recommended_target_value"]) for row in plan), 2),
        "extra_cash": round(extra_cash, 2),
        "deployed_cash": round(extra_cash * (float(plan[0]["trade_budget_scale"]) if plan else 1.0), 2),
        "trade_notional": round(sum(abs(float(row["actionable_value_delta"])) for row in plan), 2),
        "rounding_mode": str(plan[0]["rounding_mode"]) if plan else "fractional",
        "rounding_value_slippage": round(sum(float(row["rounding_value_slippage"]) for row in plan), 2),
        "buy_flow": round(sum(max(float(row["actionable_value_delta"]), 0.0) for row in plan), 2),
        "sell_flow": round(sum(max(-float(row["actionable_value_delta"]), 0.0) for row in plan), 2),
        "concentration_threshold": concentration_threshold,
        "max_target_weight": round(max_target_weight, 4) if max_target_weight is not None else None,
        "max_trade_notional": round(max_trade_notional, 2) if max_trade_notional is not None else None,
        "trade_budget_scale": round(float(plan[0]["trade_budget_scale"]) if plan else 1.0, 4),
        "unused_cash": round(float(plan[0]["unused_cash"]) if plan else 0.0, 2),
        "concentration_watch": concentration_watch,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    positions = read_positions(args.input)
    plan = compute_targets(
        positions,
        extra_cash=args.cash,
        min_trade_value=args.min_trade_value,
        max_target_weight=args.max_target_weight,
        max_trade_notional=args.max_trade_notional,
        share_rounding=args.share_rounding,
    )
    print_report(
        plan,
        extra_cash=args.cash,
        min_trade_value=args.min_trade_value,
        concentration_threshold=args.concentration_threshold,
        max_target_weight=args.max_target_weight,
        max_trade_notional=args.max_trade_notional,
        share_rounding=args.share_rounding,
    )

    if args.output_plan:
        write_plan_csv(plan, args.output_plan)
        print()
        print(f"Wrote trade plan: {args.output_plan}")

    if args.summary_output:
        write_summary_json(
            plan,
            args.summary_output,
            extra_cash=args.cash,
            concentration_threshold=args.concentration_threshold,
            max_target_weight=args.max_target_weight,
            max_trade_notional=args.max_trade_notional,
        )
        print(f"Wrote rebalance summary: {args.summary_output}")


if __name__ == "__main__":
    main()
