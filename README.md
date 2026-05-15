# GoMining AI Analyst

A React/Vite dashboard for planning how to use the official GoMining MCP Server.

The app gives you a command-center style workspace with:

- MCP connection details for `https://mcp.gomining.com/mcp`.
- Portfolio snapshot cards for hashrate, daily BTC, ROI, and value.
- Miner performance ranking with ROI, payback, efficiency, and status.
- Mining rewards trend view.
- Marketplace buying rules and safety warnings.
- Copy-ready AI prompts for portfolio audits, miner optimization, marketplace scans, and daily digests.
- A local MCP bridge that lists and calls GoMining's exposed MCP tools.
- A GoMining AI Skills page with install commands and Claude.ai upload guidance.

## Run Locally

```bash
npm install
npm run dev:full
```

Open the local Vite URL shown in the terminal, usually `http://127.0.0.1:5173`.

`npm run dev:full` starts both:

- Vite frontend on `http://127.0.0.1:5173`.
- Local MCP bridge on `http://127.0.0.1:8787`.

## How To Use It With GoMining MCP

1. Open an MCP-compatible AI tool such as Claude, Cursor, Windsurf, or Claude Code.
2. Add this remote MCP server:

```text
https://mcp.gomining.com/mcp
```

3. Approve GoMining account permissions on the consent screen.
4. Paste your GoMining API key into the app so the bridge can send GoMining's required `API_KEY` header.
5. Use the live MCP console to retrieve exposed tools and call them with JSON arguments.
6. Use the prompts in the app to analyze your wallet, miners, rewards, Simple Earn status, VIP tier, and marketplace options.

The API key is stored only in local bridge memory by default. Restarting the bridge clears it. You can also provide it as an environment variable:

```bash
set GOMINING_API_KEY=your_key_here
npm run dev:full
```

The GoMining MCP tools are read-only at the time this app was created. Always verify balances, deposit addresses, prices, and ROI inside GoMining before taking action.

## GoMining AI Skills

GoMining AI Skills are open-source knowledge packages for compatible AI agents. They are different from MCP:

- MCP retrieves live or account-specific data through tools.
- Skills teach the agent GoMining concepts, product rules, tokenomics, VIP tiers, wallet behavior, cards, Simple Earn, KYC, cashback, Academy, and related platform areas.

Install all skills:

```bash
npx skills add gomining-ai/gomining-agent-skills --all
```

Install one skill:

```bash
npx skills add gomining-ai/gomining-agent-skills --skill gomining-token
```

Repository: https://github.com/gomining-ai/gomining-agent-skills
