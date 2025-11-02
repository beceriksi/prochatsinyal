import os, time, math, requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MEXC_FAPI = "https://contract.mexc.com"

# ---------- Utils ----------
def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def get_json(url, params=None, retries=3, timeout=10):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            time.sleep(1)
    return None

def telegram_send(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram bilgisi eksik, mesaj:\n", text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except:
        print("Telegram gönderim hatası.")

# ---------- Data ----------
def futures_symbols():
    data = get_json(f"{MEXC_FAPI}/api/v1/contract/detail")
    out = []
    if not data or "data" not in data:
        return out
    for s in data["data"]:
        if s.get("quoteCoin") == "USDT":
            out.append(s["symbol"])
    return out

def klines(symbol, interval="1h", limit=200):
    data = get_json(f"{MEXC_FAPI}/api/v1/contract/kline/{symbol}", {"interval": interval, "limit": limit})
    if not data or "data" not in data:
        return None
    df = pd.DataFrame(data["data"])
    df.columns = ["timestamp", "open", "high", "low", "close", "volume", "turnover"]
    df = df.astype({"open":"float64","high":"float64","low":"float64","close":"float64","volume":"float64"})
    return df

# ---------- Indicators ----------
def rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
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
    return macd_line, signal, macd_line - signal

# ---------- SMC ----------
def break_of_structure_up(df, lookback=40, exclude_last=2):
    hh = df['high'][:-exclude_last].tail(lookback).max()
    return df['close'].iloc[-1] > hh

def break_of_structure_down(df, lookback=40, exclude_last=2):
    ll = df['low'][:-exclude_last].tail(lookback).min()
    return df['close'].iloc[-1] < ll

def last_down_candle_before_bos(df, lookback=50):
    sub = df.tail(lookback)
    for i in range(len(sub)-2, 0, -1):
        if sub['close'].iloc[i] < sub['open'].iloc[i]:
            row = sub.iloc[i]
            return float(row['low']), float(max(row['open'], row['close']))
    return None

def last_up_candle_before_bos(df, lookback=50):
    sub = df.tail(lookback)
    for i in range(len(sub)-2, 0, -1):
        if sub['close'].iloc[i] > sub['open'].iloc[i]:
            row = sub.iloc[i]
            return float(min(row['open'], row['close'])), float(row['high'])
    return None

def within_zone(price, zone, tol=0.015):
    if not zone: return False
    low, high = zone
    pad = (high - low) * tol
    return (price >= low - pad) and (price <= high + pad)

def volume_spike(df, avg_n=20, spike_ratio=2.5):
    if len(df) < avg_n + 2: return False, 0.0
    last_vol = df['volume'].iloc[-1]
    base = df['volume'].iloc[-(avg_n+1):-1].mean()
    if base <= 0: return False, 0.0
    ratio = last_vol / base
    return ratio >= spike_ratio, ratio

# ---------- Analysis ----------
VOL_PARAMS = {"1h":(20,2.5), "4h":(20,2.5), "1d":(20,2.0)}

def analyze_symbol(sym):
    df4h = klines(sym, "4h", 300)
    if df4h is None or len(df4h) < 120:
        return None

    close = df4h['close']
    r = rsi(close, 14).iloc[-1]
    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    m, s, _ = macd(close, 12, 26, 9)
    macd_dir = "Yukarı" if m.iloc[-1] > s.iloc[-1] else "Aşağı"

    bos_up = break_of_structure_up(df4h, 40, 2)
    bos_dn = break_of_structure_down(df4h, 40, 2)
    demand = last_down_candle_before_bos(df4h, 60)
    supply = last_up_candle_before_bos(df4h, 60)
    last_price = float(close.iloc[-1])

    near_demand = within_zone(last_price, demand, 0.015)
    near_supply = within_zone(last_price, supply, 0.015)

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

    buy = bos_up and near_demand
    sell = bos_dn and near_supply
    if not (buy or sell):
        return None

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

# ---------- Main ----------
def main():
    syms = futures_symbols()
    if not syms:
        telegram_send("⚠️ *Sembol listesi alınamadı!* MEXC API erişimi başarısız.")
        return

    telegram_send(f"✅ {len(syms)} sembol bulundu, tarama başlıyor...")

    signals = []
    for i, s in enumerate(syms):
        try:
            out = analyze_symbol(s)
            if out:
                signals.append(out)
        except Exception:
            pass
        if i % 10 == 0:
            time.sleep(0.25)

    if not signals:
        telegram_send("ℹ️ Şu an aktif sinyal yok.")
        return

    for m in signals[:10]:
        telegram_send(m)
        time.sleep(0.2)

if __name__ == "__main__":
    main()
