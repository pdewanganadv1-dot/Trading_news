import math
from typing import Optional
from scipy.stats import norm


def bs_call_price(S, K, T, r, sigma):
    if sigma <= 0 or T <= 0:
        return max(S - K, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def bs_put_price(S, K, T, r, sigma):
    if sigma <= 0 or T <= 0:
        return max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def implied_volatility(market_price, S, K, T, r, option_type="CE", max_iter=200, tol=1e-6):
    if market_price <= 0 or T <= 0:
        return None
    intrinsic = max(S - K, 0) if option_type == "CE" else max(K - S, 0)
    if market_price < intrinsic:
        return None
    sigma_low, sigma_high = 0.001, 5.0
    for _ in range(max_iter):
        sigma_mid = (sigma_low + sigma_high) / 2
        if option_type == "CE":
            price = bs_call_price(S, K, T, r, sigma_mid)
        else:
            price = bs_put_price(S, K, T, r, sigma_mid)
        diff = price - market_price
        if abs(diff) < tol:
            return round(sigma_mid, 4)
        if diff > 0:
            sigma_high = sigma_mid
        else:
            sigma_low = sigma_mid
    return round((sigma_low + sigma_high) / 2, 4)


def delta(S, K, T, r, sigma, option_type="CE"):
    if T <= 0 or sigma <= 0:
        return 1.0 if option_type == "CE" else -1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    if option_type == "CE":
        return round(norm.cdf(d1), 4)
    return round(norm.cdf(d1) - 1, 4)


def gamma(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    g = norm.pdf(d1) / (S * sigma * math.sqrt(T))
    return round(g, 6)


def theta(S, K, T, r, sigma, option_type="CE"):
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    t1 = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
    if option_type == "CE":
        t2 = r * K * math.exp(-r * T) * norm.cdf(d2)
        return round((t1 - t2) / 365, 6)
    t2 = r * K * math.exp(-r * T) * norm.cdf(-d2)
    return round((t1 + t2) / 365, 6)


def vega(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    v = S * norm.pdf(d1) * math.sqrt(T)
    return round(v / 100, 4)


def rho(S, K, T, r, sigma, option_type="CE"):
    if T <= 0:
        return 0.0
    d2 = (math.log(S / K) + (r - 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    if option_type == "CE":
        return round(K * T * math.exp(-r * T) * norm.cdf(d2) / 100, 4)
    return round(-K * T * math.exp(-r * T) * norm.cdf(-d2) / 100, 4)


def compute_greeks(market_price, S, K, T, r=0.065, option_type="CE"):
    iv = implied_volatility(market_price, S, K, T, r, option_type)
    if iv is None:
        return {"iv": None, "delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}
    return {
        "iv": iv,
        "delta": delta(S, K, T, r, iv, option_type),
        "gamma": gamma(S, K, T, r, iv),
        "theta": theta(S, K, T, r, iv, option_type),
        "vega": vega(S, K, T, r, iv),
        "rho": rho(S, K, T, r, iv, option_type),
    }
