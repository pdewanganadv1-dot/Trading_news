import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { registerDhanTools } from './tools/dhan.js';

const server = new McpServer(
  {
    name: 'dhanhq',
    version: '1.0.0',
    description: 'DhanHQ trading API — profile, funds, orders, market data, and more',
  },
  {
    instructions: `DhanHQ MCP — tools for reading and trading via DhanHQ API.

CREDENTIALS: Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN environment variables.
The access token expires every 24 hours. Use dhan_renew_token to refresh it.

KEY TOOLS:
- dhan_get_dashboard → profile, funds, positions, orders in one call
- dhan_get_fund_limits → available balance, margins
- dhan_get_positions → open positions with P&L
- dhan_get_order_book → today's orders
- dhan_get_market_ltp → live prices for symbols (batch)
- dhan_get_market_ohlc → OHLC data (batch)
- dhan_get_market_quote → full quote with market depth (batch)
- dhan_place_order → BUY/SELL (supports MARKET, LIMIT, AMO)
- dhan_cancel_order → cancel a pending order
- dhan_renew_token → refresh the 24h access token
- dhan_get_security_id → look up security ID by symbol
`,
  }
);

registerDhanTools(server);

const transport = new StdioServerTransport();
await server.connect(transport);
