#!/usr/bin/env python3
import requests, time, math, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BASE='https://fapi.binance.com'
TZ=timezone(timedelta(hours=8))
START=datetime(2026,5,14,0,0,0,tzinfo=TZ)
END=datetime(2026,6,4,0,0,0,tzinfo=TZ)
SYMBOLS=["XAGUSDT","XAUUSDT","LABUSDT","SUIUSDT","XRPUSDT","BUSDT","CRCLUSDT","BILLUSDT","BNBUSDT","SNDKUSDT","TONUSDT","GTCUSDT","1000PEPEUSDT","SKYAIUSDT","VVVUSDT","SAGAUSDT","MUUSDT","ADAUSDT","INTCUSDT","LDOUSDT","AVAXUSDT","LINKUSDT","PAXGUSDT","AAVEUSDT"]
OI_SURGE=5.0; MIN_OI=2_000_000
FEE=0.0004; SLIP=0.0005
MARGIN=50.0; LEV=3.0; NOTIONAL=MARGIN*LEV
DEFAULT_SL=0.05; ATR_MULT=1.5; MAX_SL=0.10; RR=2.5; MAX_HOLD=24

def ms(dt): return int(dt.timestamp()*1000)
def get(path, params):
    for i in range(6):
        r=requests.get(BASE+path, params=params, timeout=30)
        if r.status_code==200: return r.json()
        time.sleep(1+i)
    raise RuntimeError((path,params,r.status_code,r.text[:200]))

def fetch_klines(sym):
    out=[]; cur=ms(START-timedelta(days=3)); end=ms(END+timedelta(days=2))
    while cur<end:
        data=get('/fapi/v1/klines', {'symbol':sym,'interval':'1h','startTime':cur,'endTime':end,'limit':1500})
        if not data: break
        out.extend(data); nxt=data[-1][0]+3600_000
        if nxt<=cur: break
        cur=nxt
        if len(data)<1500: break
    rows=[]
    for k in out:
        rows.append({'t':int(k[0]),'open':float(k[1]),'high':float(k[2]),'low':float(k[3]),'close':float(k[4])})
    # unique
    dd={r['t']:r for r in rows}
    return [dd[k] for k in sorted(dd)]

def fetch_oi(sym):
    # Binance only exposes latest ~1 month; limit=500 gives latest ~20.8 days for 1h.
    data=get('/futures/data/openInterestHist', {'symbol':sym,'period':'1h','limit':500})
    dd={int(x['timestamp']):float(x.get('sumOpenInterestValue') or 0) for x in (data or [])}
    return dd

def atr_pct(bars, i, n=14):
    if i<n+1: return DEFAULT_SL
    trs=[]
    for j in range(i-n+1,i+1):
        h,l=bars[j]['high'],bars[j]['low']; pc=bars[j-1]['close']
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    atr=sum(trs)/len(trs)
    return atr / bars[i]['close'] if bars[i]['close']>0 else DEFAULT_SL

def simulate_trade(bars, entry_i, direction, sl_pct):
    entry=bars[entry_i]['open']
    if direction=='long':
        sl=entry*(1-sl_pct); tp=entry*(1+sl_pct*RR)
    else:
        sl=entry*(1+sl_pct); tp=entry*(1-sl_pct*RR)
    exit_price=bars[min(entry_i+MAX_HOLD, len(bars)-1)]['close']; reason='max_hold'
    exit_i=min(entry_i+MAX_HOLD, len(bars)-1)
    for j in range(entry_i, min(entry_i+MAX_HOLD, len(bars)-1)+1):
        h,l=bars[j]['high'],bars[j]['low']
        if direction=='long':
            hit_sl=l<=sl; hit_tp=h>=tp
            if hit_sl: exit_price=sl; reason='stop_loss'; exit_i=j; break
            if hit_tp: exit_price=tp; reason='take_profit'; exit_i=j; break
        else:
            hit_sl=h>=sl; hit_tp=l<=tp
            if hit_sl: exit_price=sl; reason='stop_loss'; exit_i=j; break
            if hit_tp: exit_price=tp; reason='take_profit'; exit_i=j; break
    raw = (exit_price-entry)/entry if direction=='long' else (entry-exit_price)/entry
    pnl=raw*NOTIONAL - NOTIONAL*2*(FEE+SLIP)
    return exit_i, exit_price, reason, pnl

def summarize(trades):
    bal=1000; peak=bal; dd=0; wins=0; gp=gl=0; monthly=defaultdict(float)
    for t in sorted(trades,key=lambda x:x['entry_time']):
        p=t['pnl']; bal+=p; peak=max(peak,bal); dd=max(dd,(peak-bal)/peak*100)
        if p>0: wins+=1; gp+=p
        else: gl+=-p
        monthly[t['entry_time'][:7]]+=p
    n=len(trades)
    return {'trades':n,'wins':wins,'win_rate':round(wins/n*100,2) if n else 0,'pnl':round(sum(t['pnl'] for t in trades),2),'dd':round(dd,2),'pf':round(gp/gl,3) if gl else None,'roi_dd':round((sum(t['pnl'] for t in trades)/10)/dd,3) if dd else None,'monthly':{k:round(v,2) for k,v in sorted(monthly.items())}}

def main():
    all_tr=[]
    bysym={}
    for idx,sym in enumerate(SYMBOLS,1):
        print('fetch',idx,len(SYMBOLS),sym, flush=True)
        try:
            bars=fetch_klines(sym); oi=fetch_oi(sym)
        except Exception as e:
            print('ERR',sym,e); continue
        t_to_i={b['t']:i for i,b in enumerate(bars)}
        trades=[]; last_exit=-1
        for i,b in enumerate(bars):
            if b['t']<ms(START) or b['t']>ms(END): continue
            if i<25 or i+1>=len(bars) or i<=last_exit: continue
            cur=oi.get(b['t']); prev=oi.get(bars[i-1]['t'])
            if not cur or not prev or prev<=0 or cur<MIN_OI: continue
            chg=(cur-prev)/prev*100
            if chg<OI_SURGE: continue
            price_chg_24=(bars[i]['close']-bars[i-24]['close'])/bars[i-24]['close']*100 if i>=24 else 0
            direction='long' if price_chg_24>0 else 'short'
            sl=min(max(atr_pct(bars,i)*ATR_MULT, DEFAULT_SL), MAX_SL)
            exit_i, xp, reason, pnl=simulate_trade(bars, i+1, direction, sl)
            last_exit=exit_i
            tr={'symbol':sym,'direction':direction,'entry_time':datetime.fromtimestamp(bars[i+1]['t']/1000,TZ).isoformat(),'oi_chg':chg,'price_chg_24':price_chg_24,'sl_pct':sl,'exit_reason':reason,'pnl':pnl}
            trades.append(tr); all_tr.append(tr)
        bysym[sym]=summarize(trades)
        print(sym, bysym[sym], flush=True)
        time.sleep(0.1)
    out={'window':{'start':START.isoformat(),'end':END.isoformat()},'params':{'oi_surge_pct':OI_SURGE,'min_oi_usd':MIN_OI,'margin':MARGIN,'leverage':LEV,'sl':'max(ATR14*1.5,5%) capped 10%','tp_rr':RR,'max_hold_h':MAX_HOLD},'summary':summarize(all_tr),'by_symbol':bysym,'trades':all_tr}
    print('\nTOTAL',json.dumps(out['summary'],ensure_ascii=False,indent=2))
    open('/tmp/m40_oi_core_backtest.json','w').write(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
