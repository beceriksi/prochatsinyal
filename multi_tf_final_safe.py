import os, time, requests, pandas as pd, numpy as np
from datetime import datetime, timezone

# === Ayarlar ===
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN")
CHAT_ID=os.getenv("CHAT_ID")
MEXC_BASE="https://futures.mexc.com"
BINANCE="https://api.binance.com"

def ts(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

# --- Yardımcı ---
def jget(url, params=None, retries=4, timeout=10):
    for _ in range(retries):
        try:
            r=requests.get(url, params=params, timeout=timeout)
            if r.status_code==200:
                j=r.json()
                if "data" in j and j["data"]:
                    return j
        except: time.sleep(0.5)
    return None

def telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(msg); return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id":CHAT_ID,"text":msg,"parse_mode":"Markdown"})
    except: pass

# --- İndikatörler ---
def ema(x,n): return x.ewm(span=n, adjust=False).mean()
def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    rs=up.ewm(alpha=1/n, adjust=False).mean()/(dn.ewm(alpha=1/n, adjust=False).mean()+1e-12)
    return 100-(100/(1+rs))
def adx(df,n=14):
    up=df['high'].diff(); dn=-df['low'].diff()
    plus=np.where((up>dn)&(up>0),up,0.0); minus=np.where((dn>up)&(dn>0),dn,0.0)
    tr1=df['high']-df['low']; tr2=(df['high']-df['close'].shift()).abs(); tr3=(df['low']-df['close'].shift()).abs()
    tr=pd.DataFrame({'a':tr1,'b':tr2,'c':tr3}).max(axis=1)
    atr=tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di=100*pd.Series(plus).ewm(alpha=1/n, adjust=False).mean()/(atr+1e-12)
    minus_di=100*pd.Series(minus).ewm(alpha=1/n, adjust=False).mean()/(atr+1e-12)
    dx=((plus_di-minus_di).abs()/((plus_di+minus_di)+1e-12))*100
    return dx.ewm(alpha=1/n, adjust=False).mean()

def volume_spike(df,n=10,r=1.10):
    t=df['turnover']
    base=t.ewm(span=n, adjust=False).mean()
    ratio=float(t.iloc[-1]/(base.iloc[-2]+1e-12))
    return ratio>=r,ratio

# --- Coin Listesi ---
def mexc_symbols():
    urls=[
        "https://api.mexc.com/api/v1/contract/detail",
        "https://futures.mexc.com/api/v1/private/contract/list",
        "https://contract.mexc.com/api/v1/contract/detail"
    ]
    for u in urls:
        d=jget(u)
        if d and "data" in d and len(d["data"])>5:
            data=[x for x in d["data"] if x.get("quoteCoin")=="USDT"]
            return [c["symbol"] for c in data[:150]]
    return []

def binance_symbols():
    d=jget(f"{BINANCE}/api/v3/exchangeInfo")
    if not d or "symbols" not in d: return []
    pairs=[s["symbol"] for s in d["symbols"] if s.get("quoteAsset")=="USDT" and s.get("status")=="TRADING"]
    return [p for p in pairs if p.endswith("USDT")][:150]

# --- Fiyat verisi ---
def klines(sym,interval="1h",limit=120,use_binance=False):
    if use_binance:
        d=jget(f"{BINANCE}/api/v3/klines",{"symbol":sym,"interval":interval,"limit":limit})
        if not d: return None
        try:
            df=pd.DataFrame(d,columns=["t","open","high","low","close","v","ct","qv","trades","tb","tq","ig"]).astype(float)
            df.rename(columns={"close":"c","v":"turnover"},inplace=True)
            return df
        except: return None
    else:
        d=jget(f"{MEXC_BASE}/api/v1/contract/kline/{sym}",{"interval":interval,"limit":limit})
        if not d or "data" not in d: return None
        try:
            df=pd.DataFrame(d["data"],columns=["ts","open","high","low","close","volume","turnover"]).astype(float)
            return df
        except: return None

# --- Analiz ---
def analyze(sym,interval,use_binance=False):
    df=klines(sym,interval,use_binance=use_binance)
    if df is None or len(df)<60: return None
    if df["turnover"].iloc[-1]<200_000: return None
    c,h,l=df['close'],df['high'],df['low']
    rr=float(rsi(c).iloc[-1]); e20,e50=ema(c,20).iloc[-1],ema(c,50).iloc[-1]
    trend_up=e20>e50
    v_ok,ratio=volume_spike(df)
    if not v_ok: return None
    if trend_up and rr>50: side="BUY"
    elif not trend_up and rr<50: side="SELL"
    else: return None
    a=float(adx(pd.DataFrame({'high':h,'low':l,'close':c}),14).iloc[-1])
    return f"{sym} | {interval.upper()} | {side} | RSI:{rr:.1f} | ADX:{a:.0f} | Hacim x{ratio:.2f}"

# --- Ana ---
def main():
    syms=mexc_symbols()
    use_binance=False
    if not syms:
        syms=binance_symbols()
        use_binance=True
        telegram("⚠️ MEXC yanıtı boş — Binance verisiyle devam ediliyor.")
    if not syms:
        telegram("⛔ Hiç sembol alınamadı (MEXC & Binance).")
        return

    signals=[]
    for s in syms:
        for tf in ["1h","4h","1d"]:
            try:
                res=analyze(s,tf,use_binance)
                if res: signals.append(res)
            except: pass
        time.sleep(0.03)

    if signals:
        msg=f"⚡ *Multi-Timeframe Sinyaller*\n⏱ {ts()}\nVeri: {'Binance' if use_binance else 'MEXC'}\n\n"+"\n".join(signals[:70])
        telegram(msg)
    else:
        print("ℹ️ sinyal yok (sessiz).")

if __name__=="__main__":
    main()
