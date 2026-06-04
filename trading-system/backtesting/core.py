from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
from requests import RequestException

try:
    from config import DATA_FAPI
except Exception:
    DATA_FAPI = "https://fapi.binance.com"


TZ_UTC8 = timezone(timedelta(hours=8))
MS_HOUR = 60 * 60 * 1000


def interval_to_ms(interval: str) -> int:
    """
    将K线周期转换为毫秒
    支持: 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 1w, 1M
    """
    unit = interval[-1]
    value = int(interval[:-1]) if len(interval) > 1 else 1

    units_ms = {
        "m": 60 * 1000,
        "h": 60 * 60 * 1000,
        "d": 24 * 60 * 60 * 1000,
        "w": 7 * 24 * 60 * 60 * 1000,
        "M": 30 * 24 * 60 * 60 * 1000,
    }
    if unit not in units_ms:
        raise ValueError(f"Unsupported interval: {interval}")
    return value * units_ms[unit]


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    symbol: str
    direction: str
    signal_type: str
    strength: str
    price: float
    sl_pct: float
    tp_pct: float
    score: float
    reason: str
    stats: dict[str, Any]


@dataclass
class Position:
    id: int
    symbol: str
    direction: str
    entry_time: int
    entry_price: float
    margin_usd: float
    notional_usd: float
    leverage: float
    sl_pct: float
    tp_pct: float
    stop_loss: float
    take_profit: float
    signal_type: str
    reason: str
    score: float


class BinanceFuturesDataProvider:
    """Production futures public-data adapter with a tiny JSON cache."""

    def __init__(self, base_url: str = DATA_FAPI, cache_dir: Path | None = None, sleep_s: float = 0.05):
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir or Path(__file__).resolve().parents[1] / "data" / "backtest_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.sleep_s = sleep_s

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{endpoint}"
        last_error: RequestException | None = None
        for attempt in range(5):
            try:
                resp = requests.get(url, params=params or {}, timeout=20)
                if resp.status_code == 429:
                    time.sleep(2 + attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except RequestException as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(
            f"Binance public data request failed: {url}; "
            f"check network/proxy/firewall or set BINANCE_DATA_FAPI. "
            f"Original error: {last_error}"
        )

    def list_symbols(self, limit: int, min_quote_volume: float, exclude: set[str]) -> list[str]:
        data = self._get("/fapi/v1/ticker/24hr")
        rows = []
        for item in data:
            symbol = item.get("symbol", "")
            if not symbol.endswith("USDT") or symbol in exclude:
                continue
            quote_volume = float(item.get("quoteVolume", 0) or 0)
            last_price = float(item.get("lastPrice", 0) or 0)
            if quote_volume >= min_quote_volume and last_price > 0:
                rows.append((symbol, quote_volume))
        rows.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in rows[:limit]]

    def klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
        cache = self.cache_dir / f"klines_{symbol}_{interval}_{start_ms}_{end_ms}.json"
        if cache.exists():
            return [Candle(**row) for row in json.loads(cache.read_text())]

        rows: list[list[Any]] = []
        cur = start_ms
        while cur < end_ms:
            data = self._get("/fapi/v1/klines", {
                "symbol": symbol,
                "interval": interval,
                "startTime": cur,
                "endTime": end_ms,
                "limit": 1500,
            })
            if not data:
                break
            rows.extend(data)
            cur = int(data[-1][0]) + 1
            if len(data) < 1500:
                break
            time.sleep(self.sleep_s)

        candles = [
            Candle(
                time=int(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[7]),
            )
            for k in rows
        ]
        cache.write_text(json.dumps([c.__dict__ for c in candles], ensure_ascii=False))
        return candles

    def funding(self, symbol: str, start_ms: int, end_ms: int) -> list[dict[str, float]]:
        cache = self.cache_dir / f"funding_{symbol}_{start_ms}_{end_ms}.json"
        if cache.exists():
            return json.loads(cache.read_text())

        rows = []
        cur = start_ms
        while cur < end_ms:
            data = self._get("/fapi/v1/fundingRate", {
                "symbol": symbol,
                "startTime": cur,
                "endTime": end_ms,
                "limit": 1000,
            })
            if not data:
                break
            rows.extend(data)
            cur = int(data[-1]["fundingTime"]) + 1
            if len(data) < 1000:
                break
            time.sleep(self.sleep_s)
        funding = [{"time": int(r["fundingTime"]), "rate": float(r["fundingRate"]) * 100} for r in rows]
        cache.write_text(json.dumps(funding, ensure_ascii=False))
        return funding


class SampleDataProvider:
    """Deterministic local data for fast smoke tests and UI demos."""

    def list_symbols(self, limit: int, min_quote_volume: float, exclude: set[str]) -> list[str]:
        base = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "XRPUSDT"]
        return [s for s in base if s not in exclude][:limit]

    def klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
        seed = sum(ord(ch) for ch in symbol)
        rng = random.Random(seed)
        price = 100 + seed % 200
        candles = []
        for ts in range(start_ms, end_ms + 1, MS_HOUR):
            drift = math.sin((ts // MS_HOUR + seed) / 31) * 0.006
            shock = rng.uniform(-0.018, 0.018)
            open_price = price
            close = max(0.01, price * (1 + drift + shock))
            high = max(open_price, close) * (1 + rng.uniform(0.001, 0.012))
            low = min(open_price, close) * (1 - rng.uniform(0.001, 0.012))
            volume = 60_000_000 + rng.random() * 300_000_000
            candles.append(Candle(ts, open_price, high, low, close, volume))
            price = close
        return candles

    def funding(self, symbol: str, start_ms: int, end_ms: int) -> list[dict[str, float]]:
        seed = sum(ord(ch) for ch in symbol)
        rows = []
        for ts in range(start_ms, end_ms + 1, 8 * MS_HOUR):
            rate = math.sin((ts // MS_HOUR + seed) / 17) * 0.13
            rows.append({"time": ts, "rate": rate})
        return rows


def load_strategy(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    data.setdefault("name", path.stem)
    data.setdefault("version", "custom")
    data.setdefault("market", {})
    data.setdefault("risk", {})
    data.setdefault("signals", {})
    data.setdefault("simulation", {})
    return data


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = value * k + current * (1 - k)
    return current


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def atr_pct(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        current = candles[i]
        prev = candles[i - 1]
        tr = max(
            current.high - current.low,
            abs(current.high - prev.close),
            abs(current.low - prev.close),
        )
        trs.append(tr)
    atr = sum(trs[-period:]) / period
    close = candles[-1].close
    return atr / close if close > 0 else 0.0


def technical_snapshot(candles: list[Candle], tech_cfg: dict[str, Any]) -> dict[str, Any]:
    closes = [c.close for c in candles]
    fast = ema(closes, int(tech_cfg.get("ema_fast", 9)))
    slow = ema(closes, int(tech_cfg.get("ema_slow", 21)))
    trend = "neutral"
    if fast and slow:
        band = float(tech_cfg.get("ema_band", 0.001))
        if fast > slow * (1 + band):
            trend = "up"
        elif fast < slow * (1 - band):
            trend = "down"
    return {
        "ema_fast": fast,
        "ema_slow": slow,
        "trend": trend,
        "rsi": rsi(closes, int(tech_cfg.get("rsi_period", 14))),
        "atr_pct": atr_pct(candles, int(tech_cfg.get("atr_period", 14))),
    }


def direction_pnl_pct(direction: str, entry: float, exit_price: float) -> float:
    if direction == "long":
        return (exit_price - entry) / entry
    return (entry - exit_price) / entry


class UniversalBacktester:
    def __init__(self, strategy: dict[str, Any], provider: Any):
        self.strategy = strategy
        self.provider = provider

    def run(
        self,
        symbols: list[str] | None = None,
        days: int = 90,
        interval: str = "1h",
        end: datetime | None = None,
    ) -> dict[str, Any]:
        end_dt = end or datetime.now(TZ_UTC8)
        start_dt = end_dt - timedelta(days=days)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)

        market = self.strategy["market"]
        exclude = set(market.get("exclude_symbols", []))
        explicit_symbols = bool(symbols)
        if not symbols:
            symbols = self.provider.list_symbols(
                int(market.get("symbol_limit", 30)),
                float(market.get("min_quote_volume", 50_000_000)),
                exclude,
            )
        if explicit_symbols and not market.get("exclude_explicit_symbols", False):
            symbols = [s.strip().upper() for s in symbols if s.strip()]
        else:
            symbols = [s.strip().upper() for s in symbols if s.strip().upper() not in exclude]

        candles_by_symbol = {
            symbol: self.provider.klines(symbol, interval, start_ms, end_ms)
            for symbol in symbols
        }
        funding_by_symbol = {
            symbol: self.provider.funding(symbol, start_ms, end_ms)
            for symbol in symbols
        }

        return self._simulate(candles_by_symbol, funding_by_symbol, days, interval, start_ms, end_ms)

    def _simulate(
        self,
        candles_by_symbol: dict[str, list[Candle]],
        funding_by_symbol: dict[str, list[dict[str, float]]],
        days: int,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> dict[str, Any]:
        risk = self.strategy["risk"]
        sim = self.strategy["simulation"]
        tech_cfg = self.strategy.get("technical", {})

        initial_balance = float(risk.get("initial_balance", 1000))
        balance = initial_balance
        leverage = float(risk.get("leverage", 3))
        fee_rate = float(sim.get("fee_rate", 0.0004))
        slippage_rate = float(sim.get("slippage_rate", 0.0005))
        max_positions = int(risk.get("max_positions", 3))
        position_pct = float(risk.get("position_pct", 10)) / 100
        cooldown_hours = float(risk.get("cooldown_hours", 4))
        max_loss_per_trade = risk.get("max_loss_per_trade")
        max_hold_hours = risk.get("max_hold_hours")
        grace_hours = float(risk.get("grace_period_hours", 4))

        indexes = {s: 0 for s in candles_by_symbol}
        funding_indexes = {s: 0 for s in funding_by_symbol}
        open_positions: list[Position] = []
        closed_trades: list[dict[str, Any]] = []
        equity_curve = [{"time": start_ms, "equity": round(balance, 6)}]
        cooldown_until: dict[str, int] = {}
        consecutive_losses = 0
        next_id = 1

        all_times = sorted({c.time for candles in candles_by_symbol.values() for c in candles})
        for ts in all_times:
            if ts < start_ms or ts > end_ms:
                continue
            current = {}
            history = {}
            for symbol, candles in candles_by_symbol.items():
                idx = indexes[symbol]
                while idx + 1 < len(candles) and candles[idx + 1].time <= ts:
                    idx += 1
                indexes[symbol] = idx
                if idx >= 30 and candles[idx].time == ts:
                    current[symbol] = candles[idx]
                    history[symbol] = candles[:idx + 1]

            balance, closed_now, open_positions, loss_delta = self._update_positions(
                ts,
                current,
                open_positions,
                balance,
                fee_rate,
                slippage_rate,
                grace_hours,
                max_hold_hours,
            )
            if closed_now:
                closed_trades.extend(closed_now)
                for trade in closed_now:
                    cooldown_until[trade["symbol"]] = ts + int(cooldown_hours * MS_HOUR)
                    consecutive_losses = consecutive_losses + 1 if trade["pnl_usd"] < 0 else 0

            # 计算当前权益（包含浮动盈亏）
            floating_pnl = 0
            for pos in open_positions:
                if pos.symbol in current:
                    candle = current[pos.symbol]
                    pnl_pct = direction_pnl_pct(pos.direction, pos.entry_price, candle.close)
                    floating_pnl += pos.notional_usd * pnl_pct

            mark_to_market_equity = balance + floating_pnl
            equity_curve.append({"time": ts, "equity": round(mark_to_market_equity, 6)})

            if len(open_positions) >= max_positions:
                continue

            open_symbols = {p.symbol for p in open_positions}
            candidates = self._scan(
                ts,
                current,
                history,
                funding_by_symbol,
                funding_indexes,
                open_symbols,
                cooldown_until,
                tech_cfg,
                interval,
            )
            candidates.sort(key=lambda s: (s.score, 1 if s.strength == "S" else 0), reverse=True)

            for signal in candidates:
                if len(open_positions) >= max_positions:
                    break
                margin = max(0.0, balance * position_pct * self._position_multiplier(signal, consecutive_losses))
                if margin <= 0:
                    continue
                if max_loss_per_trade is not None:
                    loss_at_stop = margin * leverage * signal.sl_pct
                    if loss_at_stop > float(max_loss_per_trade):
                        margin = float(max_loss_per_trade) / (leverage * signal.sl_pct)
                entry_price = signal.price * (1 + slippage_rate if signal.direction == "long" else 1 - slippage_rate)
                fee = margin * leverage * fee_rate
                if balance - fee <= 0:
                    continue
                balance -= fee
                stop_loss = entry_price * (1 - signal.sl_pct if signal.direction == "long" else 1 + signal.sl_pct)
                take_profit = entry_price * (1 + signal.tp_pct if signal.direction == "long" else 1 - signal.tp_pct)
                open_positions.append(Position(
                    id=next_id,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    entry_time=ts,
                    entry_price=entry_price,
                    margin_usd=margin,
                    notional_usd=margin * leverage,
                    leverage=leverage,
                    sl_pct=signal.sl_pct,
                    tp_pct=signal.tp_pct,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    signal_type=signal.signal_type,
                    reason=signal.reason,
                    score=signal.score,
                ))
                next_id += 1
                open_symbols.add(signal.symbol)

        # Mark remaining positions to market at the last available close.
        if open_positions:
            last_current = {s: candles[-1] for s, candles in candles_by_symbol.items() if candles}
            balance, closed_now, open_positions, _ = self._force_close(
                end_ms,
                last_current,
                open_positions,
                balance,
                fee_rate,
                slippage_rate,
                "end_of_backtest",
            )
            closed_trades.extend(closed_now)
            equity_curve.append({"time": end_ms, "equity": round(balance, 6)})

        return self._report(
            closed_trades,
            equity_curve,
            initial_balance,
            balance,
            days,
            interval,
            list(candles_by_symbol),
            start_ms,
            end_ms,
        )

    def _scan(
        self,
        ts: int,
        current: dict[str, Candle],
        history: dict[str, list[Candle]],
        funding_by_symbol: dict[str, list[dict[str, float]]],
        funding_indexes: dict[str, int],
        open_symbols: set[str],
        cooldown_until: dict[str, int],
        tech_cfg: dict[str, Any],
        interval: str,
    ) -> list[Signal]:
        signals_cfg = self.strategy["signals"]
        candidates: list[Signal] = []
        dt = datetime.fromtimestamp(ts / 1000, timezone.utc)

        for symbol, candle in current.items():
            if symbol in open_symbols or cooldown_until.get(symbol, 0) > ts:
                continue
            candles = history.get(symbol, [])
            if len(candles) < 48:
                continue
            tech = technical_snapshot(candles, tech_cfg)
            atr = max(tech["atr_pct"], float(signals_cfg.get("min_sl_pct", 0.025)))

            if signals_cfg.get("extreme_funding_enabled", True) and dt.hour % 8 == 0:
                f_rows = funding_by_symbol.get(symbol, [])
                f_idx = funding_indexes.get(symbol, 0)
                while f_idx + 1 < len(f_rows) and f_rows[f_idx + 1]["time"] <= ts:
                    f_idx += 1
                funding_indexes[symbol] = f_idx
                recent = f_rows[max(0, f_idx - 7):f_idx + 1]
                if len(recent) >= 3:
                    rate = recent[-1]["rate"]
                    avg = sum(r["rate"] for r in recent) / len(recent)
                    if rate >= float(signals_cfg.get("extreme_pos_funding", 0.10)):
                        signal = self._build_signal(symbol, "short", "extreme_pos_funding", candle, atr, tech, f"funding {rate:+.4f}% avg {avg:+.4f}%")
                        if signal:
                            candidates.append(signal)
                    elif rate <= float(signals_cfg.get("extreme_neg_funding", -0.08)):
                        signal = self._build_signal(symbol, "long", "extreme_neg_funding", candle, atr, tech, f"funding {rate:+.4f}% avg {avg:+.4f}%")
                        if signal:
                            candidates.append(signal)

            if signals_cfg.get("pump_reversal_enabled", True):
                # 使用周期换算：24h对应的K线数量
                interval_ms = interval_to_ms(interval)
                bars_24h = max(1, int(24 * MS_HOUR / interval_ms))
                recent24 = candles[-bars_24h:]
                change_pct = (recent24[-1].close - recent24[0].close) / recent24[0].close * 100
                high6 = max(c.high for c in candles[-6:])
                low6 = min(c.low for c in candles[-6:])
                pullback = (high6 - candle.close) / high6 * 100 if high6 else 0
                bounce = (candle.close - low6) / low6 * 100 if low6 else 0
                if change_pct >= float(signals_cfg.get("pump_short_change_pct", 40)) and pullback >= float(signals_cfg.get("pump_short_pullback_pct", 6)):
                    signal = self._build_signal(symbol, "short", "pump_short", candle, atr, tech, f"24h +{change_pct:.1f}% pullback {pullback:.1f}%")
                    if signal:
                        candidates.append(signal)
                if change_pct <= -float(signals_cfg.get("crash_long_change_pct", 25)) and bounce >= float(signals_cfg.get("crash_long_bounce_pct", 4)):
                    signal = self._build_signal(symbol, "long", "crash_bounce", candle, atr, tech, f"24h {change_pct:.1f}% bounce {bounce:.1f}%")
                    if signal:
                        candidates.append(signal)

        return candidates

    def _build_signal(self, symbol: str, direction: str, signal_type: str, candle: Candle, base_sl: float, tech: dict[str, Any], reason: str) -> Signal | None:
        signals_cfg = self.strategy["signals"]
        risk = self.strategy["risk"]
        score = 5.0
        trend = tech.get("trend", "neutral")
        rsi_value = float(tech.get("rsi", 50))

        if direction == "long":
            score += 1.5 if trend == "up" else (-1.0 if trend == "down" else 0)
            score += 1.0 if rsi_value < 45 else (-1.0 if rsi_value > 70 else 0)
        else:
            score += 1.5 if trend == "down" else (-1.0 if trend == "up" else 0)
            score += 1.0 if rsi_value > 55 else (-1.0 if rsi_value < 30 else 0)

        min_score = float(signals_cfg.get("min_score", 4))
        if score < min_score:
            return None

        sl_mult = float(signals_cfg.get("atr_sl_multiplier", 1.5))
        sl_pct = max(base_sl * sl_mult, float(signals_cfg.get("min_sl_pct", 0.025)))
        sl_pct = min(sl_pct, float(signals_cfg.get("max_sl_pct", risk.get("max_sl_pct", 0.10))))
        tp_pct = sl_pct * float(signals_cfg.get("rr", 2.0))
        max_atr = float(signals_cfg.get("max_atr_pct", 0.05))
        if tech.get("atr_pct", 0) > max_atr:
            return None

        return Signal(
            symbol=symbol,
            direction=direction,
            signal_type=signal_type,
            strength="A" if score >= 6 else "B",
            price=candle.close,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            score=round(score, 2),
            reason=reason,
            stats={
                "trend": trend,
                "rsi": round(rsi_value, 2),
                "atr_pct": round(float(tech.get("atr_pct", 0)) * 100, 3),
            },
        )

    def _position_multiplier(self, signal: Signal, consecutive_losses: int) -> float:
        risk = self.strategy["risk"]
        mult = 1.0
        if signal.direction == "short" and signal.score >= float(risk.get("short_score_reduce_threshold", 5)):
            mult *= float(risk.get("short_score_reduce_mult", 0.5))
        if signal.score <= float(risk.get("low_score_boost_threshold", 4)):
            mult *= float(risk.get("low_score_boost_mult", 1.0))
        if consecutive_losses >= int(risk.get("consecutive_loss_threshold", 2)):
            mult *= float(risk.get("consecutive_loss_mult", 0.7))
        return mult

    def _update_positions(
        self,
        ts: int,
        current: dict[str, Candle],
        open_positions: list[Position],
        balance: float,
        fee_rate: float,
        slippage_rate: float,
        grace_hours: float,
        max_hold_hours: Any,
    ) -> tuple[float, list[dict[str, Any]], list[Position], int]:
        still_open = []
        closed = []
        loss_count = 0
        for pos in open_positions:
            candle = current.get(pos.symbol)
            if not candle:
                still_open.append(pos)
                continue
            exit_price = None
            exit_reason = None
            held_hours = (ts - pos.entry_time) / MS_HOUR
            in_grace = held_hours < grace_hours

            if pos.direction == "long":
                if not in_grace and candle.low <= pos.stop_loss:
                    exit_price = pos.stop_loss * (1 - slippage_rate)
                    exit_reason = "stop_loss"
                elif candle.high >= pos.take_profit:
                    exit_price = pos.take_profit * (1 - slippage_rate)
                    exit_reason = "take_profit"
            else:
                if not in_grace and candle.high >= pos.stop_loss:
                    exit_price = pos.stop_loss * (1 + slippage_rate)
                    exit_reason = "stop_loss"
                elif candle.low <= pos.take_profit:
                    exit_price = pos.take_profit * (1 + slippage_rate)
                    exit_reason = "take_profit"

            if exit_price is None and max_hold_hours is not None and held_hours >= float(max_hold_hours):
                exit_price = candle.close
                exit_reason = "max_hold"

            if exit_price is None:
                still_open.append(pos)
                continue
            trade, balance = self._close_position(ts, pos, exit_price, exit_reason, balance, fee_rate)
            closed.append(trade)
            if trade["pnl_usd"] < 0:
                loss_count += 1
        return balance, closed, still_open, loss_count

    def _force_close(
        self,
        ts: int,
        current: dict[str, Candle],
        open_positions: list[Position],
        balance: float,
        fee_rate: float,
        slippage_rate: float,
        reason: str,
    ) -> tuple[float, list[dict[str, Any]], list[Position], int]:
        closed = []
        for pos in open_positions:
            candle = current.get(pos.symbol)
            if not candle:
                continue
            exit_price = candle.close * (1 - slippage_rate if pos.direction == "long" else 1 + slippage_rate)
            trade, balance = self._close_position(ts, pos, exit_price, reason, balance, fee_rate)
            closed.append(trade)
        return balance, closed, [], 0

    def _close_position(self, ts: int, pos: Position, exit_price: float, reason: str, balance: float, fee_rate: float) -> tuple[dict[str, Any], float]:
        pnl_pct = direction_pnl_pct(pos.direction, pos.entry_price, exit_price)
        gross = pos.notional_usd * pnl_pct
        exit_fee = pos.notional_usd * fee_rate
        net = gross - exit_fee
        balance += net
        trade = {
            "id": pos.id,
            "symbol": pos.symbol,
            "direction": pos.direction,
            "signal_type": pos.signal_type,
            "entry_time": ms_to_text(pos.entry_time),
            "exit_time": ms_to_text(ts),
            "entry_price": round(pos.entry_price, 8),
            "exit_price": round(exit_price, 8),
            "margin_usd": round(pos.margin_usd, 4),
            "notional_usd": round(pos.notional_usd, 4),
            "pnl_usd": round(net, 4),
            "pnl_pct_on_margin": round((net / pos.margin_usd) * 100 if pos.margin_usd else 0, 4),
            "exit_reason": reason,
            "score": pos.score,
            "reason": pos.reason,
        }
        return trade, balance

    def _report(
        self,
        trades: list[dict[str, Any]],
        equity_curve: list[dict[str, Any]],
        initial_balance: float,
        final_balance: float,
        days: int,
        interval: str,
        symbols: list[str],
        start_ms: int,
        end_ms: int,
    ) -> dict[str, Any]:
        wins = [t for t in trades if t["pnl_usd"] > 0]
        losses = [t for t in trades if t["pnl_usd"] <= 0]
        gross_profit = sum(t["pnl_usd"] for t in wins)
        gross_loss = abs(sum(t["pnl_usd"] for t in losses))
        equity_values = [p["equity"] for p in equity_curve]
        max_dd = max_drawdown(equity_values)
        by_symbol: dict[str, dict[str, Any]] = {}
        by_type: dict[str, dict[str, Any]] = {}
        for trade in trades:
            add_bucket(by_symbol, trade["symbol"], trade)
            add_bucket(by_type, trade["signal_type"], trade)
        summary = {
            "strategy": self.strategy.get("name", "custom"),
            "version": self.strategy.get("version", "custom"),
            "start": ms_to_text(start_ms),
            "end": ms_to_text(end_ms),
            "days": days,
            "interval": interval,
            "symbols": symbols,
            "initial_balance": round(initial_balance, 4),
            "final_balance": round(final_balance, 4),
            "pnl_usd": round(final_balance - initial_balance, 4),
            "roi_pct": round((final_balance / initial_balance - 1) * 100, 4) if initial_balance else 0,
            "trades": len(trades),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
            "max_drawdown_pct": round(max_dd * 100, 4),
            "avg_trade_usd": round(sum(t["pnl_usd"] for t in trades) / len(trades), 4) if trades else 0,
        }
        return {
            "summary": summary,
            "equity_curve": equity_curve,
            "trades": trades,
            "by_symbol": finish_buckets(by_symbol),
            "by_signal_type": finish_buckets(by_type),
            "config": self.strategy,
        }


def add_bucket(buckets: dict[str, dict[str, Any]], key: str, trade: dict[str, Any]) -> None:
    bucket = buckets.setdefault(key, {"trades": 0, "wins": 0, "pnl_usd": 0.0})
    bucket["trades"] += 1
    bucket["wins"] += 1 if trade["pnl_usd"] > 0 else 0
    bucket["pnl_usd"] += trade["pnl_usd"]


def finish_buckets(buckets: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for key, bucket in buckets.items():
        trades = bucket["trades"]
        out[key] = {
            "trades": trades,
            "wins": bucket["wins"],
            "win_rate_pct": round(bucket["wins"] / trades * 100, 2) if trades else 0,
            "pnl_usd": round(bucket["pnl_usd"], 4),
        }
    return dict(sorted(out.items(), key=lambda item: item[1]["pnl_usd"], reverse=True))


def max_drawdown(equity: list[float]) -> float:
    peak = equity[0] if equity else 0
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def ms_to_text(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ_UTC8).strftime("%Y-%m-%d %H:%M:%S")


def save_report(report: dict[str, Any], output_dir: Path | None = None) -> Path:
    output_dir = output_dir or Path(__file__).resolve().parents[1] / "data" / "backtest_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    strategy = report["summary"]["strategy"].replace(" ", "_")
    path = output_dir / f"{now}_{strategy}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    latest = output_dir / "latest.json"
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return path
