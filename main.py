import os, time, math, requests, statistics
import pandas as pd
import numpy as np
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BINANCE_FAPI = "https://fapi.binance.com"

# ---------- Utils ----------
def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def get(url, params=None, retries=3, timeout=15):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(0.5*(i+1))
    return None

def telegram_send(text):
    if not TELEGRAM_TOKEN or not CHAT_ID: 
        print("Telegram env eksik, mesaj:\n", text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})

def klines(symbol, interval, limit=300):
    data = get(f"{BINANCE_FAPI}/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    if not data: return None
    cols = ["open_time","open","high","low","close","volume","close_time","qav","trades","tbbav","tbqav","ignore"]
    df = pd.DataFrame(data, columns=cols)
    df = df.astype({"open":"float64","high":"float64","low":"float64","close":"float64","volume":"float64"})
    return df

def rsi(series, period=14):
    delta = series.diff()
    up   = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ema_up = up.ewm(alpha=1/period, adjust=False).mean()
    ema_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = ema_up / (ema_down + 1e-12)
    return 100 - (100 / (1 + rs))

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def macd(series, f=12, s=26, sig=9):
    fast = ema(series, f)
    slow = ema(series, s)
    macd_line = fast - slow
    signal = macd_line.ewm(span=sig, adjust=False).mean()
    hist = macd_line - signal
    return macd_line, signal, hist

# ---------- SMC helpers ----------
def break_of_structure_up(df, lookback=40, exclude_last=2):
    hh = df['high'][:-exclude_last].tail(lookback).max()
    return df['close'].iloc[-1] > hh

def break_of_structure_down(df, lookback=40, exclude_last=2):
    ll = df['low'][:-exclude_last].tail(lookback).min()
    return df['close'].iloc[-1] < ll

def last_down_candle_before_bos(df, lookback=50):
    # Demand zone candidate: last bearish candle prior to last significant up close
    sub = df.tail(lookback)
    idx = None
    for i in range(len(sub)-2, 0, -1):
        if sub['close'].iloc[i] < sub['open'].iloc[i]:
            idx = sub.index[i]
            break
    if idx is None: return None
    row = df.loc[idx]
    # Demand zone = [low, high] of that down candle
    return float(row['low']), float(max(row['open'], row['close']))

def last_up_candle_before_bos(df, lookback=50):
    # Supply zone candidate: last bullish candle prior to last significant down close
    sub = df.tail(lookback)
    idx = None
    for i in range(len(sub)-2, 0, -1):
        if sub['close'].iloc[i] > sub['open'].iloc[i]:
            idx = sub.index[i]
            break
    if idx is None: return None
    row = df.loc[idx]
    # Supply zone = [low, high] of that up candle
    return float(min(row['open'], row['close'])), float(row['high'])

def within_zone(price, zone, tol=0.015):
    if not zone: return False
    low, high = zone
    # geniş tolerans: zonun %1.5 üst/alt tamponu
    pad = (high - low) * tol if high > low else price * tol
    return (price >= low - pad) and (price <= high + pad)

# ---------- Volume Spike ----------
def volume_spike(df, avg_n=20, spike_ratio=2.5):
    if len(df) < avg_n + 2: return False, 0.0
    last_vol = df['volume'].iloc[-1]
    base = df['volume'].iloc[-(avg_n+1):-1].mean()
    if base <= 0: return False, 0.0
    ratio = last_vol / base
    return ratio >= spike_ratio, ratio

# ---------- Symbol universe ----------
def futures_usdt_perp_symbols():
    info = get(f"{BINANCE_FAPI}/fapi/v1/exchangeInfo")
    out = []
    if not info: return out
    for s in info.get("symbols", []):
        if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
            out.append(s["symbol"])
    return out

# ---------- Scan logic ----------
INTERVALS = ["1h","4h","1d"]
VOL_PARAMS = {"1h":(20,2.5), "4h":(20,2.5), "1d":(20,2.0)}  # günlükte spike eşiği biraz daha düşük

def analyze_symbol(sym):
    # ana zaman dilimi 4H üstünden SMC kararı, 1H/4H/1D hacim teyidi
    df4h = klines(sym, "4h", 300)
    if df4h is None or len(df4h) < 120: 
        return None

    # indikatörler (yorum)
    close = df4h['close']
    r = rsi(close, 14).iloc[-1]
    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    m, s, h = macd(close, 12, 26, 9)
    macd_dir = "Yukarı" if m.iloc[-1] > s.iloc[-1] else "Aşağı"

    # SMC BoS
    bos_up = break_of_structure_up(df4h, 40, 2)
    bos_dn = break_of_structure_down(df4h, 40, 2)

    # Zonlar
    demand = last_down_candle_before_bos(df4h, 60)
    supply = last_up_candle_before_bos(df4h, 60)
    last_price = float(close.iloc[-1])

    near_demand = within_zone(last_price, demand, 0.015)
    near_supply = within_zone(last_price, supply, 0.015)

    # Hacim spike taraması (1H/4H/1D)
    vol_flags = []
    for iv in ["1h","4h","1d"]:
        dfi = klines(sym, iv, 240 if iv!="1d" else 400)
        if dfi is None or len(dfi) < 50: 
            continue
        spk, ratio = volume_spike(dfi, *VOL_PARAMS[iv])
        if spk:
            vol_flags.append((iv, round(ratio,2)))

    if not vol_flags:
        return None

    # Sinyal kuralları
    buy = bos_up and near_demand
    sell = bos_dn and near_supply
    if not (buy or sell):
        return None

    # Yorum oluştur
    ema_trend = "EMA20>EMA50" if ema20 > ema50 else "EMA20<EMA50"
    vol_txt = ", ".join([f"{iv} x{rt}" for iv,rt in vol_flags])

    side = "BUY" if buy else "SELL"
    msg = (
        f"🟢 *{side} SİNYALİ* — `{sym}`\n"
        f"Fiyat: `{round(last_price,6)}`  | Zaman: {ts()}\n"
        f"• Hacim Spike: {vol_txt}\n"
        f"• Market Structure: {'BoS↑' if bos_up else ('BoS↓' if bos_dn else '-')}\n"
        f"• Bölge: {'Demand' if buy else 'Supply'} (yakında)\n"
        f"• RSI(14): `{round(r,2)}`  | MACD: *{macd_dir}*  | {ema_trend}\n"
        f"— Not: RSI/MACD/EMA *yorum amaçlıdır*; ana sistem SMC+Hacim."
    )
    return msg

def main():
    syms = futures_usdt_perp_symbols()
    if not syms:
        telegram_send("⚠️ Sembol listesi alınamadı.")
        return

    signals = []
    for i, s in enumerate(syms):
        try:
            out = analyze_symbol(s)
            if out:
                signals.append(out)
        except Exception as e:
            # Sessiz geç; hız için log kısaltıldı
            pass
        # nazik rate limit
        if i % 10 == 0:
            time.sleep(0.25)

    if not signals:
        print("Sinyal yok.")
        return

    # Spam koruma: en fazla 10 mesaj
    for m in signals[:10]:
        telegram_send(m)
        time.sleep(0.2)

if __name__ == "__main__":
    main()
