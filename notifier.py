"""
通知模块 v6 — 宏观快照 + 智能复盘版
每笔交易包含完整逻辑说明 + 技术指标 + 平仓后自动复盘
v6新增: 宏观快照(BTC趋势/FGI/ETF流入)、多时间框架评分、聪明钱分析、双平台推送
"""
import json
import os
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

# 导入宏观快照和智能分析模块
try:
    from intel_flow import intel_quick_macro, get_tf_scores, get_smart_money_analysis
except ImportError:
    # 兜底: 如果intel_flow不可用，定义stub函数
    def intel_quick_macro() -> dict:
        return {}
    def get_tf_scores(symbol: str = "BTCUSDT") -> dict:
        return {}
    def get_smart_money_analysis(symbol: str = "BTCUSDT") -> dict:
        return {}


HERMES_API = os.environ.get("HERMES_API_URL", "http://localhost:8787")

# 支持双平台: 默认微信，也可推送到Telegram
# 如果传入了telegram_chat_id配置，则自动推送双平台


def send_wechat(text: str) -> bool:
    """通过 Hermes API Server 发微信"""
    try:
        resp = requests.post(
            f"{HERMES_API}/api/message",
            json={"platform": "weixin", "text": text},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        notif_dir = Path.home() / ".hermes" / "trading" / "notifications"
        notif_dir.mkdir(parents=True, exist_ok=True)
        import time
        fallback_path = notif_dir / f"notif_{int(time.time())}.txt"
        fallback_path.write_text(text, encoding="utf-8")
        print(f"[notifier] send_wechat failed: {e}, saved to {fallback_path}")
        return False


def send_telegram(text: str) -> bool:
    """通过 Hermes API Server 发 Telegram"""
    try:
        resp = requests.post(
            f"{HERMES_API}/api/message",
            json={"platform": "telegram", "text": text},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[notifier] send_telegram failed: {e}")
        return False


def send_message_to_both(text: str):
    """同时发送微信和Telegram"""
    wx_ok = send_wechat(text)
    tg_ok = send_telegram(text)
    return wx_ok or tg_ok


def notify(text: str):
    """发送微信通知（向后兼容）"""
    return send_wechat(text)


# ═══════════════════════════════════════
# 开仓通知 — 包含完整交易逻辑
# ═══════════════════════════════════════

def format_open_message(trade: dict, env_detail: str = "") -> str:
    """
    开仓通知：包含完整的【为什么开仓】逻辑 + 宏观快照
    v6: 增加宏观快照段落(BTC趋势/FGI/ETF流入)
    """
    d = "做多🟢" if trade["direction"] == "long" else "做空🔴"
    lev = trade.get("leverage", 3)
    
    signal_type = trade.get("signal_type", "")
    signal_reason = trade.get("signal_reason", "")
    strength = trade.get("signal_strength", "")
    tech = trade.get("tech_snapshot", {})
    
    # 策略逻辑解释
    logic_map = {
        "extreme_neg_funding": (
            "【极端负费率 → 逼空做多】\n"
            "逻辑: 费率极端深负=空头过多，空头持续出血→平仓买入→价格反弹\n"
            "确认: EMA趋势不能向下 + RSI不超65"
        ),
        "extreme_pos_funding": (
            "【极端正费率 → 多头拥挤做空】\n"
            "逻辑: 费率极端正=多头过度拥挤，多头持续出血→平仓卖出→价格回落\n"
            "确认: EMA趋势不能向上 + RSI不低于35"
        ),
        "crash_bounce": (
            "【暴跌反弹 → 超跌做多】\n"
            "逻辑: 24h暴跌>30%后企稳，恐慌抛售后空头回补→超跌反弹\n"
            "确认: 必须开始反弹(1h收阳) + EMA不持续向下"
        ),
        "pump_short": (
            "【暴涨回落 → 做空】\n"
            "逻辑: 24h暴涨>50%后从高点回落>8%，投机资金出逃→继续回调\n"
            "确认: EMA不持续向上 + RSI不超卖"
        ),
        "oi_surge": (
            "【OI异动 → 大资金跟单】\n"
            "逻辑: 持仓量暴增，大资金进场，价格同向确认方向"
        ),
        "funding_flip": (
            "【费率翻转 → 趋势信号】\n"
            "逻辑: 费率从正转负或负转正，市场情绪拐点确认"
        ),
        "funding_flip_neg": (
            "【费率翻转(正→负) → 做多】\n"
            "逻辑: 前2期正费率→最新1期负费率，空头开始占据主导，逼空概率增加\n"
            "确认: EMA不向下 + 成交量配合"
        ),
        "funding_flip_pos": (
            "【费率翻转(负→正) → 做多】\n"
            "逻辑: 前2期负费率→最新1期正费率，多头觉醒趋势确认\n"
            "确认: EMA向上 + RSI不过热"
        ),
    }
    
    strategy_logic = logic_map.get(signal_type, f"【{signal_type}】")
    
    entry = trade["entry_price"]
    sl = trade["stop_loss"]
    tp = trade["take_profit"]
    sl_pct = trade.get("signal_sl_pct", 3)
    tp_pct = trade.get("signal_tp_pct", 8)
    rr = trade.get("signal_rr", 0)
    
    # 止损逻辑说明
    atr_pct = tech.get("atr_pct", 0)
    if atr_pct > 0:
        sl_logic = f"ATR动态止损: ATR={atr_pct:.2f}% × 1.5倍 = SL{sl_pct:.1f}%"
    else:
        sl_logic = f"固定止损{sl_pct:.1f}%"
    
    if trade["direction"] == "long":
        sl_detail = f"跌破{sl} → 跌{sl_pct:.1f}% 说明反弹失败\n  {sl_logic}"
        tp_detail = f"涨到{tp} → 涨{tp_pct:.1f}% 反弹到位获利 (RR={rr:.1f})"
    else:
        sl_detail = f"涨破{sl} → 涨{sl_pct:.1f}% 说明空头力量不足\n  {sl_logic}"
        tp_detail = f"跌到{tp} → 跌{tp_pct:.1f}% 回调到位获利 (RR={rr:.1f})"
    
    # 技术指标详情
    tech_detail = (
        f"  EMA趋势: {tech.get('ema_trend', 'N/A')}\n"
        f"  RSI(14): {tech.get('rsi', 50):.1f}\n"
        f"  ATR(%): {atr_pct:.3f}%"
    )
    
    # ══════════════════════════════════
    # 宏观快照段落 (v6新增)
    # ══════════════════════════════════
    macro_section = ""
    try:
        macro = intel_quick_macro()
        if macro:
            # BTC趋势
            btc_trend = macro.get("btc_trend", "N/A")
            trend_emoji = {"up": "📈", "down": "📉", "sideways": "➡️"}.get(btc_trend, "❓")
            btc_price = macro.get("btc_price", "N/A")
            btc_change = macro.get("btc_24h_change", "N/A")
            
            # FGI
            fgi = macro.get("fgi", "N/A")
            fgi_label = macro.get("fgi_label", "N/A")
            
            # ETF/资金流入
            eth_inflow = macro.get("eth_inflow", "N/A")
            total_mcap = macro.get("total_mcap_change", "N/A")
            dominance = macro.get("dominance", "N/A")
            
            if isinstance(btc_change, (int, float)):
                btc_change_str = f"{btc_change:+.2f}%"
            else:
                btc_change_str = str(btc_change)
            
            macro_section = (
                f"\n"
                f"🌐 宏观快照:\n"
                f"  BTC: {trend_emoji} ${btc_price} ({btc_change_str}) | 趋势: {btc_trend}\n"
                f"  FGI: {fgi}/{fgi_label} | 市占: {dominance}%\n"
                f"  ETH净流入: {eth_inflow}M | 总市值: {total_mcap}%"
            )
    except Exception:
        macro_section = ""
    
    msg = (
        f"📊 ========== 开仓通知 ==========\n"
        f"#{trade['id']} | {trade['symbol']} | {d} | {lev}x\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"📌 策略逻辑:\n"
        f"{strategy_logic}\n"
        f"\n"
        f"📈 信号详情:\n"
        f"  强度: {strength}级\n"
        f"  {signal_reason}\n"
        f"\n"
        f"📉 技术指标:\n"
        f"{tech_detail}\n"
        f"{macro_section}"
        f"\n"
        f"\n"
        f"🔢 交易参数:\n"
        f"  入场价: {entry}\n"
        f"  仓位: {trade.get('position_usd', 0):.2f}U (×{lev}={trade.get('notional_usd', 0):.2f}U)\n"
        f"  止损: {sl} → {sl_detail}\n"
        f"  止盈: {tp} → {tp_detail}\n"
        f"  移动止盈: 盈利4%后启动，回撤2%锁定\n"
        f"\n"
        f"🌍 环境: {env_detail}\n"
        f"⏰ 时间: {trade['entry_time']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return msg


# ═══════════════════════════════════════
# 平仓通知 — 包含平仓逻辑 + 自动复盘
# ═══════════════════════════════════════

def format_close_message(trade: dict) -> str:
    """
    平仓通知：包含【为什么平仓】+ 盈亏分析
    v5: 增加移动止盈/时间止损说明
    """
    d = "做多" if trade["direction"] == "long" else "做空"
    pnl = trade.get("pnl_usd", 0)
    pnl_pct = trade.get("pnl_pct", 0)
    emoji = "✅盈" if pnl > 0 else "❌亏"
    
    exit_reason = trade.get("exit_reason", "")
    
    close_logic_map = {
        "止损": "价格触及止损位 → 判断错误，及时止损保护本金",
        "止盈": "价格到达目标位 → 策略验证成功，按计划获利了结",
        "时间止损(超48h)": "持仓超过48小时 → 信号时效已过，强制平仓",
    }
    # 匹配移动止盈
    if "移动止盈" in exit_reason:
        close_logic_map[exit_reason] = f"{exit_reason} → 利润回吐，锁定已有盈利"
    
    close_logic = close_logic_map.get(exit_reason, exit_reason)
    
    entry = trade.get("entry_price", 0)
    exit_p = trade.get("exit_price", 0)
    if entry > 0:
        raw_pct = (exit_p - entry) / entry * 100
        if trade["direction"] == "short":
            raw_pct = -raw_pct
        price_move = f"价格变动: {raw_pct:+.2f}% × {trade.get('leverage', 3)}x杠杆 = {pnl_pct:+.1f}%"
    else:
        price_move = ""
    
    # 持仓时长
    try:
        from datetime import datetime, timezone, timedelta
        TZ = timezone(timedelta(hours=8))
        entry_t = datetime.fromisoformat(trade["entry_time"])
        exit_t = datetime.fromisoformat(trade.get("exit_time", ""))
        hold_hours = (exit_t - entry_t).total_seconds() / 3600
        hold_info = f"  持仓时长: {hold_hours:.1f}小时\n"
    except:
        hold_info = ""
    
    msg = (
        f"{emoji} ========== 平仓通知 ==========\n"
        f"#{trade['id']} | {trade['symbol']} | {d}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"📌 平仓逻辑:\n"
        f"  {close_logic}\n"
        f"\n"
        f"💰 盈亏:\n"
        f"  入场: {entry} → 出场: {exit_p}\n"
        f"  {price_move}\n"
        f"  净盈亏: {pnl:+.2f}U ({pnl_pct:+.1f}%)\n"
        f"{hold_info}"
        f"\n"
        f"⏰ 平仓时间: {trade.get('exit_time', 'N/A')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return msg


def format_review_message(trade: dict) -> str:
    """
    交易复盘 — 每笔平仓后自动生成
    v6: 增加多时间框架评分、聪明钱分析、更精准的改进建议
    """
    pnl = trade.get("pnl_usd", 0)
    signal_type = trade.get("signal_type", "")
    strength = trade.get("signal_strength", "")
    reason = trade.get("exit_reason", "")
    tech = trade.get("tech_snapshot", {})
    symbol = trade.get("symbol", "BTCUSDT")

    # 盈亏归因分析
    if pnl > 0:
        if "止盈" in reason:
            verdict = "策略正确✅ 信号+趋势+RSI三重确认有效，完全按计划走"
        elif "移动止盈" in reason:
            verdict = "移动止盈保护了利润✅ 价格未到目标位但及时锁定了盈利"
        else:
            verdict = "小盈⚠️ 未到止盈就被平仓，可能止盈位太远或入场时机偏差"
    else:
        verdict_map = {
            "extreme_neg_funding": "负费率策略失败 — 市场极端持续超预期，空头没有回补，趋势向下",
            "extreme_pos_funding": "正费率策略失败 — 多头拥挤但持续加仓，价格没有回落，趋势向上",
            "crash_bounce": "反弹失败 — 下跌趋势未结束，接飞刀，EMA仍空头排列",
            "pump_short": "做空失败 — 暴涨惯性超预期，回调延迟，趋势仍向上",
            "oi_surge": "OI信号失效 — 大资金假突破或方向判断错误",
            "funding_flip": "翻转失败 — 费率翻转不等于价格翻转",
        }
        verdict = verdict_map.get(signal_type, "信号失效，市场环境变化")
        
        # 检查是否是时间止损
        if "时间止损" in reason:
            verdict = "信号时效性不足 — 48h内未达预期，说明入场时机或方向有误"

    # ══════════════════════════════════
    # 多时间框架评分 (v6新增)
    # ══════════════════════════════════
    tf_section = ""
    try:
        tf = get_tf_scores(symbol)
        if tf:
            tf_1h = tf.get("1h", {})
            tf_4h = tf.get("4h", {})
            summary = tf.get("summary", "")

            def trend_label(t: str) -> str:
                mapping = {"bullish": "看多📈", "bearish": "看空📉", "neutral": "中性➡️"}
                return mapping.get(t, t)

            score_1h = tf_1h.get("score", 0)
            score_4h = tf_4h.get("score", 0)
            score_1h_str = f"{score_1h:+d}" if isinstance(score_1h, int) else str(score_1h)
            score_4h_str = f"{score_4h:+d}" if isinstance(score_4h, int) else str(score_4h)

            tf_section = (
                f"\n"
                f"⏱ 多时间框架:\n"
                f"  1h: {trend_label(tf_1h.get('trend', 'N/A'))} (评分{score_1h_str}, RSI:{tf_1h.get('rsi', 'N/A')})\n"
                f"  4h: {trend_label(tf_4h.get('trend', 'N/A'))} (评分{score_4h_str}, RSI:{tf_4h.get('rsi', 'N/A')})\n"
                f"  总结: {summary}\n"
            )
    except Exception:
        tf_section = ""

    # ══════════════════════════════════
    # 聪明钱分析 (v6新增)
    # ══════════════════════════════════
    smart_money_section = ""
    try:
        sm = get_smart_money_analysis(symbol)
        if sm:
            participation = sm.get("participation", "unknown")
            part_emoji = {"strong": "🔥", "moderate": "⚡", "weak": "💤"}.get(participation, "❓")
            taker_buy = sm.get("taker_buy_ratio", "N/A")
            oi_change = sm.get("oi_change_24h", "N/A")
            liquidation = sm.get("liquidation_dominance", "N/A")
            interpretation = sm.get("interpretation", "")

            oi_str = ""
            if isinstance(oi_change, (int, float)):
                oi_str = f"OI{oi_change:+.1f}%"
            else:
                oi_str = f"OI{oi_change}"

            liq_emoji = {"longs": "多头清算💥", "shorts": "空头清算💥", "balanced": "均衡⚖️"}.get(liquidation, liquidation)

            smart_money_section = (
                f"\n"
                f"💰 聪明钱动态:\n"
                f"  参与度: {part_emoji} {participation} | {oi_str}\n"
                f"  Taker买入: {taker_buy}% | 清算倾向: {liq_emoji}\n"
                f"  解读: {interpretation}\n"
            )
    except Exception:
        smart_money_section = ""

    # 改进建议
    if pnl > 0 and "止盈" in reason:
        improve = "✅ 继续保持当前策略和仓位管理"
    elif pnl > 0 and "移动止盈" in reason:
        improve = "✅ 移动止盈有效保护利润，可考虑适当放宽触发阈值以获取更多利润"
    elif pnl > 0:
        improve = "✅ 考虑调整止盈/移动止盈参数，锁定更多利润"
    elif reason == "止损":
        tech_hint = ""
        ema = tech.get("ema_trend", "")
        rsi = tech.get("rsi", 50)
        if ema == "down" and trade["direction"] == "long":
            tech_hint = "⚠️ EMA趋势向下时做多风险大，应更严格过滤"
        if rsi > 60 and trade["direction"] == "long":
            tech_hint += " RSI偏高时做多容易追高"

        # 结合TF评分给出更精确建议
        try:
            tf = get_tf_scores(symbol)
            if tf:
                tf_1h = tf.get("1h", {})
                tf_4h = tf.get("4h", {})
                tf_1h_score = tf_1h.get("score", 0)
                tf_4h_score = tf_4h.get("score", 0)
                if isinstance(tf_1h_score, (int, float)) and isinstance(tf_4h_score, (int, float)):
                    if trade["direction"] == "long" and tf_4h_score < 0:
                        tech_hint += " 4h趋势向下，逆势做多需更确凿的反转信号"
                    elif trade["direction"] == "short" and tf_4h_score > 0:
                        tech_hint += " 4h趋势向上，逆势做空需更强的顶部确认"
        except Exception:
            pass

        improve = f"📌 检查止损位是否合理{tech_hint}，考虑更严格的环境过滤"
    else:
        improve = "📌 需要回测该策略的历史胜率，考虑降低仓位或暂时关闭"

    emoji = "📋" if pnl > 0 else "🔍"

    tech_summary = ""
    if tech:
        tech_summary = (
            f"\n"
            f"📉 技术指标(开仓时):\n"
            f"  EMA趋势: {tech.get('ema_trend', 'N/A')}\n"
            f"  RSI: {tech.get('rsi', 50):.1f}\n"
            f"  ATR%: {tech.get('atr_pct', 0):.3f}%\n"
        )

    msg = (
        f"{emoji} ========== 交易复盘 ==========\n"
        f"#{trade['id']} | {trade['symbol']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"📌 信号: [{strength}] {signal_type}\n"
        f"  {trade.get('signal_reason', '')}\n"
        f"{tech_summary}"
        f"{tf_section}"
        f"{smart_money_section}"
        f"\n"
        f"📊 归因分析:\n"
        f"  {verdict}\n"
        f"\n"
        f"💡 改进建议:\n"
        f"  {improve}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    # v8: 标签建议
    tag_suggestions = []
    pnl_val = trade.get("pnl_usd", 0) or 0
    reason = trade.get("exit_reason", "")
    sig_type = trade.get("signal_type", "")
    sig_strength = trade.get("signal_strength", "")

    if pnl_val > 0:
        if "止盈" in reason or "移动止盈" in reason:
            tag_suggestions.append("✅策略正确")
        elif "时间止损" in reason:
            tag_suggestions.append("✅保本出")
        else:
            tag_suggestions.append("✅盈利")
    elif pnl_val < 0:
        tag_suggestions.append(f"❌{sig_type}({sig_strength})")
        if "止损" in reason:
            tag_suggestions.append("⛔止损")
    else:
        tag_suggestions.append("➖平保")

    # 自动标签: 信号类型分类
    type_tags = {
        "extreme_neg_funding": "费率做多", "extreme_pos_funding": "费率做空",
        "crash_bounce": "暴跌反弹", "pump_short": "暴涨回落",
        "oi_surge": "OI异动", "funding_flip_neg": "费率翻转做多",
        "funding_flip_pos": "费率翻转做多",
    }
    tag_suggestions.append(type_tags.get(sig_type, sig_type))

    trade_id = trade.get("id", "")

    msg += f"\n🏷️ {' '.join(tag_suggestions)}"
    msg += f"\n💬 /tag {trade_id} <标签> 手动打标签"
    msg += f"\n📝 /note {trade_id} <笔记> 写复盘笔记"

    return msg


def send_open_notification(trade: dict, env_detail: str = ""):
    """发送开仓通知到微信"""
    msg = format_open_message(trade, env_detail)
    return notify(msg)


def send_close_and_review(trade: dict):
    """发送平仓通知 + 复盘到微信"""
    close_msg = format_close_message(trade)
    review_msg = format_review_message(trade)
    notify(close_msg)
    notify(review_msg)
