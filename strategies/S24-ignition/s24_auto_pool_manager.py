#!/usr/bin/env python3
"""
S24 Auto Pool Manager

自动维护 S24-Ignition 的动态标的池：
  Candidate/Screen output -> Watch -> Probe -> LowRisk -> Active -> Quarantine

设计原则：
  - 不直接扫描交易信号，不下单；交易器只读取 pool_state。
  - Watch 只观察；Probe/LowRisk/Active 才允许交易。
  - 默认 dry-run 可查看动作；加 --apply 才写入 s24_pool_state.json。
  - 所有升降级写 action_log，便于复盘。

典型用法：
  python3 s24_auto_pool_manager.py --dry-run
  python3 s24_auto_pool_manager.py --apply
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
S24_DIR = ROOT / "strategies" / "S24-ignition"
DATA_DIR = S24_DIR / "data"
STATE_FILE = S24_DIR / "s24_pool_state.json"
SCREEN_FILE = DATA_DIR / "s24_symbol_candidates.json"
PAPER_TRADES_FILE = DATA_DIR / "s24_paper_trades.jsonl"
TESTNET_TRADES_FILE = S24_DIR / "testnet" / "data_S24" / "s24_trades.jsonl"
REPORT_DIR = S24_DIR / "data"
TZ_UTC8 = timezone(timedelta(hours=8))

# Tier params
MARGIN_CAPS = {"probe": 30.0, "lowrisk": 100.0, "active": 300.0}
DAILY_CAPS = {"probe": 1, "lowrisk": 1, "active": 2}

DEFAULT_LIMITS = {
    "max_active": 20,
    "max_lowrisk": 10,
    "max_probe": 3,
    "max_watch": 80,
    "max_active_plus_lowrisk": 30,
}

# Candidate -> Watch
WATCH_MIN_SCORE = 45.0
WATCH_EXT_SCORE_GATE = 35.0
WATCH_MIN_EXT_SCORE = 15.0

# Watch -> Probe
PROBE_MIN_SCORE = 45.0
PROBE_MIN_HR_SPIKES = 8
PROBE_MIN_QUALITY_PROXY = 0.0  # reserved; screen output may not carry quality for all symbols
PROBE_COOLDOWN_DAYS = 3

# Probe -> LowRisk
PROBE_TO_LOWRISK_TRADES = 5
PROBE_TO_LOWRISK_PF = 1.25
PROBE_TO_LOWRISK_WR = 0.45

# LowRisk -> Active
LOWRISK_MIN_DAYS = 5
LOWRISK_TO_ACTIVE_TRADES = 5
LOWRISK_TO_ACTIVE_PF = 1.20
LOWRISK_TO_ACTIVE_MAX_DD = 0.05

# Demotion
PF_30D_QUARANTINE = 0.80
LOSS_30D_PCT = 0.05
MIN_SIGNALS_30D = 5
RECENT8_LOSS_N = 5
DAILY_LOSS_PCT = 0.03
QUARANTINE_DAYS = 14
QUARANTINE_REVIEW_EXTEND_DAYS = 7
REACTIVATE_MIN_SPIKES_7D = 2

STRUCTURAL_QUARANTINE = {"BUSDT", "BILLUSDT", "BNBUSDT", "LINKUSDT", "SAGAUSDT"}


def now_dt() -> datetime:
    return datetime.now(TZ_UTC8)


def today_str() -> str:
    return now_dt().date().isoformat()


def iso() -> str:
    return now_dt().isoformat()


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value)
        if len(s) == 10:
            return datetime.fromisoformat(s).replace(tzinfo=TZ_UTC8)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_UTC8)
        return dt.astimezone(TZ_UTC8)
    except Exception:
        return None


def days_since(value: Any) -> int:
    dt = parse_dt(value)
    if not dt:
        return 0
    return max(0, (now_dt().date() - dt.date()).days)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def default_state() -> dict[str, Any]:
    return {
        "version": "2.0",
        "updated_at": iso(),
        "limits": deepcopy(DEFAULT_LIMITS),
        "active_pool": {},
        "lowrisk_pool": {},
        "probe_pool": {},
        "watch_pool": {},
        "quarantine_pool": {},
        "action_log": [],
    }


def normalize_entry(sym: str, entry: dict[str, Any] | None, tier: str) -> dict[str, Any]:
    entry = dict(entry or {})
    entry["tier"] = tier
    entry.setdefault("added", today_str())
    entry.setdefault("source", "legacy" if tier == "active" else "auto_pool_manager")
    if tier in MARGIN_CAPS:
        entry.setdefault("margin_cap", MARGIN_CAPS[tier])
        entry.setdefault("symbol_daily_cap", DAILY_CAPS[tier])
    if tier == "active":
        entry.setdefault("first_loss_stop", True)
    if tier == "probe":
        entry.setdefault("consecutive_losses", 0)
        entry.setdefault("probe_started", entry.get("added", today_str()))
    if tier == "watch":
        entry.setdefault("shadow_signals", 0)
        entry.setdefault("shadow_pf", 0.0)
        entry.setdefault("shadow_wr", 0.0)
        entry.setdefault("external_score", 0.0)
        entry.setdefault("final_score", float(entry.get("score", 0) or 0) + float(entry.get("external_score", 0) or 0))
    if tier == "quarantine":
        entry.setdefault("reason", entry.get("reason", "unspecified"))
        entry.setdefault("demoted", entry.get("demoted", today_str()))
        entry.setdefault("review_after", entry.get("review_after") or (now_dt().date() + timedelta(days=QUARANTINE_DAYS)).isoformat())
        entry.setdefault("failed_reviews", 0)
    return entry


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert old v1 state to v2 without dropping symbols."""
    st = default_state()
    st["version"] = "2.0"
    if raw:
        st["updated_at"] = raw.get("updated_at") or raw.get("last_scan") or iso()
        st["limits"].update(raw.get("limits") or {})
        st["action_log"] = list(raw.get("action_log") or [])[-500:]
        for tier_key, tier in [
            ("active_pool", "active"),
            ("lowrisk_pool", "lowrisk"),
            ("probe_pool", "probe"),
            ("watch_pool", "watch"),
            ("quarantine_pool", "quarantine"),
        ]:
            for sym, ent in (raw.get(tier_key) or {}).items():
                st[tier_key][sym] = normalize_entry(sym, ent, tier)
    return st


def load_state() -> dict[str, Any]:
    return migrate_state(load_json(STATE_FILE, {}))


def save_state(st: dict[str, Any]) -> None:
    st["updated_at"] = iso()
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n")


def log_action(st: dict[str, Any], symbol: str, action: str, reason: str, metrics: dict[str, Any] | None = None) -> None:
    st.setdefault("action_log", []).append({
        "time": iso(),
        "symbol": symbol,
        "action": action,
        "reason": reason,
        "metrics": metrics or {},
    })
    st["action_log"] = st["action_log"][-1000:]


def remove_from_all_pools(st: dict[str, Any], symbol: str) -> None:
    for key in ["active_pool", "lowrisk_pool", "probe_pool", "watch_pool", "quarantine_pool"]:
        st.get(key, {}).pop(symbol, None)


def move_symbol(st: dict[str, Any], symbol: str, from_key: str | None, to_key: str, entry: dict[str, Any], reason: str, action: str) -> None:
    if from_key:
        st.get(from_key, {}).pop(symbol, None)
    # avoid duplicates
    remove_from_all_pools(st, symbol)
    tier = to_key.replace("_pool", "")
    st[to_key][symbol] = normalize_entry(symbol, entry, tier)
    log_action(st, symbol, action, reason, {"to": tier})


def screen_candidates() -> list[dict[str, Any]]:
    data = load_json(SCREEN_FILE, {})
    rows = data.get("watch_recommended") or data.get("passed_candidates") or []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = r.get("symbol")
        if not sym:
            continue
        score = float(r.get("final_score", r.get("score", 0)) or 0)
        ext = float(r.get("external_score", 0) or 0)
        s24_score = float(r.get("s24_score", r.get("score", score - ext)) or 0)
        out.append({**r, "symbol": sym.upper(), "score": s24_score, "external_score": ext, "final_score": score})
    out.sort(key=lambda x: -float(x.get("final_score", 0) or 0))
    return out


def parse_trade_time(t: dict[str, Any]) -> datetime | None:
    for key in ["exit_time", "time"]:
        if t.get(key):
            return parse_dt(t.get(key))
    ms = t.get("exit_time_ms") or t.get("time_ms") or t.get("timestamp")
    if ms:
        try:
            return datetime.fromtimestamp(float(ms) / 1000, tz=TZ_UTC8)
        except Exception:
            pass
    return None


def all_trades() -> list[dict[str, Any]]:
    rows = load_jsonl(PAPER_TRADES_FILE) + load_jsonl(TESTNET_TRADES_FILE)
    rows.sort(key=lambda x: parse_trade_time(x) or datetime.min.replace(tzinfo=TZ_UTC8))
    return rows


def trade_metrics(symbol: str, trades: list[dict[str, Any]], days: int | None = None) -> dict[str, Any]:
    cutoff = now_dt() - timedelta(days=days) if days else None
    arr = []
    for t in trades:
        if str(t.get("symbol", "")).upper() != symbol:
            continue
        dt = parse_trade_time(t)
        if cutoff and (not dt or dt < cutoff):
            continue
        arr.append(t)
    pnls = [float(t.get("pnl_usd", 0) or 0) for t in arr]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    pf = gp / gl if gl > 0 else (99.0 if gp > 0 else 0.0)
    wr = len(wins) / len(pnls) if pnls else 0.0
    # rough realized DD on symbol cumulative pnl
    peak = 0.0; eq = 0.0; max_dd = 0.0
    for p in pnls:
        eq += p; peak = max(peak, eq); max_dd = max(max_dd, peak - eq)
    recent8 = pnls[-8:]
    # current day pnl UTC+8
    day = today_str()
    day_pnl = sum(float(t.get("pnl_usd", 0) or 0) for t in arr if (parse_trade_time(t) and parse_trade_time(t).date().isoformat() == day))
    return {
        "trades": len(pnls),
        "pnl": round(sum(pnls), 4),
        "wr": round(wr, 4),
        "pf": round(pf, 4),
        "max_dd_usd": round(max_dd, 4),
        "losses_recent8": sum(1 for p in recent8 if p <= 0),
        "consecutive_losses": consecutive_losses(pnls),
        "day_pnl": round(day_pnl, 4),
    }


def consecutive_losses(pnls: list[float]) -> int:
    n = 0
    for p in reversed(pnls):
        if p <= 0:
            n += 1
        else:
            break
    return n


def state_symbols(st: dict[str, Any]) -> set[str]:
    out = set()
    for key in ["active_pool", "lowrisk_pool", "probe_pool", "watch_pool", "quarantine_pool"]:
        out.update(st.get(key, {}).keys())
    return out


def add_candidates_to_watch(st: dict[str, Any], candidates: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
    limits = st.get("limits", DEFAULT_LIMITS)
    known = state_symbols(st)
    room = max(0, int(limits.get("max_watch", 80)) - len(st.get("watch_pool", {})))
    if room <= 0:
        return
    for c in candidates:
        sym = c["symbol"]
        if sym in known or sym in STRUCTURAL_QUARANTINE:
            continue
        score = float(c.get("score", 0) or 0)
        ext = float(c.get("external_score", 0) or 0)
        final = float(c.get("final_score", score + ext) or 0)
        if not (score >= WATCH_MIN_SCORE or (score >= WATCH_EXT_SCORE_GATE and ext >= WATCH_MIN_EXT_SCORE)):
            continue
        ent = normalize_entry(sym, {
            "added": today_str(),
            "source": c.get("source", "screen_symbols"),
            "score": score,
            "external_score": ext,
            "final_score": final,
            "hr_spikes": c.get("hr_spikes"),
            "hr_wr": c.get("hr_wr"),
            "avg_mfe_hr": c.get("avg_mfe_hr"),
            "fbk_pct": c.get("fbk_pct"),
            "shadow_signals": c.get("hr_spikes", 0),
            "shadow_wr": c.get("hr_wr", 0),
        }, "watch")
        st["watch_pool"][sym] = ent
        log_action(st, sym, "candidate_to_watch", "screen_passed", {"final_score": final})
        actions.append({"symbol": sym, "action": "candidate_to_watch", "score": final})
        known.add(sym)
        room -= 1
        if room <= 0:
            break


def promote_watch_to_probe(st: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    limits = st.get("limits", DEFAULT_LIMITS)
    room = max(0, int(limits.get("max_probe", 3)) - len(st.get("probe_pool", {})))
    if room <= 0:
        return
    ranked = sorted(st.get("watch_pool", {}).items(), key=lambda kv: -float(kv[1].get("final_score", kv[1].get("score", 0)) or 0))
    for sym, ent in ranked:
        if room <= 0:
            break
        if ent.get("cooldown_until") and parse_dt(ent.get("cooldown_until")) and parse_dt(ent.get("cooldown_until")) > now_dt():
            continue
        score = float(ent.get("final_score", ent.get("score", 0)) or 0)
        hr_spikes = int(ent.get("hr_spikes", ent.get("shadow_signals", 0)) or 0)
        if score < PROBE_MIN_SCORE or hr_spikes < PROBE_MIN_HR_SPIKES:
            continue
        new_ent = {**ent, "added": today_str(), "probe_started": today_str(), "source": "watch_promoted"}
        move_symbol(st, sym, "watch_pool", "probe_pool", new_ent, "probe_slot_and_strong_watch_score", "watch_to_probe")
        actions.append({"symbol": sym, "action": "watch_to_probe", "score": score})
        room -= 1


def manage_probe(st: dict[str, Any], trades: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
    for sym, ent in list(st.get("probe_pool", {}).items()):
        m = trade_metrics(sym, trades)
        ent["probe_trades"] = m["trades"]
        ent["probe_pf"] = m["pf"]
        ent["probe_wr"] = m["wr"]
        ent["consecutive_losses"] = m["consecutive_losses"]
        # Probe fail: back to Watch, not quarantine.
        if m["consecutive_losses"] >= 2:
            new_ent = {**ent, "tier": "watch", "cooldown_until": (now_dt().date() + timedelta(days=PROBE_COOLDOWN_DAYS)).isoformat(),
                       "source": "probe_failed", "shadow_pf": m["pf"], "shadow_wr": m["wr"], "shadow_signals": m["trades"]}
            move_symbol(st, sym, "probe_pool", "watch_pool", new_ent, "probe_2_consecutive_losses", "probe_to_watch")
            actions.append({"symbol": sym, "action": "probe_to_watch", "metrics": m})
            continue
        if m["trades"] >= PROBE_TO_LOWRISK_TRADES and m["pf"] >= PROBE_TO_LOWRISK_PF and m["wr"] >= PROBE_TO_LOWRISK_WR:
            if len(st.get("lowrisk_pool", {})) < int(st.get("limits", DEFAULT_LIMITS).get("max_lowrisk", 10)):
                new_ent = {**ent, "added": today_str(), "source": "probe_promoted", "probe_trades": m["trades"], "probe_pf": m["pf"], "probe_wr": m["wr"]}
                move_symbol(st, sym, "probe_pool", "lowrisk_pool", new_ent, "probe_metrics_passed", "probe_to_lowrisk")
                actions.append({"symbol": sym, "action": "probe_to_lowrisk", "metrics": m})


def manage_lowrisk_active(st: dict[str, Any], trades: list[dict[str, Any]], actions: list[dict[str, Any]], balance: float) -> None:
    limits = st.get("limits", DEFAULT_LIMITS)
    for pool_key in ["lowrisk_pool", "active_pool"]:
        for sym, ent in list(st.get(pool_key, {}).items()):
            m30 = trade_metrics(sym, trades, days=30)
            tier = ent.get("tier", pool_key.replace("_pool", ""))
            # Demotions/quarantine
            reason = None
            if m30["trades"] >= 5 and m30["pf"] < PF_30D_QUARANTINE:
                reason = "pf_30d_low"
            elif m30["pnl"] < -balance * LOSS_30D_PCT:
                reason = "loss_30d_gt_5pct"
            # Do not demote legacy/manual Active symbols merely because local trade history is absent.
            # Signal-count aging should be based on scanner/watch stats, not only closed trades.
            elif (ent.get("source") in {"probe_promoted", "lowrisk_promoted", "auto_pool_manager"}
                  and days_since(ent.get("added")) >= 30
                  and 0 < m30["trades"] < MIN_SIGNALS_30D):
                reason = "signals_30d_too_low"
            elif m30["losses_recent8"] >= RECENT8_LOSS_N:
                reason = "recent8_loss_ge5"
            elif m30["day_pnl"] < -balance * DAILY_LOSS_PCT:
                reason = "daily_loss_gt_3pct"
            if reason:
                qent = {**ent, "demoted": today_str(), "reason": reason,
                        "review_after": (now_dt().date() + timedelta(days=QUARANTINE_DAYS)).isoformat(),
                        "failed_reviews": 0}
                move_symbol(st, sym, pool_key, "quarantine_pool", qent, reason, f"{tier}_to_quarantine")
                actions.append({"symbol": sym, "action": f"{tier}_to_quarantine", "reason": reason, "metrics": m30})
                continue
            # LowRisk -> Active
            if pool_key == "lowrisk_pool":
                m = trade_metrics(sym, trades)
                if (days_since(ent.get("added")) >= LOWRISK_MIN_DAYS and
                    m["trades"] >= LOWRISK_TO_ACTIVE_TRADES and
                    m["pf"] >= LOWRISK_TO_ACTIVE_PF and
                    (m["max_dd_usd"] / max(balance, 1.0)) <= LOWRISK_TO_ACTIVE_MAX_DD and
                    len(st.get("active_pool", {})) < int(limits.get("max_active", 20)) and
                    (len(st.get("active_pool", {})) + len(st.get("lowrisk_pool", {}))) <= int(limits.get("max_active_plus_lowrisk", 30))):
                    new_ent = {**ent, "added": today_str(), "source": "lowrisk_promoted", "lowrisk_trades": m["trades"], "lowrisk_pf": m["pf"], "lowrisk_wr": m["wr"]}
                    move_symbol(st, sym, "lowrisk_pool", "active_pool", new_ent, "lowrisk_metrics_passed", "lowrisk_to_active")
                    actions.append({"symbol": sym, "action": "lowrisk_to_active", "metrics": m})


def manage_quarantine(st: dict[str, Any], candidates: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
    candidate_by_symbol = {c["symbol"]: c for c in candidates}
    for sym, ent in list(st.get("quarantine_pool", {}).items()):
        if sym in STRUCTURAL_QUARANTINE and ent.get("review_after") is None:
            continue
        review = parse_dt(ent.get("review_after"))
        if review and review.date() > now_dt().date():
            continue
        c = candidate_by_symbol.get(sym, {})
        recent_spikes = int(c.get("hr_spikes", ent.get("recent_hr_spikes_7d", 0)) or 0)
        # We do not always have real 7d metrics; use screen hr_spikes as a proxy if available.
        if recent_spikes >= REACTIVATE_MIN_SPIKES_7D:
            went = {"added": today_str(), "source": "quarantine_reactivated", "score": c.get("score", 0),
                    "external_score": c.get("external_score", 0), "final_score": c.get("final_score", c.get("score", 0)),
                    "hr_spikes": recent_spikes}
            move_symbol(st, sym, "quarantine_pool", "watch_pool", went, "quarantine_review_active_again", "quarantine_to_watch")
            actions.append({"symbol": sym, "action": "quarantine_to_watch", "hr_spikes": recent_spikes})
        else:
            ent["failed_reviews"] = int(ent.get("failed_reviews", 0) or 0) + 1
            ent["review_after"] = (now_dt().date() + timedelta(days=QUARANTINE_REVIEW_EXTEND_DAYS)).isoformat()
            log_action(st, sym, "quarantine_review_extended", "not_enough_recent_spikes", {"hr_spikes": recent_spikes})
            actions.append({"symbol": sym, "action": "quarantine_review_extended", "hr_spikes": recent_spikes})


def report(st_before: dict[str, Any], st_after: dict[str, Any], actions: list[dict[str, Any]], path: Path) -> None:
    out = {
        "generated_at": iso(),
        "actions": actions,
        "counts_before": {k: len(st_before.get(k, {})) for k in ["active_pool", "lowrisk_pool", "probe_pool", "watch_pool", "quarantine_pool"]},
        "counts_after": {k: len(st_after.get(k, {})) for k in ["active_pool", "lowrisk_pool", "probe_pool", "watch_pool", "quarantine_pool"]},
        "pools_after": {k: sorted(st_after.get(k, {}).keys()) for k in ["active_pool", "lowrisk_pool", "probe_pool", "watch_pool", "quarantine_pool"]},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="S24 auto pool manager")
    p.add_argument("--apply", action="store_true", help="write changes to s24_pool_state.json")
    p.add_argument("--dry-run", action="store_true", help="show/report changes without writing state (default)")
    p.add_argument("--screen-file", default=str(SCREEN_FILE), help="screen_symbols output JSON")
    p.add_argument("--balance", type=float, default=5000.0, help="account balance for risk thresholds")
    p.add_argument("--report", default=str(REPORT_DIR / f"s24_auto_pool_report_{now_dt().date().strftime('%Y%m%d')}.json"))
    return p.parse_args()


def main() -> None:
    args = parse_args()
    global SCREEN_FILE
    SCREEN_FILE = Path(args.screen_file)

    st_before = load_state()
    st = deepcopy(st_before)
    actions: list[dict[str, Any]] = []
    candidates = screen_candidates()
    trades = all_trades()

    # Ensure structural quarantine survives migrations.
    for sym in STRUCTURAL_QUARANTINE:
        if sym not in st.get("quarantine_pool", {}):
            st["quarantine_pool"][sym] = normalize_entry(sym, {"reason": "structural_exclude", "review_after": None, "demoted": today_str()}, "quarantine")

    add_candidates_to_watch(st, candidates, actions)
    manage_quarantine(st, candidates, actions)
    promote_watch_to_probe(st, actions)
    manage_probe(st, trades, actions)
    manage_lowrisk_active(st, trades, actions, args.balance)

    # Stable metadata
    st["version"] = "2.0"
    st["updated_at"] = iso()

    report_path = Path(args.report)
    report(st_before, st, actions, report_path)

    print(json.dumps({
        "apply": bool(args.apply),
        "actions": actions,
        "counts_before": {k: len(st_before.get(k, {})) for k in ["active_pool", "lowrisk_pool", "probe_pool", "watch_pool", "quarantine_pool"]},
        "counts_after": {k: len(st.get(k, {})) for k in ["active_pool", "lowrisk_pool", "probe_pool", "watch_pool", "quarantine_pool"]},
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))

    if args.apply:
        save_state(st)


if __name__ == "__main__":
    main()
