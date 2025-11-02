import os, time, requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MEXC_FAPI = "https://contract.mexc.com"

# ----- utils -----
def ts(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
def get_json(url, params=None, retries=3, timeout=10):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200: return r.json()
        except: time.sleep(1)
    return None

def telegram_send(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram yok:\n", text); return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except: print("Telegram gönderim hatası.")

def klines(symbol, interval, limit=300):
    data = get_json(f"{MEXC_FAPI}/api/v1/contract/kline/{symbol}", {"interval": interval, "limit": limit})
    if not data or "data" not in data: return None
    df = pd.DataFrame(data["data"], columns=["ts","open","high","low","close","vol","turnover"])
    df = df.astype({"open":"float64","high":"float64","low":"float64","close":"float64","vol":"float64"})
    return df

# ----- indicators -----
def ema(x, n): return x.ewm(span=n, adjust=False).mean()
def rsi(s, n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    rs=up.ewm(alpha=1/n, adjust=False).mean()/(dn.ewm(alpha=1/n, adjust=False).mean()+1e-12)
    return 100-(100/(1+rs))
def macd(s, f=12, m=26, sig=9):
    fast=ema(s,f); slow=ema(s,m); line=fast-slow; signal=line.ewm(span=sig, adjust=False).mean()
    return line, signal
def atr(df, n=14):
    tr = np.maximum(df['high']-df['low'], np.maximum(abs(df['high']-df['close'].shift()), abs(df['low']-df['close'].shift())))
    return pd.Series(tr).ewm(span=n, adjust=False).mean()

# ----- smc -----
def bos_up(df, look=40, excl=2):
    hh = df['high'][:-excl].tail(look).max()
    return df['close'].iloc[-1] > hh
def bos_dn(df, look=40, excl=2):
    ll = df['low'][:-excl].tail(look).min()
    return df['close'].iloc[-1] < ll

def brief_for(df):
    c = df['close']
    e20, e50 = ema(c,20).iloc[-1], ema(c,50).iloc[-1]
    trend = "↑" if e20>e50 else ("↓" if e20<e50 else "=")
    r = float(rsi(c,14).iloc[-1])
    m, s = macd(c)
    macd_dir = "↑" if m.iloc[-1]>s.iloc[-1] else "↓"
    vu = df['vol'].iloc[-1] / (df['vol'].iloc[-21:-1].mean()+1e-12)
    vu_txt = f"x{vu:.2f}"
    bos = "BoS↑" if bos_up(df) else ("BoS↓" if bos_dn(df) else "-")
    volat = atr(df).iloc[-1] / (c.iloc[-1]+1e-12) * 100
    return trend, r, macd_dir, bos, vu_txt, float(c.iloc[-1]), float(volat)

def classify(tf_data):
    # tf_data: list of tuples from brief_for
    pos = sum([ (t[0]=="↑") + (t[1]>50) + (t[2]=="↑") + (t[3]=="BoS↑") for t in tf_data ])
    neg = sum([ (t[0]=="↓") + (t[1]<50) + (t[2]=="↓") + (t[3]=="BoS↓") for t in tf_data ])
    if pos - neg >= 2: return "GÜÇLÜ"
    if neg - pos >= 2: return "ZAYIF"
    return "NÖTR"

def pack(name, d1, d4, dH):
    lines = []
    for label, d in [("1D", d1), ("4H", d4), ("1H", dH)]:
        trend, r, macd_dir, bos, vu, px, vol = d
        lines.append(f"{label}: Trend {trend} | RSI {r:.1f} | MACD {macd_dir} | {bos} | Hacim {vu} | ATR% {vol:.2f} | Fiyat {px}")
    cls = classify([d1,d4,dH])
    hdr = f"*{name}* → *{cls}*"
    return hdr + "\n" + "\n".join(lines)

def main():
    # MEXC futures sembol adları:
    pairs = [("BTC","BTC_USDT"), ("ETH","ETH_USDT")]
    interval_map = {"1d":"1d", "4h":"4h", "1h":"1h"}  # MEXC interval adları bu şekilde çalışır.

    out = [f"⏱ {ts()} — *BTC/ETH Piyasa Özeti*"]
    for name, sym in pairs:
        d1 = klines(sym, interval_map["1d"], 300)
        d4 = klines(sym, interval_map["4h"], 400)
        dH = klines(sym, interval_map["1h"], 400)
        if d1 is None or d4 is None or dH is None:
            out.append(f"*{name}*: veri alınamadı.")
            continue
        b1 = brief_for(d1); b4 = brief_for(d4); bH = brief_for(dH)
        out.append(pack(name, b1, b4, bH))

    telegram_send("\n\n".join(out))

if __name__ == "__main__":
    main()
