import os, time, requests, pandas as pd, numpy as np
from datetime import datetime, timezone
import ccxt

# ==============================
#   Telegram Ayarları
# ==============================
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN")
CHAT_ID=os.getenv("CHAT_ID")

MEXC_API = "https://api.mexc.com"
BINANCE_API = "https://api.binance.com"


def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ========================================================
#   Yardımcı Fonksiyonlar
# ========================================================
def jget(url, params=None, retries=3, timeout=10):
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except:
            time.sleep(0.4)
    return None


def telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        )
    except:
        pass


# ========================================================
#   Göstergeler
# ========================================================
def ema(x, n):
    return x.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / (dn.ewm(alpha=1/n, adjust=False).mean() + 1e-12)
    return 100 - (100 / (1 + rs))

def adx(df, n=14):
    up = df['high'].diff()
    dn = -df['low'].diff()

    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)

    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift()).abs()
    tr3 = (df['low'] - df['close'].shift()).abs()

    tr = pd.DataFrame({"a": tr1, "b": tr2, "c": tr3}).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()

    plus_di = 100 * pd.Series(plus).ewm(alpha=1/n, adjust=False).mean() / (atr + 1e-12)
    minus_di = 100 * pd.Series(minus).ewm(alpha=1/n, adjust=False).mean() / (atr + 1e-12)

    dx = ((plus_di - minus_di).abs() / ((plus_di + minus_di) + 1e-12)) * 100
    return dx.ewm(alpha=1/n, adjust=False).mean()

def volume_spike(df, n=10, r=1.10):
    t = df["turnover"]
    base = t.ewm(span=n, adjust=False).mean()
    ratio = float(t.iloc[-1] / (base.iloc[-2] + 1e-12))
    return ratio >= r, ratio


# ========================================================
# ✅ COIN LİSTELEME (1. KOD MANTIĞI – %100 DOĞRU)
# ========================================================
def mexc_coin_list(limit=400):
    try:
        ex = ccxt.mexc({"enableRateLimit": True})
        ex.load_markets()

        syms = []
        for s, m in ex.markets.items():
            if m.get("active") and m.get("spot") and m.get("quote") == "USDT":
                syms.append(s)

        volmap = {}
        d = jget(f"{MEXC_API}/api/v3/ticker/24hr")
        if d:
            for x in d:
                sym = x.get("symbol", "")
                if sym in syms:
                    volmap[sym] = float(x.get("quoteVolume", 0))

        syms = sorted(syms, key=lambda x: volmap.get(x, 0), reverse=True)

        return syms[:limit]

    except Exception as e:
        print("MEXC coin fetch error:", e)
        return []


def binance_coin_list(limit=400):
    d = jget(f"{BINANCE_API}/api/v3/exchangeInfo")
    if not d or "symbols" not in d:
        return []

    syms = [
        s["symbol"]
        for s in d["symbols"]
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
    ]

    return syms[:limit]


# ========================================================
#   Kline Verisi
# ========================================================
def klines(sym, interval="1h", limit=120, binance=False):
    if binance:
        d = jget(f"{BINANCE_API}/api/v3/klines", {"symbol": sym, "interval": interval, "limit": limit})
        if not d:
            return None
        try:
            df = pd.DataFrame(
                d, columns=["t","o","h","l","c","v","ct","qv","trades","tb","tq","ig"]
            ).astype(float)
            df.rename(columns={"c": "close", "h": "high", "l": "low", "v": "turnover"}, inplace=True)
            return df
        except:
            return None
    else:
        d = jget(f"{MEXC_API}/api/v3/klines", {"symbol": sym, "interval": interval, "limit": limit})
        if not d:
            return None
        try:
            df = pd.DataFrame(
                d, columns=["t","o","h","l","c","v","qv","n","t1","t2","ig","ib"]
            ).astype(float)
            df.rename(columns={"c": "close", "h": "high", "l": "low", "qv": "turnover"}, inplace=True)
            return df
        except:
            return None


# ========================================================
#   Analiz
# ========================================================
def analyze(sym, interval, binance=False):
    df = klines(sym, interval, binance=binance)
    if df is None or len(df) < 60:
        return None

    if df["turnover"].iloc[-1] < 150_000:
        return None

    c = df["close"]
    h = df["high"]
    l = df["low"]

    rr = float(rsi(c).iloc[-1])
    e20 = ema(c, 20).iloc[-1]
    e50 = ema(c, 50).iloc[-1]
    trend_up = e20 > e50

    v_ok, ratio = volume_spike(df)
    if not v_ok:
        return None

    if trend_up and rr > 50:
        side = "BUY"
    elif (not trend_up) and rr < 50:
        side = "SELL"
    else:
        return None

    a = float(adx(pd.DataFrame({"high": h, "low": l, "close": c}), 14).iloc[-1])

    return f"{sym} | {interval.upper()} | {side} | RSI:{rr:.1f} | ADX:{a:.0f} | Hacim x{ratio:.2f}"


# ========================================================
#   ANA FONKSİYON
# ========================================================
def main():
    syms = mexc_coin_list()

    binance_mode = False

    if not syms:
        syms = binance_coin_list()
        binance_mode = True
        telegram("⚠️ MEXC Spot çalışmıyor → Binance Spot ile devam ediliyor.")

    if not syms:
        telegram("⛔ Coin listesi alınamadı.")
        return

    signals = []

    for s in syms:
        for tf in ["1h", "4h", "1d"]:
            try:
                res = analyze(s, tf, binance_mode)
                if res:
                    signals.append(res)
            except:
                pass
        time.sleep(0.04)

    if signals:
        msg = (
            f"⚡ *USDT Multi-Timeframe Sinyalleri*\n"
            f"Kaynak: {'Binance' if binance_mode else 'MEXC'}\n"
            f"⏱ {ts()}\n\n"
            + "\n".join(signals[:70])
        )
        telegram(msg)
    else:
        telegram(f"ℹ️ {ts()} - Sinyal yok. ({len(syms)} coin tarandı)")


if __name__ == "__main__":
    main()
