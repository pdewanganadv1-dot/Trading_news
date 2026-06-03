import { z } from 'zod';
import { jsonResult } from './_format.js';
import * as dhan from '../core/dhan.js';

export function registerDhanTools(server) {
  // --- Profile ---
  server.tool('dhan_get_profile', 'Get Dhan trading account profile', {}, async () => {
    try { return jsonResult(await dhan.getProfile()); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Fund Limits ---
  server.tool('dhan_get_fund_limits', 'Get Dhan account fund limits and available balance', {}, async () => {
    try { return jsonResult(await dhan.getFundLimits()); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Positions ---
  server.tool('dhan_get_positions', 'Get all open positions for the current trading day', {}, async () => {
    try { return jsonResult(await dhan.getPositions()); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Order Book ---
  server.tool('dhan_get_order_book', 'Get all orders for the current trading day', {}, async () => {
    try { return jsonResult(await dhan.getOrderBook()); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Trade Book ---
  server.tool('dhan_get_trade_book', 'Get executed trades for the current day', {}, async () => {
    try { return jsonResult(await dhan.getTradeBook()); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Holdings ---
  server.tool('dhan_get_holdings', 'Get all demat holdings', {}, async () => {
    try { return jsonResult(await dhan.getHoldings()); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Market LTP ---
  server.tool('dhan_get_market_ltp', 'Get last traded price for one or more symbols', {
    symbols: z.array(z.string()).describe('Stock symbols (e.g. ["RELIANCE", "TCS"])'),
  }, async ({ symbols }) => {
    try { return jsonResult(await dhan.getMarketLtp(symbols)); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Market OHLC ---
  server.tool('dhan_get_market_ohlc', 'Get OHLC data for one or more symbols', {
    symbols: z.array(z.string()).describe('Stock symbols (e.g. ["RELIANCE", "TCS"])'),
  }, async ({ symbols }) => {
    try { return jsonResult(await dhan.getMarketOhlc(symbols)); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Market Quote (full depth) ---
  server.tool('dhan_get_market_quote', 'Get full market quote with depth for symbols', {
    symbols: z.array(z.string()).describe('Stock symbols (e.g. ["RELIANCE", "TCS"])'),
  }, async ({ symbols }) => {
    try { return jsonResult(await dhan.getMarketQuote(symbols)); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Place Order ---
  server.tool('dhan_place_order', 'Place a BUY or SELL order via DhanHQ', {
    symbol: z.string().describe('Stock symbol (e.g. "RELIANCE")'),
    qty: z.number().int().positive().describe('Quantity to buy/sell'),
    transaction_type: z.enum(['BUY', 'SELL']).describe('BUY or SELL'),
    product_type: z.enum(['INTRADAY', 'CNC', 'MARGIN', 'MTF', 'CO', 'BO']).optional().default('INTRADAY').describe('Product type'),
    order_type: z.enum(['MARKET', 'LIMIT', 'STOP_LOSS', 'STOP_LOSS_MARKET']).optional().default('MARKET').describe('Order type'),
    price: z.number().optional().default(0).describe('Price for LIMIT orders'),
    after_market: z.boolean().optional().default(false).describe('After Market Order'),
    amo_time: z.string().optional().default('OPEN').describe('AMO time (OPEN or PRE_OPEN)'),
  }, async ({ symbol, qty, transaction_type, product_type, order_type, price, after_market, amo_time }) => {
    try { return jsonResult(await dhan.placeOrder(symbol, qty, transaction_type, product_type, order_type, price, after_market, amo_time)); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Cancel Order ---
  server.tool('dhan_cancel_order', 'Cancel a pending order by order ID', {
    order_id: z.string().describe('Order ID to cancel'),
  }, async ({ order_id }) => {
    try { return jsonResult(await dhan.cancelOrder(order_id)); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Renew Token ---
  server.tool('dhan_renew_token', 'Renew the DhanHQ access token (expires every 24h)', {}, async () => {
    try { return jsonResult(await dhan.renewToken()); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Dashboard ---
  server.tool('dhan_get_dashboard', 'Get full DhanHQ dashboard snapshot (profile + funds + positions + orders)', {}, async () => {
    try { return jsonResult(await dhan.getDashboard()); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  // --- Security Map ---
  server.tool('dhan_get_security_id', 'Look up Dhan security ID for a stock symbol', {
    symbol: z.string().describe('Stock symbol (e.g. "RELIANCE")'),
  }, async ({ symbol }) => {
    try {
      await dhan.ensureSecurityMap();
      const sid = dhan.getSecurityId(symbol);
      return jsonResult({ success: true, symbol: symbol.toUpperCase(), securityId: sid || null, found: !!sid });
    } catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });
}
