const BASE = 'https://api.dhan.co/v2';
const SECURITY_CSV = 'https://images.dhan.co/api-data/api-scrip-master.csv';

function headers() {
  return {
    'access-token': process.env.DHAN_ACCESS_TOKEN || '',
    'Content-Type': 'application/json',
    Accept: 'application/json',
    'client-id': process.env.DHAN_CLIENT_ID || '',
  };
}

async function get(endpoint) {
  const res = await fetch(`${BASE}${endpoint}`, {
    method: 'GET',
    headers: headers(),
  });
  if (!res.ok) {
    const text = await res.text();
    return { success: false, status: res.status, error: text.slice(0, 500) };
  }
  const data = await res.json();
  return { success: true, data };
}

async function post(endpoint, body = {}) {
  const res = await fetch(`${BASE}${endpoint}`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    return { success: false, status: res.status, error: text.slice(0, 500) };
  }
  const data = await res.json();
  return { success: true, data };
}

async function del(endpoint) {
  const res = await fetch(`${BASE}${endpoint}`, {
    method: 'DELETE',
    headers: headers(),
  });
  if (!res.ok) {
    const text = await res.text();
    return { success: false, status: res.status, error: text.slice(0, 500) };
  }
  const data = await res.json();
  return { success: true, data };
}

// --- Security map ---
let _securityMap = null;
let _securityMapTs = 0;
const SECURITY_MAP_TTL = 86400_000;

export async function ensureSecurityMap(force = false) {
  const now = Date.now();
  if (!force && _securityMap && now - _securityMapTs < SECURITY_MAP_TTL) {
    return _securityMap;
  }
  try {
    const res = await fetch(SECURITY_CSV, { timeout: 30000 });
    if (!res.ok) return _securityMap || {};
    const text = await res.text();
    const lines = text.split('\n');
    const header = lines[0].split(',');
    const exIdx = header.indexOf('SEM_EXM_EXCH_ID');
    const segIdx = header.indexOf('SEM_SEGMENT');
    const symIdx = header.indexOf('SEM_TRADING_SYMBOL');
    const idIdx = header.indexOf('SEM_SMST_SECURITY_ID');
    if (exIdx === -1 || segIdx === -1 || symIdx === -1 || idIdx === -1) {
      return _securityMap || {};
    }
    const map = {};
    for (let i = 1; i < lines.length; i++) {
      const cols = lines[i].split(',');
      const exch = (cols[exIdx] || '').trim().toUpperCase();
      const seg = (cols[segIdx] || '').trim().toUpperCase();
      if (exch !== 'NSE' || seg !== 'E') continue;
      const sym = (cols[symIdx] || '').trim().toUpperCase();
      const sid = (cols[idIdx] || '').trim();
      if (sym && sid) map[sym] = sid;
    }
    _securityMap = map;
    _securityMapTs = now;
    return map;
  } catch {
    return _securityMap || {};
  }
}

export function getSecurityId(symbol) {
  if (!_securityMap) return null;
  return _securityMap[symbol.toUpperCase().trim()] || null;
}

// --- Auth ---
export async function renewToken() {
  const token = process.env.DHAN_ACCESS_TOKEN || '';
  const cid = process.env.DHAN_CLIENT_ID || '';
  if (!token || !cid) return { success: false, error: 'Missing credentials' };
  const res = await fetch(`${BASE}/RenewToken`, {
    method: 'POST',
    headers: { 'access-token': token, dhanClientId: cid },
  });
  if (!res.ok) return { success: false, status: res.status };
  const data = await res.json();
  const newToken = data.accessToken;
  if (newToken) {
    process.env.DHAN_ACCESS_TOKEN = newToken;
    return { success: true, accessToken: newToken };
  }
  return { success: false, error: 'No accessToken in response' };
}

// --- Profile ---
export async function getProfile() {
  return get('/profile');
}

// --- Fund Limits ---
export async function getFundLimits() {
  return get('/fundlimit');
}

// --- Positions ---
export async function getPositions() {
  return get('/positions');
}

// --- Order Book ---
export async function getOrderBook() {
  return get('/orders');
}

// --- Trade Book ---
export async function getTradeBook() {
  return get('/trades');
}

// --- Holdings ---
export async function getHoldings() {
  return get('/holdings');
}

// --- Market LTP ---
export async function getMarketLtp(symbols) {
  await ensureSecurityMap();
  const ids = [];
  const symMap = {};
  for (const sym of symbols) {
    const sid = getSecurityId(sym);
    if (sid) {
      ids.push(Number(sid));
      symMap[sid] = sym.toUpperCase();
    }
  }
  if (!ids.length) return { success: false, error: 'No security IDs found' };
  const result = await post('/marketfeed/ltp', { NSE_EQ: ids });
  if (!result.success) return result;
  const feed = result.data?.data?.NSE_EQ || {};
  const out = {};
  for (const [sid, info] of Object.entries(feed)) {
    const sym = symMap[sid] || sid;
    out[sym] = { ltp: info.last_price || 0 };
  }
  return { success: true, data: out };
}

// --- Market OHLC ---
export async function getMarketOhlc(symbols) {
  await ensureSecurityMap();
  const ids = [];
  const symMap = {};
  for (const sym of symbols) {
    const sid = getSecurityId(sym);
    if (sid) {
      ids.push(Number(sid));
      symMap[sid] = sym.toUpperCase();
    }
  }
  if (!ids.length) return { success: false, error: 'No security IDs found' };
  const result = await post('/marketfeed/ohlc', { NSE_EQ: ids });
  if (!result.success) return result;
  const feed = result.data?.data?.NSE_EQ || {};
  const out = {};
  for (const [sid, info] of Object.entries(feed)) {
    const sym = symMap[sid] || sid;
    const ohlc = info.ohlc || {};
    out[sym] = {
      ltp: info.last_price || 0,
      open: ohlc.open || 0,
      high: ohlc.high || 0,
      low: ohlc.low || 0,
      close: ohlc.close || 0,
    };
  }
  return { success: true, data: out };
}

// --- Market Quote ---
export async function getMarketQuote(symbols) {
  await ensureSecurityMap();
  const ids = [];
  const symMap = {};
  for (const sym of symbols) {
    const sid = getSecurityId(sym);
    if (sid) {
      ids.push(Number(sid));
      symMap[sid] = sym.toUpperCase();
    }
  }
  if (!ids.length) return { success: false, error: 'No security IDs found' };
  const result = await post('/marketfeed/quote', { NSE_EQ: ids });
  if (!result.success) return result;
  const feed = result.data?.data?.NSE_EQ || {};
  const out = {};
  for (const [sid, info] of Object.entries(feed)) {
    const sym = symMap[sid] || sid;
    out[sym] = info;
  }
  return { success: true, data: out };
}

// --- Place Order ---
export async function placeOrder(symbol, qty, transactionType, productType = 'INTRADAY', orderType = 'MARKET', price = 0, afterMarket = false, amoTime = 'OPEN') {
  await ensureSecurityMap();
  const sid = getSecurityId(symbol);
  if (!sid) return { success: false, error: `Security ID not found for ${symbol}` };
  return post('/orders', {
    dhanClientId: process.env.DHAN_CLIENT_ID || '',
    transactionType: transactionType.toUpperCase(),
    exchangeSegment: 'NSE_EQ',
    productType: productType.toUpperCase(),
    orderType: orderType.toUpperCase(),
    validity: 'DAY',
    securityId: sid,
    quantity: qty,
    price: orderType.toUpperCase() === 'LIMIT' && price > 0 ? price : 0,
    triggerPrice: 0,
    disclosedQuantity: 0,
    afterMarketOrder: afterMarket,
    amoTime: amoTime,
    boProfitValue: null,
    boStopLossValue: null,
  });
}

// --- Cancel Order ---
export async function cancelOrder(orderId) {
  return del(`/orders/${orderId}`);
}

// --- Dashboard ---
export async function getDashboard() {
  const [profile, funds, positions, orders] = await Promise.all([
    getProfile(), getFundLimits(), getPositions(), getOrderBook(),
  ]);
  return {
    success: true,
    data: {
      profile: profile.success ? profile.data : null,
      funds: funds.success ? funds.data : null,
      positions: positions.success ? positions.data : null,
      orders: orders.success ? orders.data : null,
    },
  };
}
