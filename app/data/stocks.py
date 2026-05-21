INDIAN_STOCKS = [
    # Nifty 50 + Next 50 (from nifty100.json — properly named)
    "abb", "abbotindia", "adanient", "adanigreen", "adaniports",
    "adanipower", "adanitrans", "alkem", "ambujacem", "angelone",
    "apollohosp", "asianpaint", "atgl", "auropharma", "axisbank",
    "bajaj-auto", "bajfinance", "bajajfinsv", "bankbaroda", "bataindia",
    "bel", "bergepaint", "bharatforg", "bhartiartl", "boschltd",
    "bpcl", "britannia", "cadilahc", "canbk", "cholafin",
    "cipla", "coalindia", "colpal", "concor", "crompton",
    "cumminsind", "dabur", "divislab", "dlf", "drreddy",
    "eichermot", "exideind", "federalbnk", "gail", "godrejcp",
    "godrejprop", "grasim", "gujgasltd", "hal", "havells",
    "hcltech", "hdfcbank", "hdfclife", "heromotoco", "hindalco",
    "hindcopper", "hindunilvr", "hindzinc", "icicibank", "indusindbk",
    "infy", "ioc", "irctc", "irfc", "itc",
    "jiofin", "jswenergy", "jswsteel", "jublfood", "kotakbank",
    "lici", "lodha", "lt", "m&m", "marico",
    "maruti", "mcdowell-n", "motherson", "mphasis", "mrf",
    "muthootfin", "nationalum", "naukri", "nestleind", "nhpc",
    "ntpc", "oil", "ongc", "pageind", "pel",
    "pfc", "pidilitind", "pnb", "polycab", "poonawalla",
    "powergrid", "pvrinox", "ramcocem", "recltd", "sbicard",
    "sbilife", "sbin", "shreecem", "siemens", "srtransfin",
    "sunpharma", "suntv", "syngene", "tatacomm", "tataconsum",
    "tataelxsi", "tatamotors", "tatapower", "tatasteel", "tcs",
    "techm", "titan", "torntpharm", "trent", "tvsmotor",
    "ubl", "ultracemco", "vbl", "vedl", "wipro",
    "zomato", "zyduslife",
    # Extra popular stocks outside Nifty 100
    "abcap", "abfrl", "adanienergy", "bandhanbnk", "biocon",
    "bse", "castrol", "chambalfert", "gvk", "hindpetro",
    "idfcfirstb", "navin", "petronet", "sail", "tatachem",
    "tatacoffee", "thermax", "torrentpow", "ujjivan", "unionbank",
    "voltas", "yesbank",
]

INDIAN_STOCKS_SET = set(INDIAN_STOCKS)

MONITORED_SYMBOLS = ['btc', 'eth', 'gold', 'silver'] + INDIAN_STOCKS
