import { useEffect, useMemo, useState } from 'react'
import { ApiPromise, WsProvider } from '@polkadot/api'
import type { InjectedAccountWithMeta } from '@polkadot/extension-inject/types'
import { web3Accounts, web3Enable, web3FromAddress } from '@polkadot/extension-dapp'
import { BN } from '@polkadot/util'
import { cryptoWaitReady, decodeAddress, encodeAddress } from '@polkadot/util-crypto'
import {
  ArrowDownUp,
  CheckCircle2,
  Copy,
  ExternalLink,
  Loader2,
  Plug,
  RefreshCw,
  Send,
  ShieldCheck,
  Wallet,
} from 'lucide-react'
import './App.css'

const SS58_BITTENSOR = 42
const DEFAULT_DECIMALS = 9
const NETWORKS = [
  {
    id: 'finney',
    label: 'Finney',
    purpose: 'Mainnet',
    endpoint: 'wss://entrypoint-finney.opentensor.ai:443',
  },
  {
    id: 'test',
    label: 'Testnet',
    purpose: 'Test TAO',
    endpoint: 'wss://test.finney.opentensor.ai:443',
  },
  {
    id: 'lite',
    label: 'Lite',
    purpose: 'Mainnet lite',
    endpoint: 'wss://lite.chain.opentensor.ai:443',
  },
] as const

type NetworkId = (typeof NETWORKS)[number]['id']
type StatusKind = 'idle' | 'loading' | 'success' | 'error'

type Status = {
  kind: StatusKind
  message: string
}

type AccountInfo = {
  data: {
    free: {
      toBigInt: () => bigint
    }
  }
}

function getNetwork(networkId: NetworkId) {
  return NETWORKS.find((network) => network.id === networkId) ?? NETWORKS[0]
}

function formatAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-8)}`
}

function formatTao(value: bigint, decimals = DEFAULT_DECIMALS) {
  const base = 10n ** BigInt(decimals)
  const whole = value / base
  const fraction = value % base
  const trimmed = fraction.toString().padStart(decimals, '0').slice(0, 4).replace(/0+$/, '')

  return trimmed ? `${whole.toLocaleString()}.${trimmed}` : whole.toLocaleString()
}

function parseTao(value: string, decimals = DEFAULT_DECIMALS) {
  const normalized = value.trim()

  if (!/^\d+(\.\d+)?$/.test(normalized)) {
    throw new Error('Enter a valid TAO amount.')
  }

  const [whole, fraction = ''] = normalized.split('.')

  if (fraction.length > decimals) {
    throw new Error(`TAO supports up to ${decimals} decimal places.`)
  }

  return BigInt(whole) * 10n ** BigInt(decimals) + BigInt(fraction.padEnd(decimals, '0'))
}

function toBittensorAddress(address: string) {
  return encodeAddress(decodeAddress(address), SS58_BITTENSOR)
}

function getExtensionLabel(account: InjectedAccountWithMeta) {
  return account.meta.source ? `${account.meta.name ?? 'Account'} via ${account.meta.source}` : account.meta.name
}

export default function App() {
  const [networkId, setNetworkId] = useState<NetworkId>('finney')
  const [api, setApi] = useState<ApiPromise | null>(null)
  const [chainStatus, setChainStatus] = useState<Status>({
    kind: 'loading',
    message: 'Connecting to Bittensor...',
  })
  const [accounts, setAccounts] = useState<InjectedAccountWithMeta[]>([])
  const [selectedAddress, setSelectedAddress] = useState('')
  const [balance, setBalance] = useState<bigint | null>(null)
  const [block, setBlock] = useState<number | null>(null)
  const [decimals, setDecimals] = useState(DEFAULT_DECIMALS)
  const [walletStatus, setWalletStatus] = useState<Status>({ kind: 'idle', message: 'Wallet not connected.' })
  const [recipient, setRecipient] = useState('')
  const [amount, setAmount] = useState('')
  const [memo, setMemo] = useState('')
  const [isSending, setIsSending] = useState(false)

  const network = useMemo(() => getNetwork(networkId), [networkId])
  const selectedAccount = useMemo(
    () => accounts.find((account) => account.address === selectedAddress),
    [accounts, selectedAddress],
  )
  const bittensorAddress = selectedAccount ? toBittensorAddress(selectedAccount.address) : ''
  const recipientIsValid = useMemo(() => {
    if (!recipient.trim()) {
      return false
    }

    try {
      toBittensorAddress(recipient.trim())
      return true
    } catch {
      return false
    }
  }, [recipient])

  useEffect(() => {
    let disposed = false
    let currentApi: ApiPromise | null = null

    async function connectChain() {
      setChainStatus({ kind: 'loading', message: `Opening ${network.label} RPC connection...` })
      setApi(null)
      setBalance(null)
      setBlock(null)

      try {
        await cryptoWaitReady()
        const provider = new WsProvider(network.endpoint)
        currentApi = await ApiPromise.create({ provider })
        await currentApi.isReady

        if (disposed) {
          await currentApi.disconnect()
          return
        }

        const chainDecimals = currentApi.registry.chainDecimals[0] ?? DEFAULT_DECIMALS
        setDecimals(chainDecimals)
        setApi(currentApi)
        setChainStatus({ kind: 'success', message: `Connected to ${network.label}.` })

        const header = await currentApi.rpc.chain.getHeader()
        setBlock(header.number.toNumber())
      } catch (error) {
        setChainStatus({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Could not connect to the selected network.',
        })
      }
    }

    connectChain()

    return () => {
      disposed = true
      if (currentApi) {
        currentApi.disconnect().catch(() => undefined)
      }
    }
  }, [network])

  async function refreshBalance(address = selectedAddress) {
    if (!api || !address) {
      return
    }

    const account = (await api.query.system.account(address)) as unknown as AccountInfo
    const free = account.data.free.toBigInt()
    setBalance(free)

    const header = await api.rpc.chain.getHeader()
    setBlock(header.number.toNumber())
  }

  async function connectWallet() {
    setWalletStatus({ kind: 'loading', message: 'Waiting for browser wallet approval...' })

    try {
      const extensions = await web3Enable('TaoVault')

      if (extensions.length === 0) {
        setWalletStatus({
          kind: 'error',
          message: 'Install or unlock a Polkadot-compatible wallet extension, then try again.',
        })
        return
      }

      const injectedAccounts = await web3Accounts()
      setAccounts(injectedAccounts)

      if (injectedAccounts.length === 0) {
        setWalletStatus({ kind: 'error', message: 'No accounts were shared by the wallet extension.' })
        return
      }

      setSelectedAddress(injectedAccounts[0].address)
      setWalletStatus({ kind: 'success', message: `${injectedAccounts.length} account(s) connected.` })
      await refreshBalance(injectedAccounts[0].address)
    } catch (error) {
      setWalletStatus({
        kind: 'error',
        message: error instanceof Error ? error.message : 'Could not connect the wallet extension.',
      })
    }
  }

  async function sendTransfer() {
    if (!api || !selectedAccount) {
      setWalletStatus({ kind: 'error', message: 'Connect your wallet before sending TAO.' })
      return
    }

    if (!recipientIsValid) {
      setWalletStatus({ kind: 'error', message: 'Enter a valid Bittensor/Substrate recipient address.' })
      return
    }

    setIsSending(true)
    setWalletStatus({ kind: 'loading', message: 'Preparing transfer for signature...' })

    try {
      const raoAmount = parseTao(amount, decimals)

      if (raoAmount <= 0n) {
        throw new Error('Amount must be greater than zero.')
      }

      const injector = await web3FromAddress(selectedAccount.address)
      const destination = toBittensorAddress(recipient.trim())
      const tx = api.tx.balances.transferKeepAlive(destination, new BN(raoAmount.toString()))

      const unsub = await tx.signAndSend(
        selectedAccount.address,
        { signer: injector.signer },
        ({ status, dispatchError, txHash }) => {
          if (dispatchError) {
            const error = dispatchError.isModule
              ? api.registry.findMetaError(dispatchError.asModule).docs.join(' ')
              : dispatchError.toString()
            setWalletStatus({ kind: 'error', message: error })
            setIsSending(false)
            unsub()
            return
          }

          if (status.isInBlock || status.isFinalized) {
            setWalletStatus({
              kind: 'success',
              message: `Transfer submitted: ${txHash.toHex().slice(0, 18)}...`,
            })
            setAmount('')
            setMemo('')
            refreshBalance().catch(() => undefined)
            setIsSending(false)
            unsub()
          }
        },
      )
    } catch (error) {
      setWalletStatus({
        kind: 'error',
        message: error instanceof Error ? error.message : 'Transfer was not submitted.',
      })
      setIsSending(false)
    }
  }

  async function copyAddress() {
    if (!bittensorAddress) {
      return
    }

    await navigator.clipboard.writeText(bittensorAddress)
    setWalletStatus({ kind: 'success', message: 'Address copied to clipboard.' })
  }

  async function handleAccountChange(address: string) {
    setSelectedAddress(address)
    await refreshBalance(address)
  }

  return (
    <main className="shell">
      <section className="hero-band">
        <div className="brand-mark" aria-hidden="true">
          <Wallet size={30} />
        </div>
        <div>
          <p className="eyebrow">Bittensor wallet</p>
          <h1>TaoVault</h1>
          <p className="lede">
            Manage TAO with extension-based signing, live Subtensor balances, and explicit network control.
          </p>
        </div>
        <div className={`status-pill ${chainStatus.kind}`}>
          {chainStatus.kind === 'loading' ? <Loader2 size={16} /> : <CheckCircle2 size={16} />}
          <span>{chainStatus.message}</span>
        </div>
      </section>

      <section className="toolbar" aria-label="Network controls">
        <div className="segmented">
          {NETWORKS.map((option) => (
            <button
              className={option.id === networkId ? 'active' : ''}
              key={option.id}
              onClick={() => setNetworkId(option.id)}
              type="button"
            >
              <span>{option.label}</span>
              <small>{option.purpose}</small>
            </button>
          ))}
        </div>
        <a href="https://docs.learnbittensor.org/concepts/bittensor-networks" target="_blank" rel="noreferrer">
          Network docs
          <ExternalLink size={16} />
        </a>
      </section>

      <section className="wallet-grid">
        <div className="panel balance-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Portfolio</p>
              <h2>Available balance</h2>
            </div>
            <button
              aria-label="Refresh balance"
              className="icon-button"
              disabled={!api || !selectedAddress}
              onClick={() => refreshBalance()}
              title="Refresh balance"
              type="button"
            >
              <RefreshCw size={18} />
            </button>
          </div>

          <div className="balance-value">
            {balance === null ? '0' : formatTao(balance, decimals)}
            <span>TAO</span>
          </div>

          <dl className="stats">
            <div>
              <dt>Network</dt>
              <dd>{network.label}</dd>
            </div>
            <div>
              <dt>Latest block</dt>
              <dd>{block?.toLocaleString() ?? '...'}</dd>
            </div>
            <div>
              <dt>Decimals</dt>
              <dd>{decimals}</dd>
            </div>
          </dl>
        </div>

        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Signer</p>
              <h2>Browser wallet</h2>
            </div>
            <button className="primary-button" onClick={connectWallet} type="button">
              <Plug size={18} />
              Connect
            </button>
          </div>

          <label className="field">
            <span>Account</span>
            <select
              disabled={accounts.length === 0}
              onChange={(event) => handleAccountChange(event.target.value)}
              value={selectedAddress}
            >
              <option value="">No account connected</option>
              {accounts.map((account) => (
                <option key={account.address} value={account.address}>
                  {getExtensionLabel(account)} - {formatAddress(toBittensorAddress(account.address))}
                </option>
              ))}
            </select>
          </label>

          <div className="address-box">
            <span>{bittensorAddress || 'Connect an extension wallet to see your Bittensor address.'}</span>
            <button
              aria-label="Copy Bittensor address"
              className="icon-button"
              disabled={!bittensorAddress}
              onClick={copyAddress}
              title="Copy address"
              type="button"
            >
              <Copy size={18} />
            </button>
          </div>
        </div>

        <div className="panel transfer-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Transfer</p>
              <h2>Send TAO</h2>
            </div>
            <ArrowDownUp size={22} />
          </div>

          <label className="field">
            <span>Recipient address</span>
            <input
              onChange={(event) => setRecipient(event.target.value)}
              placeholder="5..."
              spellCheck={false}
              value={recipient}
            />
          </label>

          <label className="field">
            <span>Amount</span>
            <div className="amount-row">
              <input
                inputMode="decimal"
                onChange={(event) => setAmount(event.target.value)}
                placeholder="0.0000"
                value={amount}
              />
              <strong>TAO</strong>
            </div>
          </label>

          <label className="field">
            <span>Memo</span>
            <input
              onChange={(event) => setMemo(event.target.value)}
              placeholder="Local note, not sent on-chain"
              value={memo}
            />
          </label>

          <button
            className="send-button"
            disabled={!api || !selectedAccount || !recipientIsValid || !amount || isSending}
            onClick={sendTransfer}
            type="button"
          >
            {isSending ? <Loader2 size={18} /> : <Send size={18} />}
            Sign and send
          </button>
        </div>

        <div className="panel security-panel">
          <div className="security-icon">
            <ShieldCheck size={28} />
          </div>
          <h2>Key-safe by design</h2>
          <p>
            TaoVault never asks for a mnemonic or private key. Transfers are composed here and signed in your installed
            wallet extension.
          </p>
          <div className={`notice ${walletStatus.kind}`}>
            <span>{walletStatus.message}</span>
          </div>
        </div>
      </section>
    </main>
  )
}
