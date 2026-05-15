"""
review_db.py — v8复盘数据库模块
基于okx-review skill精简适配到交易系统

存储: ~/.hermes/trading/data/review.db (SQLite)
表:
- trades: 交易记录快照(从trades.json同步)
- tags: 自定义标签 (trade_id, tag)
- notes: 复盘笔记 (trade_id, content, created_at)
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime

REVIEW_DB_PATH = Path.home() / ".hermes" / "trading" / "data" / "review.db"


def get_conn():
    """获取SQLite连接，自动创建数据库"""
    REVIEW_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(REVIEW_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL,
            exit_price REAL,
            entry_time TEXT,
            exit_time TEXT,
            pnl_usd REAL,
            pnl_pct REAL,
            exit_reason TEXT,
            signal_type TEXT,
            signal_strength TEXT,
            signal_reason TEXT,
            leverage INTEGER DEFAULT 3,
            position_usd REAL,
            rr REAL,
            v8_signal_quality REAL,
            v8_macro_score REAL,
            ingested_at TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL REFERENCES trades(id),
            tag TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL REFERENCES trades(id),
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_tags_trade ON tags(trade_id);
        CREATE INDEX IF NOT EXISTS idx_notes_trade ON notes(trade_id);
        CREATE INDEX IF NOT EXISTS idx_trades_exit ON trades(exit_time);
    """)
    conn.commit()
    conn.close()


def sync_trade(trade: dict):
    """
    从trades.json同步一条交易记录到review DB
    开仓时创建记录，平仓时更新
    """
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO trades
        (id, symbol, direction, entry_price, exit_price,
         entry_time, exit_time, pnl_usd, pnl_pct, exit_reason,
         signal_type, signal_strength, signal_reason,
         leverage, position_usd, rr, v8_signal_quality, v8_macro_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade["id"],
        trade["symbol"],
        trade["direction"],
        trade.get("entry_price"),
        trade.get("exit_price"),
        trade.get("entry_time"),
        trade.get("exit_time"),
        trade.get("pnl_usd"),
        trade.get("pnl_pct"),
        trade.get("exit_reason"),
        trade.get("signal_type"),
        trade.get("signal_strength"),
        trade.get("signal_reason", ""),
        trade.get("leverage"),
        trade.get("position_usd"),
        trade.get("signal_rr"),
        trade.get("v8_signal_quality"),
        trade.get("v8_macro_score"),
    ))
    conn.commit()
    conn.close()


def add_tag(trade_id: str, tag: str) -> bool:
    """给交易添加标签"""
    try:
        conn = get_conn()
        conn.execute("INSERT INTO tags (trade_id, tag) VALUES (?, ?)",
                     (trade_id, tag))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[review_db] 添加标签失败: {e}")
        return False


def add_note(trade_id: str, content: str) -> bool:
    """给交易添加复盘笔记"""
    try:
        conn = get_conn()
        conn.execute("INSERT INTO notes (trade_id, content) VALUES (?, ?)",
                     (trade_id, content))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[review_db] 添加笔记失败: {e}")
        return False


def get_tags(trade_id: str) -> list:
    """获取交易的所有标签"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT tag, created_at FROM tags WHERE trade_id = ? ORDER BY created_at",
        (trade_id,)
    ).fetchall()
    conn.close()
    return [{"tag": r["tag"], "created_at": r["created_at"]} for r in rows]


def get_notes(trade_id: str) -> list:
    """获取交易的所有复盘笔记"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT content, created_at FROM notes WHERE trade_id = ? ORDER BY created_at",
        (trade_id,)
    ).fetchall()
    conn.close()
    return [{"content": r["content"], "created_at": r["created_at"]} for r in rows]


def get_stats() -> dict:
    """
    获取交易统计数据
    返回: {
        "total": int, "closed": int, "wins": int,
        "losses": int, "win_rate": float,
        "total_pnl": float, "total_pos_usd": float,
        "top_tags": [{"tag": str, "count": int}, ...],
    }
    """
    conn = get_conn()
    stats = {}

    stats["total"] = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    stats["closed"] = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE exit_price IS NOT NULL"
    ).fetchone()[0]

    win_row = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE pnl_usd IS NOT NULL AND pnl_usd > 0"
    ).fetchone()
    stats["wins"] = win_row[0]

    loss_row = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE pnl_usd IS NOT NULL AND pnl_usd < 0"
    ).fetchone()
    stats["losses"] = loss_row[0]

    closed_count = stats["wins"] + stats["losses"]
    stats["win_rate"] = round(stats["wins"] / closed_count, 3) if closed_count > 0 else 0

    pnl = conn.execute(
        "SELECT COALESCE(SUM(pnl_usd), 0) FROM trades WHERE pnl_usd IS NOT NULL"
    ).fetchone()
    stats["total_pnl"] = round(pnl[0], 2)

    pos = conn.execute(
        "SELECT COALESCE(SUM(position_usd), 0) FROM trades"
    ).fetchone()
    stats["total_pos_usd"] = round(pos[0], 2)

    # 标签统计
    top = conn.execute("""
        SELECT tag, COUNT(*) as cnt FROM tags
        GROUP BY tag ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    stats["top_tags"] = [{"tag": r["tag"], "count": r["cnt"]} for r in top]

    conn.close()
    return stats


def get_recent_trades(limit: int = 20) -> list:
    """获取最近交易的摘要"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, symbol, direction, entry_price, exit_price,
               pnl_usd, pnl_pct, exit_reason,
               signal_type, exit_time, ingested_at
        FROM trades ORDER BY COALESCE(exit_time, ingested_at) DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trade_detail(trade_id: str) -> dict:
    """获取单笔交易的完整信息+标签+笔记"""
    conn = get_conn()
    trade = conn.execute(
        "SELECT * FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    if not trade:
        return None
    detail = dict(trade)
    detail["tags"] = get_tags(trade_id)
    detail["notes"] = get_notes(trade_id)
    return detail


# 初始化
init_db()
