#!/usr/bin/env python3
"""Standalone backtest visualizer server — no background tasks, starts instantly."""
import os, sys, json, pickle, http.server, urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "data", "ohlc_180_cache")
TEMPLATE = os.path.join(ROOT, "serve_viz.html")
INDIAN_STOCKS = [
    "abb", "abbotindia", "abfrl", "adanient", "adanigreen", "adaniports",
    "adanipower", "alkem", "ambujacem", "angelone", "apollohosp", "asianpaint",
    "atgl", "auropharma", "axisbank", "bajaj-auto", "bajfinance", "bajajfinsv",
    "bankbaroda", "bataindia", "bel", "bergepaint", "bharatforg", "bhartiartl",
    "boschltd", "bpcl", "britannia", "cadilahc", "canbk", "cholafin", "cipla",
    "coalindia", "colpal", "concor", "crompton", "cumminsind", "dabur",
    "divislab", "dlf", "drreddy", "eichermot", "exideind", "federalbnk", "gail",
    "godrejcp", "godrejprop", "grasim", "gujgasltd", "hal", "havells", "hcltech",
    "hdfcbank", "hdfclife", "heromotoco", "hindalco", "hindcopper", "hindunilvr",
    "hindzinc", "icicibank", "indusindbk", "infy", "ioc", "irctc", "irfc", "itc",
    "jiofin", "jswenergy", "jswsteel", "jublfood", "kotakbank", "lici", "lodha",
    "lt", "m&m", "marico", "maruti", "mcdowell-n", "motherson", "mphasis", "mrf",
    "muthootfin", "nationalum", "naukri", "nestleind", "nhpc", "ntpc", "oil",
    "ongc", "pageind", "pel", "pfc", "pidilitind", "pnb", "polycab",
    "poonawalla", "powergrid", "pvrinox", "ramcocem", "recltd", "sbicard",
    "sbilife", "sbin", "shreecem", "siemens", "srtransfin", "sunpharma", "suntv",
    "syngene", "tatacomm", "tataconsum", "tataelxsi", "tatamotors", "tatapower",
    "tatasteel", "tcs", "techm", "titan", "torntpharm", "trent", "tvsmotor",
    "ubl", "ultracemco", "vbl", "vedl", "wipro", "zomato", "zyduslife",
    "abcap", "bandhanbnk", "biocon", "bse", "castrol", "chambalfert",
    "hindpetro", "idfcfirstb", "navin", "petronet", "sail", "tatachem",
    "tatacoffee", "thermax", "torrentpow", "ujjivan", "unionbank", "voltas", "yesbank",
]


def ema(arr, p):
    if len(arr) < p: return [arr[-1]]*len(arr)
    k = 2/(p+1); r = [arr[0]]
    for v in arr[1:]: r.append(v*k + r[-1]*(1-k))
    return r

def sma(arr, p):
    return [sum(arr[max(0,i-p+1):i+1])/min(p,i+1) for i in range(len(arr))]

def swing_highs(h, lft=3, rgt=3):
    n=len(h); r=[0.0]*n
    for i in range(lft, n-rgt):
        if all(h[j]<h[i] for j in range(i-lft,i) if j>=0) and all(h[j]<h[i] for j in range(i+1,i+rgt+1) if j<n): r[i]=1.0
    return r

def swing_lows(l, lft=3, rgt=3):
    n=len(l); r=[0.0]*n
    for i in range(lft, n-rgt):
        if all(l[j]>l[i] for j in range(i-lft,i) if j>=0) and all(l[j]>l[i] for j in range(i+1,i+rgt+1) if j<n): r[i]=1.0
    return r

def prev_swing(i, arr):
    for j in range(i-1,-1,-1):
        if j<len(arr) and arr[j]==1.0: return j
    return None

def detect_bos(i, h, l, sh, sl):
    shi=[j for j in range(i) if sh[j]==1]; sli=[j for j in range(i) if sl[j]==1]
    if len(shi)<2 or len(sli)<2: return 0
    h1=h[shi[-2]]; h2=h[shi[-1]]; l1=l[sli[-2]]; l2=l[sli[-1]]
    if h2>h1 and l2>l1 and h[i]>h2: return 1
    if h2<h1 and l2<l1 and l[i]<l2: return -1
    return 0


def run_backtest(symbol, strategy):
    path = os.path.join(CACHE, f"{symbol}.pkl")
    if not os.path.exists(path): return None
    with open(path, "rb") as f: df = pickle.load(f)
    if hasattr(df.columns, 'is_multi') and df.columns.is_multi():
        df.columns = [c[0] for c in df.columns]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < 50: return None

    opens=[float(x) for x in df["Open"]]; highs=[float(x) for x in df["High"]]
    lows=[float(x) for x in df["Low"]]; closes=[float(x) for x in df["Close"]]
    volumes=[float(x) for x in df["Volume"]]; dates=list(df.index)

    _configs = {
        "1": {"session":True,"swing":False,"ema":False,"vol":False,"bos":False,"next":False},
        "3": {"session":True,"swing":False,"ema":True,"vol":False,"bos":False,"next":False},
        "5": {"session":True,"swing":False,"ema":True,"vol":True,"bos":False,"next":False},
        "7": {"session":True,"swing":False,"ema":True,"vol":True,"bos":True,"next":False},
        "9": {"session":True,"swing":False,"ema":False,"vol":False,"bos":False,"next":True},
    }
    cfg = _configs.get(strategy, _configs["1"])

    sh=swing_highs(highs,3,3); sl=swing_lows(lows,3,3)
    e50=ema(closes,50); va=sma(volumes,20)

    trades=[]; entries=set()
    for i in range(60, len(closes)):
        if i in entries: continue
        ci=closes[i]; hi=highs[i]; li=lows[i]; oi=opens[i]
        if cfg["ema"] and (i>=len(e50) or ci<=e50[i]): continue
        if cfg["vol"] and (i<20 or volumes[i]<1.5*va[i]): continue
        if cfg["bos"] and detect_bos(i,highs,lows,sh,sl)==0: continue

        levels=[]
        if cfg["session"] and i>=1:
            levels.append((highs[i-1],"SELL")); levels.append((lows[i-1],"BUY"))
        if cfg["swing"]:
            si=prev_swing(i,sh); si2=prev_swing(i,sl)
            if si is not None and i-si>=20: levels.append((highs[si],"SELL"))
            if si2 is not None and i-si2>=20: levels.append((lows[si2],"BUY"))

        for level, ed in levels:
            if i in entries: break
            if ed=="BUY" and li<level and ci>level:
                ep=oi if cfg["next"] else ci; st=min(li,level-(hi-li)*0.1)
                if abs(ep-st)/ep*100>8: continue
                tg=None
                for j in range(i,max(i-120,-1),-1):
                    if j<len(sh) and sh[j]==1.0 and highs[j]>ep: tg=highs[j]; break
                if tg is None: tg=ep+(ep-st)*10
                if (tg-ep)/(ep-st)<1.5 or ep==st: continue
                t=_sim(i,"BUY",ep,st,tg,closes,highs,lows,dates)
                if t: trades.append(t); entries.add(i)
            elif ed=="SELL" and hi>level and ci<level:
                ep=oi if cfg["next"] else ci; st=max(hi,level+(hi-li)*0.1)
                if abs(ep-st)/ep*100>8: continue
                tg=None
                for j in range(i,max(i-120,-1),-1):
                    if j<len(sl) and sl[j]==1.0 and lows[j]<ep: tg=lows[j]; break
                if tg is None: tg=ep-(st-ep)*10
                if (ep-tg)/(st-ep)<1.5 or st==ep: continue
                t=_sim(i,"SELL",ep,st,tg,closes,highs,lows,dates)
                if t: trades.append(t); entries.add(i)

    ohlc = [{"time":int(dt.timestamp()),"o":round(opens[i],2),"h":round(highs[i],2),
             "l":round(lows[i],2),"c":round(closes[i],2),"v":int(volumes[i])}
            for i,dt in enumerate(dates)]

    eq=[]; rn=0.0
    for t in sorted(trades, key=lambda x: x["et"]):
        rn+=t["p"]; eq.append({"t":t["et"],"v":round(rn,2)})

    wr = sum(1 for t in trades if t["p"]>0)/max(len(trades),1)*100
    tr = sum(t["p"] for t in trades)
    return {"ohlc":ohlc,"trades":trades,"equity":eq,"summary":{
        "trades":len(trades),"wr":round(wr,1),"ret":round(tr,2)}}


def _sim(ei, d, ep, st, tg, cl, hi, lo, dt):
    be=False; cs=st
    for j in range(ei+1, len(cl)):
        ch,cl2=hi[j],lo[j]
        if not be:
            if d=="BUY" and ch>=ep+(ep-st): be=True; cs=ep
            elif d=="SELL" and cl2<=ep-(st-ep): be=True; cs=ep
        if d=="BUY" and cl2<=cs:
            return _r(j,cs,"stop",be,ei,ep,dt,d)
        if d=="SELL" and ch>=cs:
            return _r(j,cs,"stop",be,ei,ep,dt,d)
        if d=="BUY" and ch>=tg:
            return _r(j,tg,"target",be,ei,ep,dt,d)
        if d=="SELL" and cl2<=tg:
            return _r(j,tg,"target",be,ei,ep,dt,d)
    return _r(len(cl)-1,cl[-1],"expired",be,ei,ep,dt,d)

def _r(xi, xp, rsn, be, ei, ep, dt, d):
    pnl=((xp-ep)/ep*100) if d=="BUY" else ((ep-xp)/ep*100)
    return {"et":int(dt[ei].timestamp()),"xt":int(dt[xi].timestamp()),
            "d":d,"ep":round(ep,2),"xp":round(xp,2),"p":round(pnl,2),
            "bh":xi-ei,"r":rsn,"be":be,"s":dt[ei].strftime("%Y-%m-%d"),
            "x":dt[xi].strftime("%Y-%m-%d")}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        if parsed.path == "/":
            self._html(200, open(TEMPLATE).read())
        elif parsed.path == "/api/stocks":
            avail = [s.upper() for s in INDIAN_STOCKS if os.path.exists(os.path.join(CACHE, f"{s}.pkl"))]
            self._json({"stocks": sorted(avail), "total": len(avail)})
        elif parsed.path.startswith("/api/backtest/"):
            sym = parsed.path.split("/")[-1].lower()
            strat = params.get("strategy", "1")
            result = run_backtest(sym, strat)
            if result is None:
                self._json({"error": f"No data for {sym.upper()}"})
            else:
                self._json(result)
        else:
            super().do_GET()

    def _html(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, fmt, *args):
        pass  # quiet


if __name__ == "__main__":
    import socketserver
    socketserver.TCPServer.allow_reuse_address = True
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8002
    print(f"📊 Backtest Visualizer → http://localhost:{port}")
    with socketserver.TCPServer(("", port), Handler) as httpd:
        httpd.serve_forever()
