from __future__ import annotations

import argparse
import csv
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
    parser.add_argument("--output-plan", type=Path, help="Optional CSV output path for the generated trade plan")
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


def compute_targets(positions: list[Position], extra_cash: float) -> list[dict[str, float | str]]:
    if extra_cash < 0:
        raise ValueError("cash cannot be negative.")

    total_current_value = sum(item.current_value for item in positions)
    total_target_value = total_current_value + extra_cash

    inv_vol = [1.0 / item.annual_volatility for item in positions]
    inv_vol_sum = sum(inv_vol)

    plan: list[dict[str, float | str]] = []
    for item, inv_component in zip(positions, inv_vol):
        target_weight = inv_component / inv_vol_sum
        target_value = total_target_value * target_weight
        value_delta = target_value - item.current_value
        share_delta = value_delta / item.price

        plan.append(
            {
                "symbol": item.symbol,
                "shares": item.shares,
                "price": item.price,
                "annual_volatility": item.annual_volatility,
                "current_value": item.current_value,
                "target_weight": target_weight,
                "target_value": target_value,
                "value_delta": value_delta,
                "share_delta": share_delta,
            }
        )

    return plan


def print_report(plan: list[dict[str, float | str]], extra_cash: float) -> None:
    total_current = sum(float(row["current_value"]) for row in plan)
    total_target = total_current + extra_cash
    gross_turnover = sum(abs(float(row["value_delta"])) for row in plan) / 2.0

    print("Portfolio Risk Rebalancer")
    print("=" * 28)
    print(f"Current portfolio value: ${total_current:,.2f}")
    print(f"Extra cash:             ${extra_cash:,.2f}")
    print(f"Target portfolio value: ${total_target:,.2f}")
    print(f"Estimated turnover:     ${gross_turnover:,.2f}")
    print()

    header = (
        f"{'Symbol':<8} {'CurValue':>12} {'TgtWeight':>10} {'TgtValue':>12} "
        f"{'Delta$':>12} {'DeltaSh':>12}"
    )
    print(header)
    print("-" * len(header))

    for row in sorted(plan, key=lambda item: str(item["symbol"])):
        print(
            f"{str(row['symbol']):<8} "
            f"{float(row['current_value']):>12.2f} "
            f"{float(row['target_weight']) * 100:>9.2f}% "
            f"{float(row['target_value']):>12.2f} "
            f"{float(row['value_delta']):>12.2f} "
            f"{float(row['share_delta']):>12.4f}"
        )


def write_plan_csv(plan: list[dict[str, float | str]], output_path: Path) -> None:
    fieldnames = [
        "symbol",
        "shares",
        "price",
        "annual_volatility",
        "current_value",
        "target_weight",
        "target_value",
        "value_delta",
        "share_delta",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in plan:
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    positions = read_positions(args.input)
    plan = compute_targets(positions, extra_cash=args.cash)
    print_report(plan, extra_cash=args.cash)

    if args.output_plan:
        write_plan_csv(plan, args.output_plan)
        print()
        print(f"Wrote trade plan: {args.output_plan}")


if __name__ == "__main__":
    main()
