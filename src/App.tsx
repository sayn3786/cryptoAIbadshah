import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  BarChart3,
  Bot,
  CalendarDays,
  CheckCircle2,
  Clipboard,
  Copy,
  CreditCard,
  Database,
  Gauge,
  GraduationCap,
  GitBranch,
  Pickaxe,
  PlayCircle,
  RefreshCw,
  Rocket,
  Search,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Trophy,
  Upload,
  Wallet,
} from 'lucide-react'
import './App.css'

const MCP_URL = 'https://mcp.gomining.com/mcp'
const BRIDGE_URL = 'http://127.0.0.1:8787'

type TabId = 'overview' | 'miners' | 'earn' | 'market' | 'automation' | 'skills'

type BridgeStatus = {
  authRequired: boolean
  authUrl: string
  callbackUrl: string
  connected: boolean
  gominingMcpUrl: string
  hasApiKey?: boolean
  instructions?: string
  lastError?: string
}

type McpTool = {
  name: string
  description?: string
  inputSchema?: unknown
}

type Miner = {
  id: string
  name: string
  collection: string
  hashrate: number
  efficiency: number
  dailyBtc: number
  roi: number
  payback: number
  rarity: string
  status: 'Mining' | 'Upgrade ready' | 'Watch'
}

const miners: Miner[] = [
  {
    id: 'm-01',
    name: 'Nordic Hash #2184',
    collection: 'GoMiner Genesis',
    hashrate: 42,
    efficiency: 31,
    dailyBtc: 0.000071,
    roi: 14.8,
    payback: 682,
    rarity: 'Epic',
    status: 'Mining',
  },
  {
    id: 'm-02',
    name: 'Volt Core #0907',
    collection: 'Industrial Pack',
    hashrate: 27,
    efficiency: 38,
    dailyBtc: 0.000039,
    roi: 9.4,
    payback: 811,
    rarity: 'Rare',
    status: 'Upgrade ready',
  },
  {
    id: 'm-03',
    name: 'Frostline #4420',
    collection: 'Hydro Series',
    hashrate: 56,
    efficiency: 26,
    dailyBtc: 0.000104,
    roi: 18.2,
    payback: 594,
    rarity: 'Legendary',
    status: 'Mining',
  },
  {
    id: 'm-04',
    name: 'Atlas Rack #1173',
    collection: 'Classic',
    hashrate: 19,
    efficiency: 44,
    dailyBtc: 0.000022,
    roi: 6.6,
    payback: 1038,
    rarity: 'Common',
    status: 'Watch',
  },
]

const rewardHistory = [
  { day: 'Mon', btc: 0.000211 },
  { day: 'Tue', btc: 0.000219 },
  { day: 'Wed', btc: 0.000226 },
  { day: 'Thu', btc: 0.000221 },
  { day: 'Fri', btc: 0.000238 },
  { day: 'Sat', btc: 0.000246 },
  { day: 'Sun', btc: 0.000252 },
]

const aiPrompts = [
  {
    title: 'Portfolio audit',
    tool: 'get_account_summary',
    prompt:
      'Give me a complete GoMining portfolio audit: wallet balances, miners, rewards, VIP level, staking, and Simple Earn status. Flag the top three opportunities.',
  },
  {
    title: 'Miner optimization',
    tool: 'get_nft_detail',
    prompt:
      'Compare all my miners by ROI, payback period, hashrate, efficiency, and daily income. Which miner should I upgrade first and why?',
  },
  {
    title: 'Marketplace scan',
    tool: 'search_secondary_marketplace',
    prompt:
      'Search the secondary marketplace for miners that beat my current price per TH and payback period. Show only candidates worth further review.',
  },
  {
    title: 'Daily digest',
    tool: 'get_nft_mining_rewards',
    prompt:
      'Create a daily GoMining digest with yesterday earnings, 7-day trend, miner warnings, Simple Earn rewards, VIP progress, and market context.',
  },
]

const aiSkills = [
  'Platform overview and ecosystem basics',
  'Digital Miners and avatar collections',
  'GOMINING token utility and tokenomics',
  'VIP loyalty program and tiers',
  'Crypto wallet deposits and withdrawals',
  'Simple Earn passive BTC yield',
  'Instant Funds',
  'GoMining Card',
  'Cashback system',
  'Travel bookings with GOMINING tokens',
  'Promo codes and GoBoxes',
  'Payment methods and KYC',
  'GoMining Academy',
]

const toolCategories = [
  { icon: Wallet, name: 'Wallet', description: 'Balances, deposits, conversions, transaction history' },
  { icon: Pickaxe, name: 'Miners', description: 'Miner list, details, ROI, upgrades, mining rewards' },
  { icon: BarChart3, name: 'Market', description: 'Ticker prices, official marketplace, secondary listings' },
  { icon: CreditCard, name: 'Card', description: 'Card balances, transactions, refunds, cashback status' },
  { icon: Trophy, name: 'VIP', description: 'Tier progress, benefits, subscriptions, loyalty rewards' },
  { icon: GraduationCap, name: 'Academy', description: 'Courses, progress, completion status, ratings' },
]

function formatBtc(value: number) {
  return `${value.toFixed(8)} BTC`
}

function formatUsd(value: number) {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value)
}

function getStatusClass(status: Miner['status']) {
  if (status === 'Mining') {
    return 'good'
  }

  if (status === 'Upgrade ready') {
    return 'warn'
  }

  return 'neutral'
}

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>('overview')
  const [copied, setCopied] = useState('')
  const [riskMode, setRiskMode] = useState<'balanced' | 'growth' | 'defensive'>('balanced')
  const [bridgeStatus, setBridgeStatus] = useState<BridgeStatus | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [mcpTools, setMcpTools] = useState<McpTool[]>([])
  const [selectedTool, setSelectedTool] = useState('')
  const [toolArgs, setToolArgs] = useState('{}')
  const [toolResult, setToolResult] = useState('')
  const [liveStatus, setLiveStatus] = useState('Bridge not checked yet.')
  const [isBridgeBusy, setIsBridgeBusy] = useState(false)

  const totals = useMemo(() => {
    const hashrate = miners.reduce((sum, miner) => sum + miner.hashrate, 0)
    const dailyBtc = miners.reduce((sum, miner) => sum + miner.dailyBtc, 0)
    const avgEfficiency = miners.reduce((sum, miner) => sum + miner.efficiency, 0) / miners.length
    const avgRoi = miners.reduce((sum, miner) => sum + miner.roi, 0) / miners.length

    return { hashrate, dailyBtc, avgEfficiency, avgRoi }
  }, [])

  const bestMiner = useMemo(() => [...miners].sort((a, b) => b.roi - a.roi)[0], [])
  const rewardMax = Math.max(...rewardHistory.map((item) => item.btc))

  useEffect(() => {
    async function checkBridge() {
      try {
        const status = await requestBridge<BridgeStatus>('/api/mcp/health')
        setBridgeStatus(status)
        setLiveStatus(status.connected ? 'Connected to GoMining MCP.' : 'Local bridge is running.')
      } catch (error) {
        setLiveStatus(
          error instanceof Error
            ? `Bridge offline: ${error.message}`
            : 'Bridge offline. Start it with npm run dev:full or npm run mcp:bridge.',
        )
      }
    }

    checkBridge().catch(() => undefined)
  }, [])

  async function copyText(value: string, label: string) {
    await navigator.clipboard.writeText(value)
    setCopied(label)
    window.setTimeout(() => setCopied(''), 1800)
  }

  async function requestBridge<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${BRIDGE_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
    const payload = await response.json()

    if (!response.ok) {
      throw new Error(payload.error || payload.lastError || 'MCP bridge request failed.')
    }

    return payload as T
  }

  async function connectBridge() {
    setIsBridgeBusy(true)
    setLiveStatus('Connecting to GoMining MCP...')

    try {
      const status = await requestBridge<BridgeStatus & { authRequired?: boolean }>('/api/mcp/connect', {
        method: 'POST',
      })
      setBridgeStatus(status)

      if (status.connected) {
        setLiveStatus('Connected. Loading exposed tools...')
        await loadTools()
      } else if (status.authRequired && status.authUrl) {
        setLiveStatus('Authorization required. Open the GoMining consent page.')
      } else {
        setLiveStatus(status.lastError || 'Could not connect yet.')
      }
    } catch (error) {
      setLiveStatus(error instanceof Error ? error.message : 'Could not connect to the MCP bridge.')
    } finally {
      setIsBridgeBusy(false)
    }
  }

  async function saveApiKey() {
    if (!apiKey.trim()) {
      setLiveStatus('Paste your GoMining MCP API key first.')
      return
    }

    setIsBridgeBusy(true)
    setLiveStatus('Saving API key in local bridge memory...')

    try {
      const status = await requestBridge<BridgeStatus>('/api/mcp/api-key', {
        body: JSON.stringify({ apiKey: apiKey.trim() }),
        method: 'POST',
      })
      setBridgeStatus(status)
      setApiKey('')
      setMcpTools([])
      setSelectedTool('')
      setToolResult('')
      setLiveStatus('API key saved locally. Connect MCP, then retrieve APIs.')
    } catch (error) {
      setLiveStatus(error instanceof Error ? error.message : 'Could not save API key.')
    } finally {
      setIsBridgeBusy(false)
    }
  }

  async function loadTools() {
    setIsBridgeBusy(true)
    setLiveStatus('Retrieving exposed MCP tools...')

    try {
      const payload = await requestBridge<BridgeStatus & { tools?: McpTool[] }>('/api/mcp/tools')
      setBridgeStatus(payload)
      setMcpTools(payload.tools ?? [])
      setSelectedTool((current) => current || payload.tools?.[0]?.name || '')
      setLiveStatus(`Retrieved ${payload.tools?.length ?? 0} exposed tools.`)
    } catch (error) {
      setLiveStatus(error instanceof Error ? error.message : 'Could not retrieve tools.')
    } finally {
      setIsBridgeBusy(false)
    }
  }

  async function callSelectedTool() {
    if (!selectedTool) {
      setLiveStatus('Select a tool first.')
      return
    }

    setIsBridgeBusy(true)
    setLiveStatus(`Calling ${selectedTool}...`)

    try {
      const parsedArgs = JSON.parse(toolArgs || '{}')
      const payload = await requestBridge<{ result: unknown }>('/api/mcp/call', {
        body: JSON.stringify({ name: selectedTool, arguments: parsedArgs }),
        method: 'POST',
      })
      setToolResult(JSON.stringify(payload.result, null, 2))
      setLiveStatus(`${selectedTool} returned data.`)
    } catch (error) {
      setLiveStatus(error instanceof Error ? error.message : 'Tool call failed.')
    } finally {
      setIsBridgeBusy(false)
    }
  }

  return (
    <main className="app-shell">
      <section className="hero">
        <div className="hero-copy">
          <div className="product-mark">
            <Bot size={30} />
            <span>GoMining AI Analyst</span>
          </div>
          <h1>Your read-only command center for GoMining MCP.</h1>
          <p>
            Connect the official MCP server to an AI assistant, then use this workspace to inspect performance, copy
            high-value prompts, plan upgrades, and track the signals that actually matter.
          </p>
          <div className="hero-actions">
            <button className="primary-button" onClick={() => copyText(MCP_URL, 'MCP URL')} type="button">
              <Copy size={18} />
              Copy MCP URL
            </button>
            <a href="https://docs.gomining.com/en/product/ai/mcp-server" rel="noreferrer" target="_blank">
              Official docs
            </a>
          </div>
        </div>

        <div className="connection-panel" aria-label="MCP connection details">
          <div className="panel-topline">
            <Database size={20} />
            <span>Live MCP bridge</span>
          </div>
          <code>{MCP_URL}</code>
          <div className={`bridge-state ${bridgeStatus?.connected ? 'connected' : 'idle'}`}>
            <span>{bridgeStatus?.connected ? 'Connected' : bridgeStatus?.authRequired ? 'Authorize account' : 'Not connected'}</span>
            <small>{liveStatus}</small>
            <small>{bridgeStatus?.hasApiKey ? 'API key is loaded in local memory.' : 'API key not loaded yet.'}</small>
          </div>
          <label className="api-key-field">
            <span>GoMining API key</span>
            <input
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="Paste key for API_KEY header"
              type="password"
              value={apiKey}
            />
          </label>
          <div className="connection-steps">
            <span>
              <CheckCircle2 size={16} /> Start local bridge on port 8787
            </span>
            <span>
              <CheckCircle2 size={16} /> Approve GoMining account permissions
            </span>
            <span>
              <ShieldCheck size={16} /> Read-only tools: no transfers or purchases
            </span>
          </div>
          <div className="bridge-actions">
            <button className="secondary-button" disabled={isBridgeBusy || !apiKey.trim()} onClick={saveApiKey} type="button">
              <ShieldCheck size={18} />
              Save key
            </button>
            <button className="primary-button" disabled={isBridgeBusy} onClick={connectBridge} type="button">
              <Database size={18} />
              Connect MCP
            </button>
            <button className="secondary-button" disabled={isBridgeBusy || !bridgeStatus?.connected} onClick={loadTools} type="button">
              <RefreshCw size={18} />
              Load tools
            </button>
          </div>
          {bridgeStatus?.authRequired && bridgeStatus.authUrl && (
            <a className="auth-link" href={bridgeStatus.authUrl} rel="noreferrer" target="_blank">
              Open GoMining authorization
            </a>
          )}
          <div className="copied-line">{copied ? `${copied} copied to clipboard.` : 'Ready for setup.'}</div>
        </div>
      </section>

      <section className="metric-strip" aria-label="Portfolio snapshot">
        <article>
          <span>Total hashrate</span>
          <strong>{totals.hashrate} TH/s</strong>
          <small>Across {miners.length} digital miners</small>
        </article>
        <article>
          <span>Daily BTC estimate</span>
          <strong>{formatBtc(totals.dailyBtc)}</strong>
          <small>Sample data until MCP is connected</small>
        </article>
        <article>
          <span>Average ROI</span>
          <strong>{totals.avgRoi.toFixed(1)}%</strong>
          <small>Best: {bestMiner.name}</small>
        </article>
        <article>
          <span>Portfolio value</span>
          <strong>{formatUsd(4380)}</strong>
          <small>Use MCP market tools for live values</small>
        </article>
      </section>

      <nav className="tabs" aria-label="Dashboard sections">
        {[
          ['overview', 'Overview'],
          ['miners', 'Miners'],
          ['earn', 'Earn'],
          ['market', 'Market'],
          ['automation', 'AI workflows'],
          ['skills', 'Skills'],
        ].map(([id, label]) => (
          <button
            className={activeTab === id ? 'active' : ''}
            key={id}
            onClick={() => setActiveTab(id as TabId)}
            type="button"
          >
            {label}
          </button>
        ))}
      </nav>

      {activeTab === 'overview' && (
        <section className="dashboard-grid">
          <article className="panel wide">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Decision board</p>
                <h2>What to do first</h2>
              </div>
              <Sparkles size={22} />
            </div>
            <div className="recommendations">
              <div>
                <strong>1. Run a live account audit</strong>
                <span>Use `get_account_summary` and `get_nfts` to replace the sample numbers with your account data.</span>
              </div>
              <div>
                <strong>2. Compare ROI before buying</strong>
                <span>Ask the AI to compare your current miners against official and secondary marketplace listings.</span>
              </div>
              <div>
                <strong>3. Track reward trend weekly</strong>
                <span>Watch seven-day BTC output and investigate miners with falling efficiency or long payback periods.</span>
              </div>
            </div>
          </article>

          <article className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Risk mode</p>
                <h2>Strategy lens</h2>
              </div>
              <Gauge size={22} />
            </div>
            <div className="segmented">
              {(['balanced', 'growth', 'defensive'] as const).map((mode) => (
                <button className={riskMode === mode ? 'active' : ''} key={mode} onClick={() => setRiskMode(mode)}>
                  {mode}
                </button>
              ))}
            </div>
            <p className="strategy-copy">
              {riskMode === 'balanced' &&
                'Prioritize upgrades that improve payback period without concentrating too much value in one miner.'}
              {riskMode === 'growth' &&
                'Look for higher hashrate and marketplace deals, but demand clear ROI improvement before adding exposure.'}
              {riskMode === 'defensive' &&
                'Focus on liquidity, maintenance discounts, stable reward history, and avoiding long-payback purchases.'}
            </p>
          </article>

          <article className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Supported tools</p>
                <h2>MCP coverage</h2>
              </div>
              <RefreshCw size={22} />
            </div>
            <div className="tool-list">
              {toolCategories.map((tool) => {
                const Icon = tool.icon
                return (
                  <div key={tool.name}>
                    <Icon size={18} />
                    <span>
                      <strong>{tool.name}</strong>
                      <small>{tool.description}</small>
                    </span>
                  </div>
                )
              })}
            </div>
          </article>
        </section>
      )}

      {activeTab === 'miners' && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Miner analysis</p>
              <h2>Performance ranking</h2>
            </div>
            <Pickaxe size={24} />
          </div>
          <div className="miner-table">
            <div className="table-head">
              <span>Miner</span>
              <span>Hashrate</span>
              <span>Efficiency</span>
              <span>Daily BTC</span>
              <span>ROI</span>
              <span>Status</span>
            </div>
            {miners.map((miner) => (
              <div className="table-row" key={miner.id}>
                <span>
                  <strong>{miner.name}</strong>
                  <small>
                    {miner.collection} / {miner.rarity}
                  </small>
                </span>
                <span>{miner.hashrate} TH/s</span>
                <span>{miner.efficiency} W/TH</span>
                <span>{formatBtc(miner.dailyBtc)}</span>
                <span>{miner.roi}%</span>
                <span className={`status ${getStatusClass(miner.status)}`}>{miner.status}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {activeTab === 'earn' && (
        <section className="dashboard-grid">
          <article className="panel wide">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Mining rewards</p>
                <h2>Seven-day BTC trend</h2>
              </div>
              <TrendingUp size={24} />
            </div>
            <div className="chart" aria-label="Seven-day reward chart">
              {rewardHistory.map((item) => (
                <div key={item.day}>
                  <span style={{ height: `${Math.max(18, (item.btc / rewardMax) * 100)}%` }} />
                  <small>{item.day}</small>
                </div>
              ))}
            </div>
          </article>

          <article className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Passive income</p>
                <h2>Simple Earn checklist</h2>
              </div>
              <Wallet size={22} />
            </div>
            <ul className="check-list">
              <li>Check eligibility by country and VIP level</li>
              <li>Compare APR by supported asset</li>
              <li>Review last-cycle BTC rewards</li>
              <li>Track VIP multiplier impact</li>
            </ul>
          </article>
        </section>
      )}

      {activeTab === 'market' && (
        <section className="dashboard-grid">
          <article className="panel wide">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Marketplace scanner</p>
                <h2>Buying rules</h2>
              </div>
              <Search size={24} />
            </div>
            <div className="rules-grid">
              <div>
                <strong>Price per TH</strong>
                <span>Only shortlist miners cheaper than your current portfolio average.</span>
              </div>
              <div>
                <strong>Payback period</strong>
                <span>Prefer listings that improve your average payback period by at least 10%.</span>
              </div>
              <div>
                <strong>Efficiency</strong>
                <span>Avoid adding miners that worsen your average W/TH without a strong discount.</span>
              </div>
              <div>
                <strong>Liquidity</strong>
                <span>Keep wallet balance available for maintenance, upgrades, and better future listings.</span>
              </div>
            </div>
          </article>

          <article className="panel caution">
            <AlertTriangle size={24} />
            <h2>Important</h2>
            <p>
              MCP analysis can help you compare data, but it is not a guarantee of profit. Always verify balances,
              deposit addresses, prices, and ROI inside GoMining before acting.
            </p>
          </article>
        </section>
      )}

      {activeTab === 'automation' && (
        <section className="prompt-grid">
          <article className="prompt-card live-console">
            <div>
              <p className="eyebrow">Live MCP API</p>
              <h2>Call exposed GoMining tools</h2>
            </div>
            <div className="console-controls">
              <label>
                <span>Tool</span>
                <select onChange={(event) => setSelectedTool(event.target.value)} value={selectedTool}>
                  <option value="">Load tools from MCP</option>
                  {mcpTools.map((tool) => (
                    <option key={tool.name} value={tool.name}>
                      {tool.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Arguments JSON</span>
                <textarea onChange={(event) => setToolArgs(event.target.value)} spellCheck={false} value={toolArgs} />
              </label>
            </div>
            <div className="bridge-actions">
              <button disabled={isBridgeBusy || !bridgeStatus?.connected} onClick={loadTools} type="button">
                <RefreshCw size={18} />
                Retrieve APIs
              </button>
              <button disabled={isBridgeBusy || !selectedTool} onClick={callSelectedTool} type="button">
                <PlayCircle size={18} />
                Run tool
              </button>
            </div>
            <pre>{toolResult || liveStatus}</pre>
          </article>

          <article className="prompt-card tools-card">
            <div>
              <p className="eyebrow">Exposed tools</p>
              <h2>{mcpTools.length ? `${mcpTools.length} retrieved` : 'Not loaded yet'}</h2>
            </div>
            <div className="retrieved-tools">
              {mcpTools.length === 0 && <span>Connect MCP, authorize GoMining, then retrieve APIs.</span>}
              {mcpTools.map((tool) => (
                <button
                  className={selectedTool === tool.name ? 'selected' : ''}
                  key={tool.name}
                  onClick={() => {
                    setSelectedTool(tool.name)
                    setToolResult(JSON.stringify(tool.inputSchema ?? {}, null, 2))
                  }}
                  type="button"
                >
                  <strong>{tool.name}</strong>
                  <small>{tool.description || 'No description returned.'}</small>
                </button>
              ))}
            </div>
          </article>

          {aiPrompts.map((item) => (
            <article className="prompt-card" key={item.title}>
              <div>
                <p className="eyebrow">{item.tool}</p>
                <h2>{item.title}</h2>
              </div>
              <p>{item.prompt}</p>
              <button onClick={() => copyText(item.prompt, item.title)} type="button">
                <Clipboard size={18} />
                Copy prompt
              </button>
            </article>
          ))}
          <article className="prompt-card schedule">
            <CalendarDays size={24} />
            <h2>Daily check-in</h2>
            <p>
              Put the daily digest prompt into your AI tool’s scheduled workflow so you wake up to reward changes,
              marketplace warnings, and VIP progress.
            </p>
            <button onClick={() => copyText('Run my GoMining daily digest every morning at 8:00.', 'Schedule idea')}>
              <Rocket size={18} />
              Copy schedule idea
            </button>
          </article>
        </section>
      )}

      {activeTab === 'skills' && (
        <section className="dashboard-grid">
          <article className="panel wide">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">GoMining AI Skills</p>
                <h2>Knowledge layer for your agent</h2>
              </div>
              <GraduationCap size={24} />
            </div>
            <p className="section-copy">
              Skills are open-source knowledge packages that teach an AI assistant the GoMining ecosystem. Use them with
              MCP: Skills explain the platform; MCP retrieves your account data.
            </p>
            <div className="skill-grid">
              {aiSkills.map((skill) => (
                <span key={skill}>{skill}</span>
              ))}
            </div>
          </article>

          <article className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Install</p>
                <h2>Agent Skills CLI</h2>
              </div>
              <GitBranch size={22} />
            </div>
            <div className="command-stack">
              <code>npx skills add gomining-ai/gomining-agent-skills --all</code>
              <code>npx skills add gomining-ai/gomining-agent-skills --skill gomining-token</code>
            </div>
            <button
              className="secondary-button"
              onClick={() => copyText('npx skills add gomining-ai/gomining-agent-skills --all', 'Skills install command')}
              type="button"
            >
              <Copy size={18} />
              Copy install command
            </button>
          </article>

          <article className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Claude.ai</p>
                <h2>Upload ZIP skills</h2>
              </div>
              <Upload size={22} />
            </div>
            <ol className="number-list">
              <li>Clone or download `gomining-ai/gomining-agent-skills`.</li>
              <li>Zip each skill folder with `SKILL.md` at the ZIP root.</li>
              <li>Upload each ZIP in Claude Settings, Capabilities, Skills.</li>
              <li>Ask a GoMining question and confirm the right skill activates.</li>
            </ol>
            <a className="auth-link" href="https://github.com/gomining-ai/gomining-agent-skills" rel="noreferrer" target="_blank">
              Open skills repository
            </a>
          </article>
        </section>
      )}
    </main>
  )
}
