/* ─── State ───────────────────────────────────────────────────────────────── */
const S = {
  symbol: 'BTC',
  timeframe: '1W',
  analysis: null,
  mainChart: null,
  candleSeries: null,
  rsiChart: null,
  rsiSeries: null,
  spotCvdChart: null,
  spotCvdSeries: null,
  futCvdChart: null,
  futCvdSeries: null,
  journalData: null,
  spotCvdSource: 'auto',
  futCvdSource: 'auto',
  fvgPriceLines: [],   // track FVG overlays so they can be cleared on token/TF switch
  overlayPriceLines: [], // swing high/low + realized price horizontal lines
  supertrendUpSeries: null,
  supertrendDownSeries: null,
  ichimokuSpanASeries: null,
  ichimokuSpanBSeries: null,
};

const API = location.port === '' || location.port === '80' || location.port === '443'
  ? '/api'
  : `${location.protocol}//${location.hostname}:8000/api`;

/* ─── Formatting helpers ──────────────────────────────────────────────────── */
const fmt = (v, d = 4) => v == null ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
// Smart price formatter: adapts decimal places to price magnitude
const fmtPrice = v => {
  if (v == null) return '—';
  const n = Math.abs(Number(v));
  let d;
  if      (n >= 1000) d = 2;
  else if (n >= 1)    d = 4;
  else if (n >= 0.1)  d = 5;
  else if (n >= 0.01) d = 6;
  else                d = 8;
  return '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
};
const fmtK = (v) => {
  if (v == null) return '—';
  const n = Number(v);
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(2)}K`;
  return `$${n.toFixed(2)}`;
};
const pct = (v) => v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
const ts = (ms) => new Date(ms).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });

/* ─── Chart colour theme ──────────────────────────────────────────────────── */
const CHART_OPTS = {
  layout: { background: { color: '#111827' }, textColor: '#94a3b8', fontSize: 13 },
  grid: { vertLines: { color: '#1e2d44' }, horzLines: { color: '#1e2d44' } },
  crosshair: { mode: 1 },
  rightPriceScale: { borderColor: '#1e2d44' },
  timeScale: { borderColor: '#1e2d44', timeVisible: true },
};

/* ─── Init charts ─────────────────────────────────────────────────────────── */
function initCharts() {
  const mainEl = document.getElementById('mainChart');
  S.mainChart = LightweightCharts.createChart(mainEl, {
    ...CHART_OPTS,
    width: mainEl.clientWidth,
    height: mainEl.clientHeight || 380,
  });
  S.candleSeries = S.mainChart.addCandlestickSeries({
    upColor: '#10b981', downColor: '#ef4444',
    borderUpColor: '#10b981', borderDownColor: '#ef4444',
    wickUpColor: '#10b981', wickDownColor: '#ef4444',
  });

  // SuperTrend — blue while bullish, orange while bearish. Deliberately NOT
  // green/red so it never reads as another FVG zone or candle color.
  // autoscaleInfoProvider: () => null prevents these overlay series from
  // stretching the Y-axis — only candles drive the price scale.
  const _overlayOpts = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null };
  S.supertrendUpSeries   = S.mainChart.addLineSeries({ ..._overlayOpts, color: '#3b82f6', lineWidth: 3 });
  S.supertrendDownSeries = S.mainChart.addLineSeries({ ..._overlayOpts, color: '#fb923c', lineWidth: 3 });

  // Ichimoku cloud boundaries (Span A / Span B) — purple/cyan, distinct from
  // every other overlay color on the chart.
  S.ichimokuSpanASeries = S.mainChart.addLineSeries({ ..._overlayOpts, color: '#a855f7', lineWidth: 2, lineStyle: 2 });
  S.ichimokuSpanBSeries = S.mainChart.addLineSeries({ ..._overlayOpts, color: '#22d3ee', lineWidth: 2, lineStyle: 2 });

  // Auto-drawn diagonal trendlines. LOCAL = near-price actionable line (solid,
  // thick, red=resistance / green=support); MACRO = multi-week context line
  // (grey, dashed). Colours match the chart legend.
  S.trendlineSeries      = S.mainChart.addLineSeries({ ..._overlayOpts, color: '#ef4444', lineWidth: 3 });
  S.trendlineMacroSeries = S.mainChart.addLineSeries({ ..._overlayOpts, color: '#cbd5e1', lineWidth: 2, lineStyle: 2 });

  // EMA 50 (pink) and EMA 200 (yellow) — the 200 is the key dynamic S/R the
  // "200 EMA retest" play trades off. autoscale off so a far EMA200 can't
  // stretch the price axis (candles drive the scale).
  S.ema50Series  = S.mainChart.addLineSeries({ ..._overlayOpts, color: '#f472b6', lineWidth: 1 });
  S.ema200Series = S.mainChart.addLineSeries({ ..._overlayOpts, color: '#facc15', lineWidth: 2 });

  const rsiEl = document.getElementById('rsiChart');
  S.rsiChart = LightweightCharts.createChart(rsiEl, {
    ...CHART_OPTS,
    width: rsiEl.clientWidth,
    height: 120,
  });
  S.rsiSeries = S.rsiChart.addLineSeries({ color: '#f59e0b', lineWidth: 2 });

  // OB/OS reference lines
  S.rsiChart.addLineSeries({ color: '#ef444455', lineWidth: 1, lineStyle: 2 })
    .setData([{ time: Date.now() / 1000 - 9e7, value: 70 }, { time: Date.now() / 1000, value: 70 }]);
  S.rsiChart.addLineSeries({ color: '#10b98155', lineWidth: 1, lineStyle: 2 })
    .setData([{ time: Date.now() / 1000 - 9e7, value: 30 }, { time: Date.now() / 1000, value: 30 }]);

  // CVD mini charts
  const sEl = document.getElementById('spotCvdChart');
  S.spotCvdChart = LightweightCharts.createChart(sEl, {
    ...CHART_OPTS, width: sEl.clientWidth, height: 80,
    rightPriceScale: { visible: false },
    timeScale: { visible: false },
  });
  S.spotCvdSeries = S.spotCvdChart.addAreaSeries({ lineColor: '#10b981', topColor: '#10b98133', bottomColor: '#10b98100', lineWidth: 2 });

  const fEl = document.getElementById('futCvdChart');
  S.futCvdChart = LightweightCharts.createChart(fEl, {
    ...CHART_OPTS, width: fEl.clientWidth, height: 80,
    rightPriceScale: { visible: false },
    timeScale: { visible: false },
  });
  S.futCvdSeries = S.futCvdChart.addAreaSeries({ lineColor: '#6366f1', topColor: '#6366f133', bottomColor: '#6366f100', lineWidth: 2 });

  // Keep every chart sized to its container. ResizeObserver fires on ANY
  // layout change — phone rotation, tablet sidebar wrap, PWA window resize,
  // browser zoom — where window 'resize' alone is unreliable (mobile rotation
  // and CSS-driven wraps often don't dispatch it). CVD minis included: they
  // were never resized before and broke when the sidebar reflowed.
  const _fit = () => {
    if (mainEl.clientWidth) S.mainChart.resize(mainEl.clientWidth, mainEl.clientHeight || 420);
    if (rsiEl.clientWidth)  S.rsiChart.resize(rsiEl.clientWidth, rsiEl.clientHeight || 120);
    if (sEl.clientWidth)    S.spotCvdChart.resize(sEl.clientWidth, sEl.clientHeight || 80);
    if (fEl.clientWidth)    S.futCvdChart.resize(fEl.clientWidth, fEl.clientHeight || 80);
    // Re-fit the visible range after the box changes. Without this, a chart
    // that GROWS (sidebar reflow, font load, rotation) keeps its old range
    // anchored left and the right half of the canvas stays blank — candles and
    // trendlines look like they "stop" mid-chart.
    try { S.mainChart.timeScale().fitContent(); } catch (_) {}
    try { S.rsiChart.timeScale().fitContent(); } catch (_) {}
    try { S.spotCvdChart.timeScale().fitContent(); } catch (_) {}
    try { S.futCvdChart.timeScale().fitContent(); } catch (_) {}
  };
  let _fitRaf = null;
  const _fitSoon = () => {           // coalesce bursts of observer callbacks
    if (_fitRaf) return;
    _fitRaf = requestAnimationFrame(() => { _fitRaf = null; _fit(); });
  };
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(_fitSoon);
    [mainEl, rsiEl, sEl, fEl].forEach(el => ro.observe(el));
  }
  window.addEventListener('resize', _fitSoon);
  window.addEventListener('orientationchange', () => setTimeout(_fit, 250));
}

/* ─── Fetch dashboard overview ────────────────────────────────────────────── */
async function loadTicker() {
  const TICKER_SYMS = ['BTC', 'ETH', 'LINK', 'TAO', 'HYPE', 'ONDO'];
  try {
    const res = await fetch(`${API}/dashboard`);
    if (!res.ok) return;
    const data = await res.json();
    const bar = document.getElementById('tickerBar');
    bar.innerHTML = TICKER_SYMS.map(sym => {
      const d = data[sym];
      if (!d || d.error) return '';
      const chg = d.change_pct ?? 0;
      const cls = chg >= 0 ? 'up' : 'dn';
      return `<div class="ticker-item">
        <span class="ticker-sym">${sym}</span>
        <span class="ticker-price">${fmtPrice(d.price || 0)}</span>
        <span class="ticker-chg ${cls}">${pct(chg)}</span>
      </div>`;
    }).join('');
  } catch (_) {}
}

/* ─── Main data load ──────────────────────────────────────────────────────── */
async function loadAnalysis() {
  setLoading(true);
  try {
    const res = await fetch(`${API}/analysis/${S.symbol}?timeframe=${S.timeframe}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    S.analysis = await res.json();
    renderAll(S.analysis);
    renderMyTrades();
    document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch (e) {
    console.error('Analysis failed:', e);
    showError(e.message);
  } finally {
    setLoading(false);
  }
}

function renderAll(a) {
  // Show data source banner
  const banner = document.getElementById('demoBanner');
  const src = a.data_source || 'demo';
  const cgSuffix = a.coinglass_enabled ? ' + <strong>CoinGlass</strong> derivatives.' : '';
  const srcLabels = {
    binance:   a.coinglass_enabled ? ['🟢', 'Binance', `Live data via Binance.${cgSuffix}`, 'cg-banner'] : null,
    coingecko: ['🟡', 'CoinGecko', `Live price & volume via CoinGecko. Derivatives estimated.${cgSuffix}`, 'cg-banner'],
    kraken:    ['🟢', 'Kraken',    `Live OHLCV via Kraken.${cgSuffix}`, 'cg-banner'],
    gateio:    ['🟢', 'Gate.io',   `Live OHLCV via Gate.io.${cgSuffix}`, 'cg-banner'],
    kucoin:    ['🟢', 'KuCoin',    `Live OHLCV via KuCoin.${cgSuffix}`, 'cg-banner'],
    demo:      ['⚡', 'Demo Mode', 'All APIs unreachable. Synthetic data shown. Check <a href="/api/diagnostics" target="_blank">diagnostics</a>.', ''],
  };
  const info = srcLabels[src];
  if (!info) {
    banner.className = 'demo-banner cg-banner';
    banner.innerHTML = `● <strong>${src.toUpperCase()}</strong> — Live OHLCV via ${src.toUpperCase()}.${cgSuffix}`;
  } else {
    banner.className = `demo-banner ${info[3]}`;
    banner.innerHTML = `${info[0]} <strong>${info[1]}</strong> — ${info[2]}`;
  }

  renderPrice(a);
  renderSignal(a.signal);
  renderMACDCard(a.macd);
  renderNewsCard(a.news);
  renderEMACard(a.ema_trend);
  renderSupertrendCard(a.supertrend);
  renderIchimokuCard(a.ichimoku);
  renderBollingerCard(a.bollinger);
  renderRsiDivCard(a.rsi_divergence);
  renderVwapCard(a.vwap);
  renderStochRsiCard(a.stoch_rsi);
  renderVolSignalCard(a.vol_signal);
  renderBtcMiningCard(a.btc_mining, a.symbol);
  renderOnchainMetrics(a.btc_mining, a.symbol, a.lth_supply);
  renderGoMiningAdvisor(a.gomining_strategy, a.symbol, a.gomining_token_signal);
  renderLSCard(a.long_short);
  renderWhaleActivity(a.whale_activity || []);
  renderArkhamPanel(a.whale_sells);
  renderEtfFlows(a.etf_flows, a.symbol);
  renderGtk(a.gomining_tokenomics, a.symbol);
  renderTaoEco(a.tao_ecosystem, a.symbol);
  renderTaoFlowHist(a.tao_ecosystem, a.symbol);
  renderMarketContext(a.markets, a.regime);
  trackSignal(a);
  evaluateSignals(a);
  renderFNGCard(a.fear_greed);
  renderRSICard(a.rsi);
  renderFunding(a.funding_rate);
  renderOI(a.open_interest);
  renderOiRotation(a.regime, a.symbol);
  renderLiquidations(a.liquidations);
  renderMarketCap(a.market_cap);
  renderMainChart(a.candles, a.fvgs, a.supertrend, a.ichimoku, a.btc_mining, a.symbol, a.trendline, a.sr_zones, a.ema_lines, a.htf_levels);
  renderRSIChart(a.rsi_series);
  renderCVDCharts(a.spot_cvd, a.agg_cvd || a.futures_cvd, a.futures_available);
  renderCVDDivergence(a.cvd_divergence);
  renderFVGTable(a.fvgs);
  renderFlags(a.flags, a.candles, a.signal);
  renderEngulfing(a.engulfing, a.timeframe);
  renderTradeManagement(a);
  renderElliottWave(a.elliott_wave);
  renderConfluence(a.signal);
  renderHtfConfluence(a);
  renderBtcContext(a);
  renderOrderBook(a.order_book);
  renderHolidayBanner(a.upcoming_holidays);
  renderOptionsBanner(a.options_expiry);
  applyHtfContext(a.timeframe);
  document.getElementById('chartTitle').textContent = `${a.symbol}/USDT · ${a.timeframe}`;
}

/* ─── Higher-timeframe context tagging ─────────────────────────────────────────
   ETF flows, macro, BTC on-chain, market regime, GoMining & TAO tokenomics are
   all daily → multi-week signals. On 1H/2H they can't move an intraday candle,
   so they're de-weighted in scoring (backend) AND flagged with a badge + dimmed
   here so the user reads them as reference context, not intraday triggers. */
function applyHtfContext(tf) {
  const lowTf = ['1H', '2H'].includes(tf);
  const sections = ['onchainMetricsSection', 'gominingSection', 'etfFlowsSection',
                    'taoEcoSection', 'cryptoRegimeSection', 'macroSection'];
  sections.forEach(id => {
    const sec = document.getElementById(id);
    if (!sec) return;
    sec.classList.toggle('htf-dim', lowTf);
    let badge = sec.querySelector('.htf-badge');
    if (lowTf && !badge) {
      const host = sec.querySelector('.card-title, .card-header, .card');
      if (host) {
        badge = document.createElement('span');
        badge.className = 'htf-badge';
        badge.textContent = '🗓️ daily+ context';
        host.insertBefore(badge, host.firstChild);
      }
    } else if (!lowTf && badge) {
      badge.remove();
    } else if (badge) {
      badge.textContent = '🗓️ daily+ context';
    }
  });
}

/* ─── Price panel ─────────────────────────────────────────────────────────── */
function renderPrice(a) {
  const c = a.candles;
  if (!c?.length) return;
  // a.candles is CLOSED candles only now; the live (forming) candle ships
  // separately. Display the live price when available, falling back to the
  // last closed close for older cached payloads.
  const closedLast = c[c.length - 1];
  const closedPrev = c.length > 1 ? c[c.length - 2] : closedLast;
  const last   = a.live_candle || closedLast;                 // for H/L/Vol
  const livePx = (a.live_price != null) ? a.live_price : closedLast.close;
  // % change of the current bar: vs last closed if a candle is forming,
  // else last closed vs the previous closed candle.
  const base = (a.live_candle ? closedLast.close : closedPrev.close) || livePx;
  const chg = base ? (livePx - base) / base * 100 : 0;
  const up = chg >= 0;

  document.getElementById('priceSymbol').textContent = `${a.symbol}/USDT`;
  document.getElementById('priceValue').textContent = fmtPrice(livePx);
  const chgEl = document.getElementById('priceChange');
  chgEl.textContent = `${up ? '▲' : '▼'} ${pct(chg)}`;
  chgEl.className = `price-change ${up ? 'up' : 'dn'}`;
  const periodEl = document.getElementById('priceChangePeriod');
  if (periodEl) {
    // "as of" = the live candle's fetch moment. Each timeframe is cached
    // independently for up to 30 min, so small price differences between TFs
    // are timing + per-TF data source, not bad data.
    const src = a.data_source ? ` · ${a.data_source}` : '';
    const asOf = a.generated_at ? new Date(a.generated_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
                                : new Date(last.timestamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    periodEl.textContent = `${a.timeframe} change · as of ${asOf}${src}`;
  }
  document.getElementById('priceHigh').textContent = `H: ${fmtPrice(last.high)}`;
  document.getElementById('priceLow').textContent  = `L: ${fmtPrice(last.low)}`;
  document.getElementById('priceVol').textContent  = `Vol: ${fmtK(last.volume)}`;

  // Data-quality chip — signals are computed on the last CLOSED candle. Warn
  // when the data isn't clean enough to trade on.
  const dqEl = document.getElementById('priceDataQuality');
  if (dqEl) {
    const dq = a.data_quality || 'good';
    const rs = (a.data_quality_reasons || []).join(' · ');
    if (dq === 'good') {
      dqEl.style.display = 'none';
    } else {
      dqEl.style.display = '';
      dqEl.className = `price-dq ${dq === 'invalid' ? 'dq-invalid' : 'dq-degraded'}`;
      dqEl.textContent = dq === 'invalid' ? `⛔ Not tradeable — ${rs}` : `⚠️ Degraded data — ${rs}`;
      dqEl.title = 'Signals are computed on the last closed candle. ' +
        (a.signal_price != null ? `Signal price ${fmtPrice(a.signal_price)}.` : '');
    }
  }
}

/* ─── Signal panel ────────────────────────────────────────────────────────── */
function renderSignal(s) {
  if (!s) return;
  const dir = s.direction || 'NEUTRAL';
  const str = s.strength || 0;

  const dirEl = document.getElementById('signalDir');
  dirEl.textContent = dir;
  dirEl.className = `signal-direction ${dir}`;

  const bar = document.getElementById('signalBar');
  bar.style.width = `${str}%`;
  bar.className = `signal-bar ${dir === 'LONG' ? 'bull' : dir === 'SHORT' ? 'bear' : ''}`;
  document.getElementById('signalStrength').textContent = `${str}/100`;

  // Tier badge + position size guide
  const tierWrap  = document.getElementById('signalTierWrap');
  const tierBadge = document.getElementById('signalTierBadge');
  const sizeGuide = document.getElementById('signalSizeGuide');
  if (s.tier && dir !== 'NEUTRAL') {
    const tierCls = { Weak: 'tier-weak', Moderate: 'tier-moderate', Strong: 'tier-strong', Confirmed: 'tier-confirmed' };
    tierBadge.textContent  = s.tier;
    tierBadge.className    = `signal-tier-badge ${tierCls[s.tier] || ''}`;
    sizeGuide.textContent  = s.size_guide || '';
    tierWrap.style.display = '';
  } else {
    tierWrap.style.display = 'none';
  }

  // Exhaustion & reversal analysis for this specific TF
  const exhEl = document.getElementById('signalExhaustion');
  if (exhEl) {
    const exhFlag  = s.exhaustion_flag;
    const revCount = s.reversal_count || 0;
    const parts = [];
    if (exhFlag) {
      const zone = dir === 'LONG' ? 'overbought' : 'oversold';
      parts.push(`<span class="sig-exh-warn">⚠ ${zone} at this TF — entry is overextended</span>`);
    }
    if (revCount > 0) {
      const names  = s.flipped_indicators || [];
      const label  = revCount === 1 ? '1 indicator just flipped' : `${revCount} indicators just flipped`;
      const detail = names.length
        ? `<div class="sig-flip-list">${names.map(n => `<span class="sig-flip-item">⚡ ${n}</span>`).join('')}</div>`
        : '';
      parts.push(`<span class="sig-rev-badge">⚡ ${label} direction</span>${detail}`);
    }
    if (parts.length) {
      exhEl.innerHTML = parts.join('');
      exhEl.style.display = '';
    } else {
      exhEl.innerHTML = '';
      exhEl.style.display = 'none';
    }
  }

  // Reversal Radar — exhaustion (in uptrends) / bottoming (in downtrends)
  const radEl = document.getElementById('signalRadar');
  if (radEl) {
    const rr = s.reversal_radar;
    if (rr && rr.mode && (rr.count > 0 || rr.level !== 'low')) {
      const isTop = rr.mode === 'top';
      const lvl   = rr.level || 'low';
      const lvlCls = { low: 'rr-low', building: 'rr-building', elevated: 'rr-elevated', high: 'rr-high' }[lvl] || 'rr-low';
      const icon  = isTop ? '🛑' : '🟢';
      const title = isTop ? 'TOPPING RISK' : 'BOTTOMING WATCH';
      const gauge = rr.applicable ? Math.round((rr.count / rr.applicable) * 100) : 0;
      const sigs  = (rr.signals || []).map(x =>
        `<div class="rr-sig"><span class="rr-sig-label">${isTop ? '▼' : '▲'} ${x.label}</span>${x.note ? `<span class="rr-sig-note">${x.note}</span>` : ''}</div>`
      ).join('');
      radEl.className = `reversal-radar ${lvlCls} ${isTop ? 'rr-top' : 'rr-bottom'}`;
      radEl.innerHTML = `
        <div class="rr-head">
          <span class="rr-title">${icon} REVERSAL RADAR · ${title}</span>
          <span class="rr-count">${rr.count}/${rr.applicable}</span>
        </div>
        <div class="rr-gauge-bg"><div class="rr-gauge-bar" style="width:${gauge}%"></div></div>
        <div class="rr-level">${lvl.toUpperCase()} — ${rr.verdict || ''}</div>
        ${sigs ? `<div class="rr-sigs">${sigs}</div>` : ''}`;
      radEl.style.display = '';
    } else {
      radEl.innerHTML = '';
      radEl.style.display = 'none';
    }
  }

  // Squeeze priming — funding ↔ CVD divergence (set up vs primed)
  const sqEl = document.getElementById('signalSqueeze');
  if (sqEl) {
    const sq = s.squeeze_priming;
    if (sq && sq.mode) {
      const isShort = sq.mode === 'short_squeeze';
      const primed  = sq.state === 'primed';
      const dirTxt  = isShort ? 'SHORT squeeze' : 'LONG squeeze';
      const arrow   = isShort ? '↑ bounce risk' : '↓ flush risk';
      const state   = primed ? 'PRIMED' : 'building';
      const fr      = sq.funding != null ? `funding ${Number(sq.funding).toFixed(4)}%` : '';
      const lev     = sq.leverage_only ? ' · leverage-only' : '';
      sqEl.className = `squeeze-chip ${primed ? 'sq-primed' : 'sq-building'} ${isShort ? 'sq-short' : 'sq-long'}`;
      sqEl.innerHTML =
        `<div class="sq-head"><span class="sq-title">${primed ? '🎯' : '👀'} ${dirTxt} — ${state}</span>` +
        `<span class="sq-dir">${arrow}</span></div>` +
        `<div class="sq-sub">${fr}${lev}${primed ? ' — crowded side is paying' : ' — watch funding to confirm'}</div>`;
      sqEl.style.display = '';
    } else {
      sqEl.innerHTML = '';
      sqEl.style.display = 'none';
    }
  }

  // SMC structure: Acc+EQL/EQH+FVG setup + CHoCH + Liquidity Grab
  const smcEl = document.getElementById('signalSMC');
  if (smcEl) {
    const acc   = s.acc_setup;
    const choch = s.choch;
    const liq   = s.liq_grab;
    const rows  = [];

    // ICT Triple-combo setup — shown first as highest-confidence signal
    if (acc) {
      const isBull = acc.signal === 'bullish';
      const cls    = isBull ? 'bull' : 'bear';
      const icon   = isBull ? '🚀 PUMP' : '💣 DUMP';
      const eq     = acc.eq_level;
      const eqTxt  = eq ? `${eq.touches}× @ ${fmtPrice(eq.price)}` : '';
      const rngTxt = acc.range ? `range ${acc.range.range_pct}%` : '';
      rows.push(`<div class="smc-row smc-acc-setup">
        <span class="smc-label">ICT Setup</span>
        <span class="smc-val ${cls}">${icon}</span>
        <span class="smc-sub">${isBull ? 'EQL' : 'EQH'} ${eqTxt} · ${rngTxt} · strength ${acc.strength}</span>
      </div>`);
    }

    if (choch) {
      const cls  = choch.signal === 'bullish' ? 'bull' : 'bear';
      const icon = choch.signal === 'bullish' ? '▲' : '▼';
      const age  = choch.candles_ago === 0 ? 'this candle' : `${choch.candles_ago}c ago`;
      rows.push(`<div class="smc-row">
        <span class="smc-label">CHoCH</span>
        <span class="smc-val ${cls}">${icon} ${choch.signal.charAt(0).toUpperCase()+choch.signal.slice(1)}</span>
        <span class="smc-sub">${choch.label} · ${age}</span>
      </div>`);
    }

    if (liq) {
      const cls  = liq.signal === 'bullish' ? 'bull' : 'bear';
      const icon = liq.signal === 'bullish' ? '⚡↑' : '⚡↓';
      const age  = liq.candles_ago === 0 ? 'this candle' : `${liq.candles_ago}c ago`;
      rows.push(`<div class="smc-row">
        <span class="smc-label">Liq. Grab</span>
        <span class="smc-val ${cls}">${icon} ${liq.signal.charAt(0).toUpperCase()+liq.signal.slice(1)}</span>
        <span class="smc-sub">${liq.label} · ${age}</span>
      </div>`);
    }

    if (rows.length) {
      smcEl.innerHTML = `<div class="smc-header">SMART MONEY STRUCTURE</div>${rows.join('')}`;
      smcEl.style.display = '';
    } else {
      smcEl.innerHTML = '';
      smcEl.style.display = 'none';
    }
  }

  const price = (n) => n ? `$${Number(n).toLocaleString('en-US', { maximumFractionDigits: 4 })}` : '—';
  document.getElementById('lvlEntry').textContent = price(s.entry);
  document.getElementById('lvlSL').textContent    = price(s.sl);
  const tps = s.tp_targets || [];
  document.getElementById('lvlTP1').textContent = price(tps[0]);
  document.getElementById('lvlTP2').textContent = price(tps[1]);
  document.getElementById('lvlTP3').textContent = price(tps[2]);
  document.getElementById('lvlRR').textContent  = s.rr_ratio ? `${s.rr_ratio}x` : '—';
}

/* ─── RSI gauge (canvas arc) ──────────────────────────────────────────────── */
function renderRSICard(rsi) {
  const val = rsi ?? 50;
  document.getElementById('rsiValue').textContent = rsi != null ? rsi.toFixed(1) : '—';
  let label, color;
  if (val < 30)       { label = 'OVERSOLD'; color = '#10b981'; }
  else if (val > 70)  { label = 'OVERBOUGHT'; color = '#ef4444'; }
  else if (val < 45)  { label = 'Bearish Zone'; color = '#f59e0b'; }
  else if (val > 55)  { label = 'Bullish Zone'; color = '#f59e0b'; }
  else                { label = 'Neutral'; color = '#6366f1'; }
  document.getElementById('rsiLabel').textContent = label;
  document.getElementById('rsiValue').style.color = color;

  const canvas = document.getElementById('rsiGauge');
  const ctx = canvas.getContext('2d');
  const W = Math.min(120, canvas.parentElement.clientWidth - 8);
  const H = Math.round(W * 70 / 120);
  canvas.width = W; canvas.height = H;
  ctx.clearRect(0, 0, W, H);

  const cx = W / 2, cy = H - 6, r = Math.min(cx, cy) - 4;
  const start = Math.PI, end = 0;

  // Background arc
  ctx.beginPath();
  ctx.arc(cx, cy, r, start, end);
  ctx.strokeStyle = '#1e2d44';
  ctx.lineWidth = 10;
  ctx.lineCap = 'round';
  ctx.stroke();

  // Value arc
  const angle = start + (val / 100) * Math.PI;
  ctx.beginPath();
  ctx.arc(cx, cy, r, start, angle);
  ctx.strokeStyle = color;
  ctx.stroke();

  // Needle
  const nx = cx + (r - 5) * Math.cos(angle);
  const ny = cy + (r - 5) * Math.sin(angle);
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(nx, ny);
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 2;
  ctx.stroke();
}

/* ─── Funding Rate ────────────────────────────────────────────────────────── */
function renderFunding(f) {
  if (!f) return;
  const cur = f.current ?? 0;
  const el = document.getElementById('fundingValue');
  el.textContent = `${cur >= 0 ? '+' : ''}${cur.toFixed(4)}%`;
  el.style.color = cur >= 0 ? 'var(--bear)' : 'var(--bull)';
  document.getElementById('fundingLabel').textContent =
    cur > 0.02 ? 'Longs paying — bearish signal' :
    cur < -0.01 ? 'Shorts paying — bullish signal' :
    'Neutral funding';

  const hist = f.history || [];
  const maxAbs = Math.max(...hist.map(h => Math.abs(h.rate)), 0.001);
  const barsEl = document.getElementById('fundingHistory');
  barsEl.innerHTML = hist.slice(-12).map(h => {
    const pxH = Math.max(4, Math.abs(h.rate) / maxAbs * 28);
    const cls = h.rate >= 0 ? 'pos' : 'neg';
    return `<div class="mini-bar ${cls}" style="height:${pxH}px" title="${h.rate.toFixed(4)}%"></div>`;
  }).join('');
}

/* ─── Open Interest ───────────────────────────────────────────────────────── */
/* ─── ALT/BTC OI rotation badge on the Open Interest card ─────────────────── */
function renderOiRotation(regime, symbol) {
  const el = document.getElementById('oiRotation');
  if (!el) return;
  const oi = regime?.oi;
  if (!oi || oi.alt_btc_ratio == null) { el.style.display = 'none'; return; }
  const cls = oi.zone === 'room-to-run' ? 'bull'
            : (oi.zone === 'alt-froth' || oi.zone === 'heating') ? 'bear' : '';
  const lbl = oi.zone === 'alt-froth' ? '⚠ exit alts'
            : oi.zone === 'heating' ? 'heating'
            : oi.zone === 'room-to-run' ? 'room to run' : 'balanced';
  el.style.display = '';
  el.className = `oi-rotation ${cls}`;
  el.title = `${oi.note || ''} · BTC OI $${oi.btc_oi_b}B · ETH $${oi.eth_oi_b}B · ALTs $${oi.alt_oi_b}B (OKX perps)`;
  el.textContent = `ALT/BTC ${oi.alt_btc_ratio} — ${lbl}`;
}

function renderOI(oi) {
  if (!oi) return;
  const valEl = document.getElementById('oiValue');
  valEl.textContent = fmtK(oi.value);
  // Flag when the value is a mock estimate (no live perp data reachable) vs a
  // live exchange source, so a fixed fallback number can't be mistaken for real.
  const src = oi.source;
  if (src === 'mock') {
    valEl.title = 'Estimated — live open-interest source unavailable';
    valEl.style.opacity = '0.55';
  } else {
    valEl.title = src ? `Live OI (USD) via ${src.toUpperCase()}` : '';
    valEl.style.opacity = '';
  }
  const badge = document.getElementById('oiChange');
  const chg = oi.change_pct ?? 0;
  badge.textContent = pct(chg);
  badge.className = `metric-badge ${chg >= 0 ? 'up' : 'dn'}`;
  badge.title = oi.window_bars ? `OI change over ~5 candles of this timeframe (${oi.window_bars}×${oi.period} bars)` : '';

  // OI-price quadrant + squeeze read — who is entering the market right now
  const qEl = document.getElementById('oiQuadrant');
  if (qEl) {
    const M = {
      shorts_building: ['⛽ Shorts crowding in — OI↑ while price↓', 'oiq-bull', 'short-squeeze fuel building'],
      longs_building:  ['📈 New longs opening — OI↑ with price↑',   'oiq-neut', 'trend conviction'],
      short_covering:  ['🔄 Short covering — OI↓ with price↑',      'oiq-warn', 'rally without new money — weaker'],
      long_liquidation:['🧯 Long liquidation — OI↓ with price↓',    'oiq-bear', 'deleveraging / capitulation'],
    };
    if (oi.quadrant && M[oi.quadrant]) {
      let [txt, cls, sub] = M[oi.quadrant];
      if (oi.squeeze === 'short_squeeze_fuel') { cls = 'oiq-bull'; sub = 'SHORT-SQUEEZE fuel — funding flat/negative; a bounce forces shorts to buy back'; }
      if (oi.squeeze === 'long_squeeze_risk')  { cls = 'oiq-bear'; sub = 'LONG-SQUEEZE risk — hot funding; dips get violent'; }
      qEl.innerHTML = `<div class="oi-quad ${cls}">${txt}</div><div class="oi-quad-sub">${sub}</div>`;
      qEl.style.display = '';
    } else {
      qEl.style.display = 'none';
    }
  }

  // Sparkline
  const hist = oi.history || [];
  if (hist.length < 2) return;
  const canvas = document.getElementById('oiSparkline');
  if (!canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.clientWidth || 160, H = 40;
  canvas.width = W; canvas.height = H;
  const vals = hist.map(h => h.oi);
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = mx - mn || 1;
  const pts = vals.map((v, i) => [i / (vals.length - 1) * W, H - ((v - mn) / rng) * (H - 4) - 2]);
  ctx.beginPath();
  ctx.moveTo(...pts[0]);
  pts.slice(1).forEach(p => ctx.lineTo(...p));
  ctx.strokeStyle = chg >= 0 ? '#10b981' : '#ef4444';
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

/* ─── Liquidations ────────────────────────────────────────────────────────── */
function renderLiquidations(l) {
  if (!l) return;
  document.getElementById('liqLongs').textContent  = fmtK(l.longs_liquidated);
  document.getElementById('liqShorts').textContent = fmtK(l.shorts_liquidated);
  document.getElementById('liqTotal').textContent  = `Total: ${fmtK(l.total)}`;
}

/* ─── Main candlestick chart ──────────────────────────────────────────────── */
function renderMarketCap(mcap) {
  const valEl  = document.getElementById('mcapValue');
  const rankEl = document.getElementById('mcapRank');
  if (!valEl) return;
  if (!mcap) { valEl.textContent = '—'; rankEl.textContent = 'via CoinGecko'; return; }
  const fmt = mcap >= 1e12 ? `$${(mcap/1e12).toFixed(2)}T`
            : mcap >= 1e9  ? `$${(mcap/1e9).toFixed(2)}B`
            : mcap >= 1e6  ? `$${(mcap/1e6).toFixed(1)}M`
            : `$${mcap.toLocaleString()}`;
  valEl.textContent  = fmt;
  rankEl.textContent = 'Live · CoinGecko';
}

function renderMainChart(candles, fvgs, supertrend, ichimoku, btcMining, symbol, trendline, srZones, emaLines, htfLevels) {
  if (!candles?.length || !S.candleSeries) return;

  // Clear FVG + swing/realized price lines and wave markers from the previous token/TF.
  S.fvgPriceLines.forEach(pl => { try { S.candleSeries.removePriceLine(pl); } catch (_) {} });
  S.fvgPriceLines = [];
  S.overlayPriceLines.forEach(pl => { try { S.candleSeries.removePriceLine(pl); } catch (_) {} });
  S.overlayPriceLines = [];
  S.candleSeries.setMarkers([]);   // wave markers — replaced later by renderElliottWave

  // Show hours on the time axis for intraday TFs; dates only for daily+
  const intraday = ['1H', '2H', '4H', '8H', '12H'].includes(S.timeframe);
  S.mainChart.applyOptions({
    timeScale: { borderColor: '#1e2d44', timeVisible: intraday, secondsVisible: false },
  });

  // Adapt price precision to the token's magnitude so sub-dollar tokens like
  // GOMINING (~$0.28) don't collapse every level to "0.28". Precision is chosen
  // from the latest price and applied to the axis + all price-line labels.
  const lastPrice = Math.abs(candles[candles.length - 1].close) || 1;
  let prec, minMove;
  if      (lastPrice >= 1000) { prec = 2; minMove = 0.01; }
  else if (lastPrice >= 1)    { prec = 4; minMove = 0.0001; }
  else if (lastPrice >= 0.01) { prec = 5; minMove = 0.00001; }
  else                        { prec = 8; minMove = 0.00000001; }
  S.candleSeries.applyOptions({
    priceFormat: { type: 'price', precision: prec, minMove },
  });

  // candles = CLOSED candles (structure/overlays are computed from these);
  // append the live forming candle so the chart still shows current price.
  const _live = S.analysis && S.analysis.live_candle;
  const _drawCandles = (_live && _live.timestamp) ? candles.concat([_live]) : candles;
  const data = _drawCandles.map(c => ({
    time: Math.floor(c.timestamp / 1000),
    open: c.open, high: c.high, low: c.low, close: c.close,
  }));
  const unique = [...new Map(data.map(d => [d.time, d])).values()].sort((a, b) => a.time - b.time);
  S.candleSeries.setData(unique);

  // Restore autoscale on every render. Dragging/pinching the price axis turns
  // autoscale OFF permanently; the broken viewport then persists across data
  // refreshes — candles squish into a band and out-of-range level labels
  // (Swing/Realized/FVG) pile into a uniform stack that reads as "inverted".
  S.mainChart.priceScale('right').applyOptions({ autoScale: true });

  // FVG overlays — decluttered: cap at the 3 NEAREST unfilled gaps within 12%
  // of price (far-away gaps stacked labels without being tradeable), and only
  // BAG (strong) gaps get their top/bottom boundary lines — plain FVGs are a
  // single labelled midline, so the chart isn't a wall of dashed lines.
  if (fvgs?.length) {
    const px = candles[candles.length - 1].close;
    const unfilled = fvgs
      .filter(f => !f.filled && px > 0 && Math.abs(f.midpoint - px) / px <= 0.12)
      .sort((a, b) => Math.abs(a.midpoint - px) - Math.abs(b.midpoint - px))
      .slice(0, 3);
    unfilled.forEach(f => {
      const isBull = f.type === 'bullish';
      const isBag  = f.gap_type === 'bag';
      const color  = isBull ? 'rgba(16,185,129,0.6)' : 'rgba(239,68,68,0.6)';
      const dimCol = isBull ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)';
      const arrow  = isBull ? '↑' : '↓';
      const label  = isBag ? 'BAG' : 'FVG';
      if (isBag) {
        S.fvgPriceLines.push(S.candleSeries.createPriceLine({ price: f.top,    color: dimCol, lineWidth: 2, lineStyle: 2, title: '', axisLabelVisible: false }));
        S.fvgPriceLines.push(S.candleSeries.createPriceLine({ price: f.bottom, color: dimCol, lineWidth: 2, lineStyle: 2, title: '', axisLabelVisible: false }));
      }
      S.fvgPriceLines.push(S.candleSeries.createPriceLine({ price: f.midpoint, color, lineWidth: isBag ? 2 : 1, lineStyle: 3, title: `${arrow} ${label} ${f.size_pct.toFixed(1)}%` }));
    });
  }

  // SuperTrend line — split into bullish/bearish segments so color flips with trend.
  // Each series gets the full timeline but with nulls where the other trend is
  // active, so lightweight-charts draws a gap instead of a flat connecting line.
  const stSeries = supertrend?.series || [];
  if (stSeries.length) {
    const allTimes = stSeries.map(p => Math.floor(p.timestamp / 1000));
    const upData = stSeries.map((p, i) => p.trend === 'bullish' ? { time: allTimes[i], value: p.value } : null).filter(Boolean);
    const dnData = stSeries.map((p, i) => p.trend === 'bearish' ? { time: allTimes[i], value: p.value } : null).filter(Boolean);
    S.supertrendUpSeries.setData(upData);
    S.supertrendDownSeries.setData(dnData);
  } else {
    S.supertrendUpSeries.setData([]);
    S.supertrendDownSeries.setData([]);
  }

  // Ichimoku cloud — Span A / Span B boundary lines tracking every candle.
  const ichiSeries = ichimoku?.series || [];
  if (ichiSeries.length) {
    S.ichimokuSpanASeries.setData(ichiSeries.map(p => ({ time: Math.floor(p.timestamp / 1000), value: p.span_a })));
    S.ichimokuSpanBSeries.setData(ichiSeries.map(p => ({ time: Math.floor(p.timestamp / 1000), value: p.span_b })));
  } else {
    S.ichimokuSpanASeries.setData([]);
    S.ichimokuSpanBSeries.setData([]);
  }

  // Swing high/low — last 5 closed candles (skip the live/forming candle).
  const closed = unique.length >= 6 ? unique.slice(-6, -1) : unique.slice(0, -1);
  if (closed.length) {
    const swingHigh = Math.max(...closed.map(c => c.high));
    const swingLow  = Math.min(...closed.map(c => c.low));
    S.overlayPriceLines.push(S.candleSeries.createPriceLine({ price: swingHigh, color: '#94a3b899', lineWidth: 1, lineStyle: 3, title: 'Swing High' }));
    S.overlayPriceLines.push(S.candleSeries.createPriceLine({ price: swingLow,  color: '#94a3b899', lineWidth: 1, lineStyle: 3, title: 'Swing Low' }));
  }

  // ── Higher-timeframe swing levels ─────────────────────────────────────────
  // The 1D / 1W / 1M swing high & low projected onto this (lower) chart — the
  // structural levels that actually turn price. Colour-coded per anchor TF,
  // drawn as dashed horizontal lines with the price on the axis. Only levels
  // within a sane band of the visible range are drawn so a far-away monthly
  // swing can't stretch the price axis and flatten the candles.
  const _HTF_COL = { '1D': '#38bdf8', '1W': '#fb7185', '1M': '#c084fc' };
  const _lastPx = unique.length ? unique[unique.length - 1].close : 0;
  (htfLevels || []).forEach(lv => {
    const col = _HTF_COL[lv.tf] || '#94a3b8';
    [['high', lv.high, '▲'], ['low', lv.low, '▼']].forEach(([kind, price, arrow]) => {
      if (!price || !_lastPx) return;
      // skip levels too far from price — a distant monthly high/low forces the
      // price axis to stretch to it and flattens the candles. 45% keeps nearby
      // structural levels while dropping far-away macro extremes.
      if (Math.abs(price - _lastPx) / _lastPx > 0.45) return;
      S.overlayPriceLines.push(S.candleSeries.createPriceLine({
        price, color: col, lineWidth: 1, lineStyle: 2,
        title: `${lv.tf} ${arrow}`, axisLabelVisible: true,
      }));
    });
  });

  // Realized Price — BTC only, historically the strongest support/floor level.
  const rp = btcMining?.realized_price || btcMining?.mvrv?.realized_price;
  if (symbol === 'BTC' && rp) {
    S.overlayPriceLines.push(S.candleSeries.createPriceLine({ price: rp, color: '#fbbf24', lineWidth: 2, lineStyle: 0, title: `Realized $${(rp/1000).toFixed(1)}K` }));
  }

  // Cycle-top band (MVRV 3.5× realized) — the ceiling mirror of the realized
  // floor. Only drawn when within 40% above price so it can't stretch the axis.
  const topSig  = btcMining?.top_signals;
  const lastPx  = unique.length ? unique[unique.length - 1].close : 0;
  if (symbol === 'BTC' && topSig?.top_band && lastPx && topSig.top_band / lastPx < 1.4) {
    S.overlayPriceLines.push(S.candleSeries.createPriceLine({
      price: topSig.top_band, color: '#f87171', lineWidth: 2, lineStyle: 0,
      title: `Top band $${(topSig.top_band/1000).toFixed(1)}K`,
    }));
  }

  // ── Diagonal trendlines — LOCAL (near price) + MACRO (multi-week) ─────────
  // Median bar interval (seconds) — used to project the lines forward.
  const _barSec = unique.length > 1
    ? (unique[unique.length - 1].time - unique[0].time) / (unique.length - 1)
    : 3600;
  const PROJ_BARS = 8;   // TradingView-style ray: extend ~8 bars past the live candle

  const _drawTrend = (series, line, isMacro) => {
    if (!line?.type || !line.anchor || !line.end) { series.setData([]); return; }
    const isRes = line.type === 'resistance';
    const broke = line.broken;                    // 'up' | 'down' | null
    let col, style, width;
    if (isMacro) {
      // Macro = neutral grey dashed context line (one legend colour).
      col = '#cbd5e1'; style = 2; width = 2;
    } else {
      // Local = solid, thick. Red = resistance / green = support; on a break the
      // line flips to the breakout-direction colour.
      col = isRes ? (broke === 'up' ? '#22c55e' : '#ef4444')
                  : (broke === 'down' ? '#ef4444' : '#22c55e');
      style = 0; width = 3;
    }
    series.applyOptions({ color: col, lineWidth: width, lineStyle: style });
    const t0 = Math.floor(line.anchor.timestamp / 1000);
    const t1 = Math.floor(line.end.timestamp    / 1000);
    const pts = [
      { time: t0, value: line.anchor.value },
      { time: t1, value: line.end.value },
    ];
    // Project forward past the live candle BAR BY BAR. The time scale spaces
    // points by bar slots, not by elapsed time — a single point 8 bars ahead
    // gets only ONE slot, compressing 8 bars of slope into it and making the
    // ray kink steeper at the end. One point per future bar keeps each slot
    // carrying exactly one bar of slope, so the line continues dead straight.
    if (t1 > t0) {
      const step = Math.max(1, Math.round(_barSec));
      const nBars = Math.max(1, Math.round((t1 - t0) / step));
      const slopePerBar = (line.end.value - line.anchor.value) / nBars;
      for (let k = 1; k <= PROJ_BARS; k++) {
        pts.push({ time: t1 + k * step, value: line.end.value + slopePerBar * k });
      }
    }
    series.setData(pts);
  };
  _drawTrend(S.trendlineSeries,      trendline?.local, false);
  _drawTrend(S.trendlineMacroSeries, trendline?.macro, true);

  // ── EMA 50 / EMA 200 lines ────────────────────────────────────────────────
  const _emaData = (arr) => (arr || [])
    .map(p => ({ time: Math.floor(p.timestamp / 1000), value: p.value }))
    .filter((d, i, a) => i === 0 || d.time !== a[i - 1].time);
  S.ema50Series.setData(_emaData(emaLines?.ema50));
  S.ema200Series.setData(_emaData(emaLines?.ema200));

  // ── Supply / demand zones — draw each band as top/mid/bottom price lines ──
  // (mirrors the FVG style; lightweight-charts v4 has no native filled boxes).
  const drawZone = (z, isRes) => {
    if (!z) return;
    const strong = z.status === 'inside' || z.status === 'approaching';
    const col    = isRes ? 'rgba(239,68,68,0.75)'  : 'rgba(16,185,129,0.75)';
    const dim    = isRes ? 'rgba(239,68,68,0.28)'  : 'rgba(16,185,129,0.28)';
    const label  = isRes ? 'Resistance Zone' : 'Support Zone';
    const tag    = z.status === 'inside' ? ' • IN ZONE' : z.status === 'approaching' ? ' • near' : '';
    S.overlayPriceLines.push(S.candleSeries.createPriceLine({ price: z.top,    color: dim, lineWidth: strong ? 2 : 1, lineStyle: 2, title: '', axisLabelVisible: false }));
    S.overlayPriceLines.push(S.candleSeries.createPriceLine({ price: z.mid,    color: col, lineWidth: strong ? 3 : 2, lineStyle: 3, title: `${label}${tag}` }));
    S.overlayPriceLines.push(S.candleSeries.createPriceLine({ price: z.bottom, color: dim, lineWidth: strong ? 2 : 1, lineStyle: 2, title: '', axisLabelVisible: false }));
  };
  if (srZones?.resistance) drawZone(srZones.resistance, true);
  if (srZones?.support)    drawZone(srZones.support, false);

  S.mainChart.timeScale().fitContent();
}

/* ─── RSI sub-chart ───────────────────────────────────────────────────────── */
function renderRSIChart(rsiSeries) {
  if (!rsiSeries?.length || !S.rsiSeries) return;
  const data = rsiSeries
    .filter(d => d.rsi != null)
    .map(d => ({ time: Math.floor(d.timestamp / 1000), value: d.rsi }));
  if (data.length) {
    const unique = [...new Map(data.map(d => [d.time, d])).values()].sort((a, b) => a.time - b.time);
    S.rsiSeries.setData(unique);
    S.rsiChart.timeScale().fitContent();
  }
}

/* ─── CVD charts ──────────────────────────────────────────────────────────── */
function renderCVDCharts(spot, fut, futuresAvailable) {
  renderCVDPanel('spot', spot, S.spotCvdSeries, 'spotCvdVal', 'spotCvdTrend', true);
  if (futuresAvailable === false && !fut) {
    // Token has no perpetual market — show clear N/A instead of a copy of spot CVD
    document.getElementById('futCvdVal').textContent   = 'N/A';
    document.getElementById('futCvdVal').style.color   = 'var(--muted)';
    document.getElementById('futCvdTrend').textContent = 'No perp market';
    document.getElementById('futCvdTrend').className   = 'cvd-trend neutral';
    S.futCvdSeries.setData([]);
  } else {
    renderCVDPanel('fut', fut, S.futCvdSeries, 'futCvdVal', 'futCvdTrend', false);
  }
}

function renderCVDDivergence(div) {
  const el = document.getElementById('cvdDivBanner');
  if (!el) return;
  if (!div || !div.type || div.type === 'neutral') { el.style.display = 'none'; return; }

  const icons = {
    futures_led_up:        '⚠',
    spot_led_up:           '✓',
    confirmed_up:          '✓✓',
    futures_led_down:      '⚠',
    spot_led_down:         '↓',
    confirmed_down:        '↓↓',
    futures_dominated_down:'⚠⚠',
    futures_heavy_down:    '⚠↓',
    futures_dominated_up:  '⚠⚠',
    spot_dominated_up:     '✓✓✓',
    spot_heavy_up:         '✓✓',
    spot_dominated_down:   '↓↓↓',
    spot_heavy_down:       '↓↓',
    spot_absorption_bullish: '✓⏳',
    spot_absorption_bearish: '↓⏳',
  };
  const sigCls = div.signal === 'bullish' ? 'bull' : div.signal === 'bearish' ? 'bear' : '';
  // Build magnitude badge — use spot_ratio when spot dominates, futures_ratio otherwise
  let ratioBadge = '';
  if (div.dominance && div.dominance !== 'balanced') {
    let ratioVal, domLabel;
    if (div.dominance === 'spot' && div.spot_ratio != null) {
      const r = div.spot_ratio;
      ratioVal = r >= 10 ? `${Math.round(r)}×` : `${r.toFixed(1)}×`;
      domLabel = `Spot ${ratioVal} futures`;
    } else if (div.dominance === 'futures' && div.futures_ratio != null) {
      const r = div.futures_ratio;
      ratioVal = r >= 10 ? `${Math.round(r)}×` : `${r.toFixed(1)}×`;
      domLabel = `Futures ${ratioVal} spot`;
    }
    if (domLabel) {
      ratioBadge = ` <span class="cvd-ratio-badge cvd-dom-${div.dominance}">${domLabel}</span>`;
    }
  }
  el.style.display = '';
  el.className = `cvd-div-banner cvd-div-${div.signal}`;
  el.innerHTML = `
    <span class="cvd-div-icon">${icons[div.type] || '·'}</span>
    <span class="cvd-div-label ${sigCls}">${div.label}</span>${ratioBadge}
    <span class="cvd-div-detail">${div.detail}</span>`;
}

function renderMACDCard(m) {
  if (!m) return;
  const trendEl = document.getElementById('macdTrend');
  const crossEl = document.getElementById('macdCross');
  const barsEl  = document.getElementById('macdHistBars');
  if (!trendEl) return;
  const trend = m.trend || 'neutral';
  trendEl.textContent = trend.charAt(0).toUpperCase() + trend.slice(1);
  trendEl.style.color = trend === 'bullish' ? 'var(--bull)' : trend === 'bearish' ? 'var(--bear)' : 'var(--neutral)';

  let crossText = '—', crossColor = 'var(--muted2)';
  if (m.cross === 'bullish' || m.zero_cross === 'bullish') {
    crossText  = '▲ Bullish cross — momentum turning up';
    crossColor = 'var(--bull)';
  } else if (m.cross === 'bearish' || m.zero_cross === 'bearish') {
    crossText  = '▼ Bearish cross — momentum turning down';
    crossColor = 'var(--bear)';
  } else if (m.histogram != null) {
    const sign = m.histogram > 0 ? '+' : '';
    crossText = `Histogram ${sign}${Number(m.histogram).toFixed(5)}`;
    crossColor = m.histogram > 0 ? 'var(--bull)' : 'var(--bear)';
  }
  crossEl.textContent  = crossText;
  crossEl.style.color  = crossColor;

  if (barsEl && m.histogram != null) {
    const h    = m.histogram;
    const barH = Math.min(Math.abs(h) / (Math.abs(h) + 1e-9) * 28 + 4, 32);
    barsEl.innerHTML = `<div class="macd-hist-bar ${h >= 0 ? 'bull' : 'bear'}" style="height:${barH}px"></div>`;
  }
}

function renderEMACard(ema) {
  if (!ema) return;
  const trendEl = document.getElementById('emaTrendVal');
  const rowsEl  = document.getElementById('emaRows');
  if (!trendEl) return;
  const trend  = ema.trend || 'neutral';
  const labels = { bullish: 'Uptrend', bearish: 'Downtrend', mixed_bullish: 'Mixed ↑', mixed_bearish: 'Mixed ↓', neutral: 'Neutral' };
  trendEl.textContent = labels[trend] || trend;
  trendEl.style.color = trend.includes('bull') ? 'var(--bull)' : trend.includes('bear') ? 'var(--bear)' : 'var(--neutral)';

  const above = ema.above || [];
  const fmt   = n => n != null ? `$${Number(n).toLocaleString('en-US', { maximumFractionDigits: 6 })}` : 'N/A';
  const rows  = [{ p: 20, v: ema.ema20 }, { p: 50, v: ema.ema50 }, { p: 200, v: ema.ema200 }].filter(r => r.v != null);
  rowsEl.innerHTML = rows.map(r => {
    const up  = above.includes(r.p);
    const cls = up ? 'bull' : 'bear';
    return `<div class="ema-row"><span class="ema-label">EMA${r.p}</span><span class="${cls}">${up ? '▲' : '▼'} ${fmt(r.v)}</span></div>`;
  }).join('');
}

function renderSupertrendCard(st) {
  const dirEl = document.getElementById('stDirection');
  const sigEl = document.getElementById('stSignal');
  const valEl = document.getElementById('stValueRow');
  if (!dirEl) return;
  if (!st || st.direction == null) {
    dirEl.textContent = '—'; sigEl.textContent = '—'; valEl.innerHTML = '';
    return;
  }
  const bull = st.direction === 'bullish';
  dirEl.textContent = bull ? '▲ Bullish' : '▼ Bearish';
  dirEl.style.color = bull ? 'var(--bull)' : 'var(--bear)';

  if (st.flipped && st.signal) {
    sigEl.textContent = `🔔 New ${st.signal} signal`;
    sigEl.style.color = bull ? 'var(--bull)' : 'var(--bear)';
  } else {
    sigEl.textContent = 'No flip on last candle';
    sigEl.style.color = 'var(--muted)';
  }

  const fmt = v => v != null ? `$${Number(v).toLocaleString('en-US', { maximumFractionDigits: 4 })}` : '—';
  valEl.innerHTML = `<span class="st-label">${bull ? 'Support' : 'Resistance'}</span>
    <span class="${bull ? 'bull' : 'bear'}">${fmt(st.value)}</span>`;
}

function renderIchimokuCard(ichi) {
  const cloudEl = document.getElementById('ichiCloud');
  const posEl   = document.getElementById('ichiPricePos');
  const rowsEl  = document.getElementById('ichiRows');
  if (!cloudEl) return;
  if (!ichi || ichi.cloud_color == null) {
    cloudEl.textContent = '—'; posEl.textContent = '—'; rowsEl.innerHTML = '';
    return;
  }

  const green = ichi.cloud_color === 'green';
  cloudEl.textContent = green ? '☁ Bullish Cloud' : '☁ Bearish Cloud';
  cloudEl.style.color = green ? 'var(--bull)' : 'var(--bear)';

  const posLabels = { above: '▲ Price above cloud', inside: '◆ Price inside cloud', below: '▼ Price below cloud' };
  const posColors = { above: 'var(--bull)', inside: 'var(--neutral)', below: 'var(--bear)' };
  posEl.textContent = posLabels[ichi.price_vs_cloud] || '—';
  posEl.style.color = posColors[ichi.price_vs_cloud] || 'var(--muted)';

  const fmt = v => v != null ? `$${Number(v).toLocaleString('en-US', { maximumFractionDigits: 4 })}` : '—';
  const tkColor = ichi.tk_cross === 'bullish' ? 'var(--bull)' : ichi.tk_cross === 'bearish' ? 'var(--bear)' : 'var(--muted)';
  const tkLabel = ichi.tk_cross === 'bullish' ? '🔼 TK Bullish Cross' : ichi.tk_cross === 'bearish' ? '🔽 TK Bearish Cross' : 'No TK cross';

  rowsEl.innerHTML = `
    <div class="ichi-row"><span class="ichi-label">Tenkan</span><span>${fmt(ichi.tenkan)}</span></div>
    <div class="ichi-row"><span class="ichi-label">Kijun</span><span>${fmt(ichi.kijun)}</span></div>
    <div class="ichi-row"><span class="ichi-label">Span A</span><span>${fmt(ichi.span_a)}</span></div>
    <div class="ichi-row"><span class="ichi-label">Span B</span><span>${fmt(ichi.span_b)}</span></div>
    <div class="ichi-row ichi-tk"><span class="ichi-label">TK Cross</span><span style="color:${tkColor}">${tkLabel}</span></div>`;
}

function renderBollingerCard(bb) {
  const statusEl  = document.getElementById('bbStatus');
  const squeezeEl = document.getElementById('bbSqueeze');
  const rowsEl    = document.getElementById('bbRows');
  if (!statusEl) return;
  if (!bb || bb.upper == null) {
    statusEl.textContent = '—'; squeezeEl.textContent = '—'; rowsEl.innerHTML = '';
    return;
  }
  const fmt = v => v != null ? `$${Number(v).toLocaleString('en-US', { maximumFractionDigits: 6 })}` : '—';

  const bo = bb.breakout;
  if (bb.squeeze && bo === 'bullish') {
    statusEl.textContent = '💥 Squeeze Breakout ↑';
    statusEl.style.color = 'var(--bull)';
  } else if (bb.squeeze && bo === 'bearish') {
    statusEl.textContent = '💥 Squeeze Breakdown ↓';
    statusEl.style.color = 'var(--bear)';
  } else if (bb.squeeze) {
    statusEl.textContent = '🔄 Squeeze Active';
    statusEl.style.color = 'var(--neutral)';
  } else if (bo === 'bullish') {
    statusEl.textContent = '▲ Above Upper Band';
    statusEl.style.color = 'var(--bull)';
  } else if (bo === 'bearish') {
    statusEl.textContent = '▼ Below Lower Band';
    statusEl.style.color = 'var(--bear)';
  } else {
    statusEl.textContent = 'Inside Bands';
    statusEl.style.color = 'var(--muted)';
  }

  const pctB = bb.pct_b != null ? (bb.pct_b * 100).toFixed(1) : '—';
  squeezeEl.textContent = `%B: ${pctB}% · BW: ${bb.bandwidth != null ? (bb.bandwidth * 100).toFixed(2) : '—'}%`;
  squeezeEl.style.color = 'var(--muted2)';

  rowsEl.innerHTML = `
    <div class="bb-row"><span class="bb-label">Upper</span><span class="bull">${fmt(bb.upper)}</span></div>
    <div class="bb-row"><span class="bb-label">Middle</span><span>${fmt(bb.middle)}</span></div>
    <div class="bb-row"><span class="bb-label">Lower</span><span class="bear">${fmt(bb.lower)}</span></div>`;
}

function renderRsiDivCard(div) {
  const typeEl = document.getElementById('rsiDivType');
  const descEl = document.getElementById('rsiDivDesc');
  if (!typeEl) return;
  if (!div || !div.type) {
    typeEl.textContent = 'No divergence';
    typeEl.style.color = 'var(--muted)';
    descEl.textContent = 'Price and RSI moving in sync';
    descEl.style.color = 'var(--muted2)';
    return;
  }
  // Four types: regular bullish/bearish (reversal) + hidden bullish/bearish
  // (trend continuation). Colour by direction; label distinguishes reversal vs
  // continuation so a hidden bearish (downtrend continuation) reads clearly.
  const LABELS = {
    bullish:        '🔼 Bullish Divergence',
    bearish:        '🔽 Bearish Divergence',
    hidden_bullish: '🔼 Hidden Bullish · continuation',
    hidden_bearish: '🔽 Hidden Bearish · continuation',
  };
  const isBull = div.type === 'bullish' || div.type === 'hidden_bullish';
  let label = LABELS[div.type] || (isBull ? '🔼 Bullish Divergence' : '🔽 Bearish Divergence');
  if (div.forming) label = label.replace('Divergence', 'Div. · forming ⏳');
  typeEl.textContent = label;
  typeEl.style.color = isBull ? 'var(--bull)' : 'var(--bear)';
  typeEl.style.opacity = div.forming ? '0.85' : '';
  descEl.textContent = div.description || '';
  descEl.style.color = 'var(--muted2)';
}

function renderVwapCard(vwap) {
  const posEl   = document.getElementById('vwapPos');
  const slopeEl = document.getElementById('vwapSlope');
  const rowsEl  = document.getElementById('vwapRows');
  if (!posEl) return;
  if (!vwap || vwap.vwap == null) {
    posEl.textContent = '—'; slopeEl.textContent = '—'; rowsEl.innerHTML = '';
    return;
  }
  const above = vwap.price_vs_vwap === 'above';
  const cross = vwap.vwap_cross;
  if (cross === 'bullish') {
    posEl.textContent = '🔀 Bullish Cross';
    posEl.style.color = 'var(--bull)';
  } else if (cross === 'bearish') {
    posEl.textContent = '🔀 Bearish Cross';
    posEl.style.color = 'var(--bear)';
  } else {
    posEl.textContent = above ? '▲ Above VWAP' : '▼ Below VWAP';
    posEl.style.color = above ? 'var(--bull)' : 'var(--bear)';
  }
  const slopeIcon = vwap.slope === 'rising' ? '↗' : vwap.slope === 'falling' ? '↘' : '→';
  slopeEl.textContent = `Slope: ${slopeIcon} ${vwap.slope || '—'}`;
  slopeEl.style.color = 'var(--muted2)';
  const fmt = v => v != null ? `$${Number(v).toLocaleString('en-US', { maximumFractionDigits: 6 })}` : '—';
  rowsEl.innerHTML = `<div class="vwap-row"><span class="vwap-label">VWAP</span><span>${fmt(vwap.vwap)}</span></div>`;
}

function renderStochRsiCard(srsi) {
  const sigEl  = document.getElementById('srsiSignal');
  const zoneEl = document.getElementById('srsiZone');
  const rowsEl = document.getElementById('srsiRows');
  if (!sigEl) return;
  if (!srsi || srsi.k == null) {
    sigEl.textContent = '—'; zoneEl.textContent = '—'; rowsEl.innerHTML = '';
    return;
  }
  const SIG_LABELS = {
    bull_cross_oversold:  ['🔼 Bull Cross (Oversold)', 'var(--bull)'],
    oversold:             ['⬇ Oversold',               'var(--bull)'],
    near_oversold:        ['↓ Near Oversold',           'var(--bull)'],
    bear_cross_overbought:['🔽 Bear Cross (Overbought)','var(--bear)'],
    overbought:           ['⬆ Overbought',              'var(--bear)'],
    near_overbought:      ['↑ Near Overbought',         'var(--bear)'],
    neutral:              ['◆ Neutral',                 'var(--muted)'],
  };
  const [label, color] = SIG_LABELS[srsi.signal] || ['—', 'var(--muted)'];
  sigEl.textContent = label;
  sigEl.style.color = color;
  const zone = srsi.zone || 'neutral';
  zoneEl.textContent = `Zone: ${zone.charAt(0).toUpperCase() + zone.slice(1)}`;
  zoneEl.style.color = zone === 'oversold' ? 'var(--bull)' : zone === 'overbought' ? 'var(--bear)' : 'var(--muted2)';
  rowsEl.innerHTML = `
    <div class="srsi-row"><span class="srsi-label">K</span><span>${srsi.k ?? '—'}</span></div>
    <div class="srsi-row"><span class="srsi-label">D</span><span>${srsi.d ?? '—'}</span></div>`;
}

function renderVolSignalCard(vol) {
  const dirEl  = document.getElementById('volSigDir');
  const descEl = document.getElementById('volSigDesc');
  if (!dirEl) return;
  if (!vol || !vol.signal) {
    dirEl.textContent  = 'No signal';
    dirEl.style.color  = 'var(--muted)';
    descEl.textContent = 'Volume within normal range';
    descEl.style.color = 'var(--muted2)';
    return;
  }
  const bull = vol.signal === 'bullish';
  dirEl.textContent = bull ? `▲ Bullish ${vol.ratio}×` : `▼ Bearish ${vol.ratio}×`;
  dirEl.style.color = bull ? 'var(--bull)' : 'var(--bear)';
  descEl.textContent = vol.description || '';
  descEl.style.color = 'var(--muted2)';
}

/* On-chain state-transition strips — "bullish today; last bearish N ago". */
function _fmtDays(d) {
  if (d == null) return '—';
  if (d >= 365) return (d / 365).toFixed(1) + 'y';
  if (d >= 30)  return Math.round(d / 30) + 'mo';
  return Math.round(d) + 'd';
}
function _prettyState(s) { return String(s || '').replace(/_/g, ' '); }
function histStrip(h, pretty) {
  if (!h || !h.current_state) return '';
  pretty = pretty || _prettyState;
  let s = `${pretty(h.current_state)} · ${_fmtDays(h.days_in_state)} in state`;
  if (h.previous) {
    const ago = h.last_seen ? h.last_seen[h.previous.state] : null;
    s += ` · last <b>${pretty(h.previous.state)}</b>` +
         (ago != null ? ` ${_fmtDays(ago)} ago` : '') +
         ` (held ${_fmtDays(h.previous.days)})`;
  }
  const flips = h.flips ? h.flips.length : 0;
  return `<div class="btcm-hist">🕑 ${s}${flips ? ` · ${flips} flips` : ''}</div>`;
}
function diffHistStrip(dh) {
  if (!dh || !dh.streak || !dh.streak.current_state) return '';
  const st = dh.streak;
  const recent = (dh.adjustments || []).slice(-5).map(a =>
    `<span class="${a.change_pct >= 0 ? 'bull' : 'bear'}">${a.change_pct >= 0 ? '+' : ''}${a.change_pct}%</span>`
  ).join(' ');
  return `<div class="btcm-hist">🕑 ${st.current_state} ${_fmtDays(st.days_in_state)}` +
         `${recent ? ` · recent: ${recent}` : ''}</div>`;
}

function renderBtcMiningCard(mining, symbol) {
  const card = document.getElementById('btcMiningCard');
  const rows = document.getElementById('btcMiningRows');
  if (!card || !rows) return;

  if (symbol !== 'BTC' || !mining) {
    card.style.display = 'none';
    return;
  }
  card.style.display = '';

  const ribbon = mining.hash_ribbon || 'neutral';
  const ribbonMeta = {
    buy:          { cls: 'bull', icon: '▲', label: 'Buy Signal',     desc: '30d MA crossed above 60d — miner recovery confirmed' },
    bull:         { cls: 'bull', icon: '▲', label: 'Bullish',        desc: '30d MA above 60d — miners recovering' },
    bear:         { cls: 'bear', icon: '▼', label: 'Bearish',        desc: '30d MA below 60d — miner sell pressure' },
    capitulation: { cls: 'bear', icon: '▼', label: 'Capitulation',   desc: '30d MA crossed below 60d — miner stress peak' },
    neutral:      { cls: '',     icon: '—', label: 'Neutral',        desc: 'Insufficient data' },
  };
  const rm = ribbonMeta[ribbon] || ribbonMeta.neutral;

  const phaseMeta = {
    early: { cls: 'bull', label: 'Early (0–6 mo)',   desc: 'Post-halving consolidation / accumulation' },
    mid:   { cls: 'bull', label: 'Mid (6–18 mo)',    desc: 'Historical bull run window — strongest phase' },
    late:  { cls: 'bear', label: 'Late (18–36 mo)',  desc: 'Late cycle — watch for distribution' },
    pre:   { cls: '',     label: 'Pre-halving',      desc: 'Accumulation ahead of next halving' },
  };
  const phase  = mining.halving_phase || 'pre';
  const pm     = phaseMeta[phase] || phaseMeta.pre;
  const months = mining.halving_months_since != null ? `${mining.halving_months_since} mo` : '—';
  const daysUntil = mining.halving_days_until != null ? `${mining.halving_days_until.toLocaleString()} days` : '—';

  const prof = mining.profitability_ratio;         // vs efficient break-even
  const profAvg = mining.profitability_ratio_avg;  // vs blended-fleet break-even
  let profCls = '', profLabel = '—';
  if (prof != null) {
    if (prof >= 2.0)       { profCls = 'bull'; profLabel = `${prof}× (Very profitable)`; }
    else if (prof >= 1.3)  { profCls = 'bull'; profLabel = `${prof}× (Profitable)`; }
    else if (prof < 1.05)  { profCls = 'bear'; profLabel = `${prof}× (Near break-even!)`; }
    else                   { profCls = '';      profLabel = `${prof}×`; }
    // Show the blended-fleet ratio too — a sub-1× avg fleet explains miner
    // sell pressure even while efficient rigs stay above water.
    if (profAvg != null) {
      profLabel += profAvg < 1.0
        ? ` · avg fleet ${profAvg}× (underwater)`
        : ` · avg fleet ${profAvg}×`;
    }
  }

  const diff        = mining.difficulty_change;
  const diffLast    = mining.difficulty_last_change;
  const diffBlocks  = mining.difficulty_remaining_blocks;
  const diffTimeSec = mining.difficulty_remaining_time;
  const diffPct     = mining.difficulty_progress_pct;

  const fmtDiff = v => v != null ? (v >= 0 ? `+${v.toFixed(1)}%` : `${v.toFixed(1)}%`) : '—';
  const diffCls     = diff == null     ? '' : diff     >= 3 ? 'bear' : diff     <= -3 ? 'bull' : '';
  const diffLastCls = diffLast == null ? '' : diffLast >= 3 ? 'bear' : diffLast <= -3 ? 'bull' : '';
  const diffStr     = fmtDiff(diff);
  const diffLastStr = fmtDiff(diffLast);

  let diffTimeStr = '';
  if (diffTimeSec != null) {
    const totalSec = diffTimeSec / 1000;  // API returns milliseconds
    const hrs  = Math.floor(totalSec / 3600);
    const days = Math.floor(hrs / 24);
    diffTimeStr = days > 0 ? `~${days}d ${hrs % 24}h` : `~${hrs}h`;
  }
  const diffProgressStr = diffPct != null ? `${diffPct.toFixed(0)}% through epoch` : '';
  const diffBlocksStr   = diffBlocks != null ? `${diffBlocks.toLocaleString()} blocks remaining` : '';

  const be = mining.break_even_usd;
  const beAvg = mining.break_even_average_usd;
  // Break-even is efficiency-sensitive → show a range: efficient rigs (floor)
  // through the blended fleet. Falls back to the single value if avg is absent.
  const beStr = be != null
    ? (beAvg != null
        ? `$${be.toLocaleString()} (efficient) → $${beAvg.toLocaleString()} (avg fleet)`
        : `$${be.toLocaleString()}`)
    : '—';

  const rev = mining.miner_revenue_usd;
  const revStr = rev != null ? `$${(rev / 1e6).toFixed(1)}M / day` : '—';

  // Reward per TH/day
  const fmtSats = v => v != null ? `${(v * 1e8).toFixed(2)} sats` : '—';
  const fmtUsd  = v => v != null ? `$${v.toFixed(4)}` : '—';
  const rwBtc   = mining.reward_per_th_btc;
  const rwUsd   = mining.reward_per_th_usd;
  const rwAfter = mining.reward_per_th_after_adj;
  let rewardRow = '';
  if (rwBtc != null) {
    const nowStr  = `${fmtSats(rwBtc)} (${fmtUsd(rwUsd)})`;
    let afterStr  = '';
    if (rwAfter != null && diff != null) {
      const pctChange = ((rwAfter - rwBtc) / rwBtc * 100);
      const sign      = pctChange >= 0 ? '+' : '';
      const adjCls    = pctChange >= 1 ? 'bull' : pctChange <= -1 ? 'bear' : '';
      afterStr = `<span class="btcm-val ${adjCls}" style="font-size:0.78rem">${sign}${pctChange.toFixed(1)}% after adj</span>`;
    }
    rewardRow = `
    <div class="btcm-row"><span class="btcm-label">Reward / TH / Day</span><span class="btcm-val">${nowStr}</span></div>
    <div class="btcm-sub">Gross BTC reward at current network hashrate ${afterStr}</div>`;
  }

  // MVRV Score (90d SMA)
  const mvrv    = mining.mvrv;
  const btcPriceUsd = mining.btc_price_usd || null;
  const fmtK = v => v >= 1000 ? '$' + (v / 1000).toFixed(1) + 'K' : '$' + Number(v).toFixed(0);
  const mvrvRow = mvrv ? (() => {
    const score = mvrv.score != null ? mvrv.score.toFixed(2) : '—';
    const sma   = mvrv.sma90 != null ? mvrv.sma90.toFixed(2) : '—';
    const cls   = mvrv.cls  || '';
    const lbl   = mvrv.label || '—';
    const desc  = mvrv.desc  || '';
    const priceStr    = btcPriceUsd ? `BTC ${fmtK(btcPriceUsd)}` : '';
    const realizedStr = mvrv.realized_price ? `· Realized ~${fmtK(mvrv.realized_price)}` : '';
    const priceLine   = (priceStr || realizedStr)
      ? `<div class="btcm-sub btcm-price-ctx">${priceStr} ${realizedStr}</div>`
      : '';
    return `
    <div class="btcm-row"><span class="btcm-label">MVRV Score</span><span class="btcm-val ${cls}">${score} <small>(90d SMA: ${sma})</small></span></div>
    <div class="btcm-sub">${lbl} — ${desc}</div>
    ${priceLine}`;
  })() : '';

  // SOPR row
  const soprData = mining.sopr;
  const soprRow = soprData ? (() => {
    const cls = soprData.cls || '';
    return `
    <div class="btcm-row"><span class="btcm-label">SOPR</span><span class="btcm-val ${cls}">${soprData.value?.toFixed(4) || '—'} <small>(7d avg: ${soprData.sma7?.toFixed(4) || '—'})</small></span></div>
    <div class="btcm-sub">${soprData.label} — &lt;1 = selling at loss (buy signal) · &gt;1 = taking profits · &gt;1.1 = euphoric top</div>`;
  })() : '';

  // Puell Multiple row
  const puellData = mining.puell_multiple;
  const puellRow = puellData ? (() => {
    const cls = puellData.cls || '';
    const rev = puellData.daily_rev_usd ? ` · daily rev $${(puellData.daily_rev_usd/1e6).toFixed(1)}M` : '';
    return `
    <div class="btcm-row"><span class="btcm-label">Puell Multiple</span><span class="btcm-val ${cls}">${puellData.value?.toFixed(2) || '—'}</span></div>
    <div class="btcm-sub">${puellData.label}${rev} — &lt;0.5 miner capitulation (buy) · &gt;2.5 peak revenue (sell)</div>`;
  })() : '';

  // Realized Price row
  const realizedPrice = mining.realized_price;
  const ptr = mining.price_to_realized;
  const realizedRow = realizedPrice ? (() => {
    const cls = ptr < 1.0 ? 'bull' : ptr < 1.3 ? 'bull' : ptr > 3.5 ? 'bear' : '';
    const label = ptr < 1.0 ? 'Below Realized — deep value' : ptr < 1.3 ? 'Near Realized — strong support' : ptr > 3.5 ? 'Far above Realized — stretched' : 'Above Realized — normal bull';
    return `
    <div class="btcm-row"><span class="btcm-label">Realized Price</span><span class="btcm-val ${cls}">${fmtK(realizedPrice)} <small>(${ptr?.toFixed(2) || '—'}×)</small></span></div>
    <div class="btcm-sub">${label} · avg cost basis of all BTC ever moved</div>`;
  })() : '';

  // On-chain composite score
  const oc    = mining.onchain_score || {};
  const ocScore = oc.score != null ? oc.score : null;
  const ocCls   = oc.cls   || '';
  const ocLabel = oc.label || '—';
  const ocBar   = ocScore != null
    ? `<div class="btcm-oc-bar"><div class="btcm-oc-fill ${ocCls}" style="width:${ocScore}%"></div></div>`
    : '';
  const ocRow = ocScore != null ? `
    <div class="btcm-oc-header">
      <span class="btcm-oc-title">On-Chain Score</span>
      <span class="btcm-oc-score ${ocCls}">${ocScore}<span style="opacity:.5;font-size:.8em">/100</span></span>
    </div>
    ${ocBar}
    <div class="btcm-sub" style="margin-bottom:10px">${ocLabel} — Hash Ribbon + Halving + Profitability + MVRV + SOPR + Puell + Difficulty</div>
    <hr class="btcm-divider">` : '';

  rows.innerHTML = `
    ${ocRow}
    <div class="btcm-row"><span class="btcm-label">Hash Ribbon</span><span class="btcm-val ${rm.cls}">${rm.icon} ${rm.label}</span></div>
    <div class="btcm-sub">${rm.desc}</div>
    ${histStrip(mining.hash_ribbon_history)}
    <div class="btcm-row"><span class="btcm-label">Halving Phase</span><span class="btcm-val ${pm.cls}">${pm.label}</span></div>
    <div class="btcm-sub">${months} since halving · ${daysUntil} until next · ${pm.desc}</div>
    <div class="btcm-row"><span class="btcm-label">Miner Profitability</span><span class="btcm-val ${profCls}">${profLabel}</span></div>
    <div class="btcm-sub">Break-even est. ${beStr} · Revenue ${revStr}</div>
    ${rewardRow}
    <div class="btcm-row"><span class="btcm-label">Last Difficulty Adj</span><span class="btcm-val ${diffLastCls}">${diffLastStr}</span></div>
    <div class="btcm-sub">Completed at last epoch — directly set current reward per TH</div>
    <div class="btcm-row"><span class="btcm-label">Next Difficulty Adj</span><span class="btcm-val ${diffCls}">${diffStr} <small style="opacity:.6">${diffTimeStr}</small></span></div>
    <div class="btcm-sub">${diffProgressStr} · ${diffBlocksStr} · rising = more competition · falling = fewer miners</div>
    ${diffHistStrip(mining.difficulty_history)}
    ${mvrvRow}
    ${mvrv && mvrv.history ? histStrip(mvrv.history) : ''}
    ${realizedRow}
    ${soprRow}
    ${soprData && soprData.history ? histStrip(soprData.history) : ''}
    ${puellRow}
    ${puellData && puellData.history ? histStrip(puellData.history) : ''}
  `;
}

/* ─── On-Chain Metrics Grid (BTC only) ────────────────────────────────────── */
function renderOnchainMetrics(mining, symbol, lth) {
  const section = document.getElementById('onchainMetricsSection');
  const grid    = document.getElementById('ocmGrid');
  if (!section || !grid) return;
  if (symbol !== 'BTC' || !mining) { section.style.display = 'none'; return; }
  section.style.display = '';

  const fmtK = v => v >= 1_000_000 ? '$' + (v/1_000_000).toFixed(2)+'M'
                  : v >= 1_000     ? '$' + (v/1_000).toFixed(1)+'K'
                  : '$' + Number(v).toFixed(2);

  const tiles = [];

  // ── MVRV ──────────────────────────────────────────────────────────────────
  const mvrv = mining.mvrv;
  if (mvrv) {
    const score = mvrv.score?.toFixed(2) ?? '—';
    const sma   = mvrv.sma90?.toFixed(2)  ?? '—';
    const rp    = mvrv.realized_price ? fmtK(mvrv.realized_price) : null;
    tiles.push(`
      <div class="ocm-tile ${mvrv.cls || ''}">
        <div class="ocm-name">MVRV Ratio</div>
        <div class="ocm-value">${score}</div>
        <div class="ocm-zone ${mvrv.cls || ''}">${(mvrv.zone||'').replace(/_/g,' ')}</div>
        <div class="ocm-desc">${mvrv.label || ''}</div>
        <div class="ocm-sub">90d avg ${sma}${rp ? ' · Realized '+rp : ''}</div>
      </div>`);
  }

  // ── SOPR ──────────────────────────────────────────────────────────────────
  const sopr = mining.sopr;
  if (sopr) {
    const SOPR_DESC = {
      capitulation: 'Panic selling at loss — strongest buy signal',
      loss:         'Selling below cost basis — accumulation zone',
      neutral:      'Holders at breakeven — no strong signal',
      profit:       'Taking profits — watch for distribution',
      euphoria:     'Euphoric profit taking — cycle top signal',
    };
    const soprName = sopr.metric_name || 'SOPR';
    const soprSub  = soprName === 'NUPL'
      ? `Net Unrealized P/L · <0 = all underwater · >0.75 = euphoria`
      : `7d avg ${sopr.sma7?.toFixed(4) ?? '—'} · ${SOPR_DESC[sopr.zone] || ''}`;
    tiles.push(`
      <div class="ocm-tile ${sopr.cls || ''}">
        <div class="ocm-name">${soprName}</div>
        <div class="ocm-value">${sopr.value?.toFixed(soprName === 'NUPL' ? 3 : 4) ?? '—'}</div>
        <div class="ocm-zone ${sopr.cls || ''}">${(sopr.zone||'').replace(/_/g,' ')}</div>
        <div class="ocm-desc">${sopr.label || ''}</div>
        <div class="ocm-sub">${soprSub}</div>
      </div>`);
  }

  // ── Puell Multiple ────────────────────────────────────────────────────────
  const puell = mining.puell_multiple;
  // Guard: a Puell < 0.15 or missing revenue is broken data, not capitulation —
  // never render a "STRONG BUY" from it (belt-and-suspenders with the backend).
  if (puell && puell.value != null && puell.value >= 0.15 &&
      (puell.daily_rev_usd == null || puell.daily_rev_usd >= 1_000_000)) {
    const PUELL_DESC = {
      deep_undervalued: 'Miner capitulation — historical buy zone',
      undervalued:      'Revenue below avg — good accumulation',
      fair:             'Revenue near average — neutral',
      elevated:         'Miners incentivised to sell BTC',
      extreme:          'Peak revenue — historical cycle top',
    };
    const rev = puell.daily_rev_usd ? '$' + (puell.daily_rev_usd/1e6).toFixed(1) + 'M/day' : '';
    const puellEst = puell.estimated ? ' (est)' : '';
    tiles.push(`
      <div class="ocm-tile ${puell.cls || ''}">
        <div class="ocm-name">Puell Multiple${puellEst}</div>
        <div class="ocm-value">${puell.value?.toFixed(2) ?? '—'}</div>
        <div class="ocm-zone ${puell.cls || ''}">${(puell.zone||'').replace(/_/g,' ')}</div>
        <div class="ocm-desc">${puell.label || ''}</div>
        <div class="ocm-sub">${rev ? 'Miner rev '+rev+' · ' : ''}${PUELL_DESC[puell.zone] || ''}</div>
      </div>`);
  }

  // ── Realized Price ────────────────────────────────────────────────────────
  const rp  = mining.realized_price || mining.mvrv?.realized_price;
  const ptr = mining.price_to_realized || (rp && btcPriceUsd ? btcPriceUsd / rp : null);
  const bp  = mining.btc_price_usd;
  if (rp) {
    const rpCls   = ptr < 1.0 ? 'bull' : ptr < 1.3 ? 'bull' : ptr > 3.5 ? 'bear' : '';
    const rpZone  = ptr < 1.0 ? 'below realized' : ptr < 1.3 ? 'near realized' : ptr > 3.5 ? 'far above' : 'above realized';
    const rpLabel = ptr < 1.0 ? 'Every holder underwater — deep value'
                  : ptr < 1.3 ? 'Near cost basis — strong support zone'
                  : ptr > 3.5 ? 'Stretched valuation — distribution risk'
                  : 'Normal bull market premium';
    tiles.push(`
      <div class="ocm-tile ${rpCls}">
        <div class="ocm-name">Realized Price</div>
        <div class="ocm-value">${fmtK(rp)}</div>
        <div class="ocm-zone ${rpCls}">${rpZone} · ${ptr?.toFixed(2) ?? '—'}×</div>
        <div class="ocm-desc">${rpLabel}</div>
        <div class="ocm-sub">${bp ? 'BTC '+fmtK(bp)+' vs avg cost basis '+fmtK(rp) : 'Average cost basis of all BTC ever moved'}</div>
      </div>`);
  }

  // ── Cycle-Top Signals — the ceiling mirror of the realized-price floor ────
  const top = mining.top_signals;
  if (top) {
    const zCls  = top.zone === 'top-zone' ? 'bear' : top.zone === 'warming' ? '' : 'bull';
    // Only mention indicators that have real values — no dangling dashes
    const descParts = [];
    if (top.pi_crossed) descParts.push('Pi Cycle FIRED ⚠');
    else if (top.pi_ratio != null) descParts.push(`Pi Cycle ${(top.pi_ratio * 100).toFixed(0)}% of trigger`);
    if (top.mayer != null) descParts.push(`Mayer ${top.mayer}${top.mayer >= 2.4 ? ' ⚠' : ''}`);
    const subParts = ['MVRV 3.5× band'];
    if (top.top_band_dist_pct != null) {
      subParts.push(top.top_band_dist_pct <= 0 ? 'price ABOVE top band'
                                               : `${top.top_band_dist_pct}% below top band`);
    }
    if (top.pi_target) subParts.push(`Pi fires near ${fmtK(top.pi_target)}`);
    tiles.push(`
      <div class="ocm-tile ${zCls}">
        <div class="ocm-name">Cycle Top Watch</div>
        <div class="ocm-value">${top.top_band ? fmtK(top.top_band) : '—'}</div>
        <div class="ocm-zone ${zCls}">${top.zone.replace('-', ' ')} · heat ${top.heat}/6</div>
        <div class="ocm-desc">${descParts.join(' · ') || 'Building indicator history…'}</div>
        <div class="ocm-sub">${subParts.join(' · ')}</div>
      </div>`);
  }

  // ── Long-Term Holder Supply ──────────────────────────────────────────────
  if (lth) {
    if (lth.source === 'coinglass') {
      const chg   = lth.change_30d_pct;
      const cls   = chg > 0.5 ? 'bull' : chg < -0.5 ? 'bear' : '';
      const chgTx = chg != null ? `${chg > 0 ? '+' : ''}${chg}%` : '—';
      tiles.push(`
        <div class="ocm-tile ${cls}">
          <div class="ocm-name">LTH Supply</div>
          <div class="ocm-value">${lth.current_btc?.toLocaleString() ?? '—'} BTC</div>
          <div class="ocm-zone ${cls}">${lth.trend || ''}</div>
          <div class="ocm-desc">Held 155+ days</div>
          <div class="ocm-sub">30d change ${chgTx}</div>
        </div>`);
    } else if (lth.is_proxy) {
      const cls = lth.cls || '';
      tiles.push(`
        <div class="ocm-tile ${cls}">
          <div class="ocm-name">LTH Supply (est)</div>
          <div class="ocm-value">${lth.score ?? '—'}</div>
          <div class="ocm-zone ${cls}">${(lth.zone||'').replace(/_/g,' ')}</div>
          <div class="ocm-desc">${lth.label || ''}</div>
          <div class="ocm-sub">${(lth.reasons||[]).join(' · ') || 'Estimated from netflow + SOPR/MVRV'}</div>
        </div>`);
    }
  }

  grid.innerHTML = tiles.join('') || '<p style="color:var(--muted);padding:16px">Loading on-chain data…</p>';
}

/* ─── GoMining Strategy Advisor ───────────────────────────────────────────── */
function renderGoMiningAdvisor(strategy, symbol, gmTokenSignal) {
  const section = document.getElementById('gominingSection');
  if (!section) return;

  if (symbol !== 'BTC' || !strategy) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';

  const { phase_label, phase_cls, phase_icon, phase_desc,
          maintenance_on, reward_protection, reinvestment, reinvest_to,
          reasons = [], watch_for = [], metrics = {} } = strategy;

  // Phase banner
  document.getElementById('gominingPhase').className = `gm-phase ${phase_cls}`;
  document.getElementById('gominingPhase').innerHTML = `
    <div><span class="gm-phase-icon">${phase_icon}</span><span class="gm-phase-label">${phase_label}</span></div>
    <div class="gm-phase-desc">${phase_desc}</div>
  `;

  // Maintenance toggle — always ON
  document.getElementById('gmToggleMaintenance').className = 'gm-toggle on';
  document.getElementById('gmToggleMaintenance').textContent = 'ON ✓';

  // Reward Protection toggle
  const protEl = document.getElementById('gmToggleProtection');
  const protReason = document.getElementById('gmReasonProtection');
  if (reward_protection) {
    protEl.className = 'gm-toggle on';
    protEl.textContent = 'ON ✓';
    const prof = metrics.profitability || 1;
    protReason.textContent = prof < 1.0
      ? `Mining below break-even ($${(metrics.breakeven || 0).toLocaleString()}) — essential protection`
      : `Near break-even (${prof.toFixed(2)}×) — keep protection active`;
  } else {
    protEl.className = 'gm-toggle off';
    protEl.textContent = 'OFF';
    protReason.textContent = `Mining profitable at ${(metrics.profitability || 0).toFixed(2)}× — protection optional`;
  }

  // Reinvestment toggle — buy GOMINING tokens (Greedy Machine converts to TH)
  const reinvEl = document.getElementById('gmToggleReinvest');
  const reinvReason = document.getElementById('gmReasonReinvest');
  const gmDir = gmTokenSignal?.direction || 'NEUTRAL';
  const gmStr = gmTokenSignal?.strength || 0;
  const gmPrice = gmTokenSignal?.price;
  const gm30d = gmTokenSignal?.change_30d_pct;
  const priceNote = gmPrice ? ` · $${Number(gmPrice).toFixed(4)}` : '';
  const chgNote = gm30d != null ? ` (${gm30d > 0 ? '+' : ''}${gm30d}% 30d)` : '';

  if (reinvestment) {
    reinvEl.className = 'gm-toggle on';
    reinvEl.textContent = 'ON → GOMINING tokens ✓';
    reinvReason.textContent = `Mining profitable + Hash Ribbon bullish + GOMINING ${gmDir} ${gmStr}%${priceNote}${chgNote} — Greedy Machine auto-converts tokens → TH`;
  } else if (phase_cls === 'gold') {
    reinvEl.className = 'gm-toggle warn';
    reinvEl.textContent = 'OFF ⚠';
    reinvReason.textContent = 'Late cycle — collect BTC rewards, do not buy GOMINING tokens at high valuations';
  } else if (gmDir === 'SHORT') {
    reinvEl.className = 'gm-toggle off';
    reinvEl.textContent = 'OFF ⚠ token SHORT';
    reinvReason.textContent = `GOMINING token in downtrend${priceNote}${chgNote} — wait for signal to turn LONG before buying tokens`;
  } else {
    reinvEl.className = 'gm-toggle off';
    reinvEl.textContent = 'OFF';
    reinvReason.textContent = 'Accumulate real BTC now — wait for Hash Ribbon + profitability before buying GOMINING tokens';
  }

  // Reward payout currency — take mining rewards in BTC or GOMINING
  const ccyEl = document.getElementById('gmToggleRewardCcy');
  const ccyReason = document.getElementById('gmReasonRewardCcy');
  const rc = strategy.reward_currency;
  if (ccyEl && rc) {
    const take = rc.take || 'BTC';
    ccyEl.className = 'gm-toggle ' + (take === 'GOMINING' ? 'on' : take === 'SPLIT' ? 'warn' : 'off');
    ccyEl.textContent = take === 'GOMINING' ? 'GOMINING ✓' : take === 'SPLIT' ? 'SPLIT ⚖' : 'BTC ✓';
    if (ccyReason) ccyReason.textContent = rc.reasoning || '';
  }

  // TH purchase advisor — is hashpower cheap or expensive right now?
  const thEl = document.getElementById('gmToggleTh');
  const thReason = document.getElementById('gmReasonTh');
  const th = strategy.th_purchase;
  if (thEl && th) {
    thEl.className = 'gm-toggle ' + (th.signal === 'buy_now' ? 'on' : th.signal === 'ok' ? 'warn' : 'off');
    thEl.textContent = th.icon + ' ' + (th.signal === 'buy_now' ? 'BUY NOW' : th.signal === 'ok' ? 'OK TO BUY' : 'WAIT');
    if (thReason) thReason.textContent = th.reasoning || '';
  }

  // Sell radars — graduated pre-indicators for TH and GOMINING peaks
  const radar = (t, sig, elId, reasonId) => {
    const el = document.getElementById(elId);
    const re = document.getElementById(reasonId);
    if (!el || !t) return;
    const cls = t.signal === 'sell_now' ? 'off' : t.signal === 'approaching' ? 'warn' : 'on';
    el.className = 'gm-toggle ' + cls;
    el.textContent = `${t.icon} ${t.label.split(' — ')[0]}`;
    if (re) re.textContent = t.reasoning || '';
  };
  radar(strategy.th_sell, 'th', 'gmToggleThSell', 'gmReasonThSell');
  radar(strategy.gm_sell, 'gm', 'gmToggleGmSell', 'gmReasonGmSell');

  // BTC Harvest Timer
  const harvestEl = document.getElementById('gmHarvestBanner');
  if (harvestEl && strategy.harvest) {
    const h = strategy.harvest;
    const pctLine = h.sell_pct > 0
      ? `<div class="gm-harvest-pct">Suggested: sell ~${h.sell_pct}% of your BTC rewards</div>`
      : `<div class="gm-harvest-pct">Keep 100% of BTC rewards — do not sell yet</div>`;
    // Metric chips
    const chips = [];
    if (h.mvrv)    chips.push(`MVRV ${Number(h.mvrv).toFixed(2)} <span class="gm-chip-zone">${(h.mvrv_zone||'').replace(/_/g,' ')}</span>`);
    if (h.sopr)    chips.push(`SOPR ${Number(h.sopr).toFixed(4)} <span class="gm-chip-zone">${(h.sopr_zone||'').replace(/_/g,' ')}</span>`);
    if (h.puell)   chips.push(`Puell ${Number(h.puell).toFixed(2)} <span class="gm-chip-zone">${(h.puell_zone||'').replace(/_/g,' ')}</span>`);
    if (h.realized_price) chips.push(`Realized $${Number(h.realized_price).toLocaleString()} · ${h.price_to_realized ? Number(h.price_to_realized).toFixed(2)+'×' : ''}`);
    harvestEl.className = `gm-harvest ${h.cls}`;
    harvestEl.innerHTML = `
      <div class="gm-harvest-icon">${h.icon}</div>
      <div class="gm-harvest-body">
        <div class="gm-harvest-label">${h.label}</div>
        ${pctLine}
        <div class="gm-harvest-reason">${h.reasoning}</div>
        ${chips.length ? `<div class="gm-harvest-chips">${chips.map(c=>`<span class="gm-chip">${c}</span>`).join('')}</div>` : ''}
      </div>`;
  }

  // Reasons list
  document.getElementById('gmReasonsList').innerHTML =
    reasons.map(r => `<li>${r}</li>`).join('') ||
    '<li>Loading on-chain data…</li>';

  // Watch for list
  document.getElementById('gmWatchList').innerHTML =
    watch_for.map(w => `<li>${w}</li>`).join('') ||
    '<li>No specific triggers — maintain current settings</li>';
}

function renderWhaleActivity(events) {
  const el = document.getElementById('whaleActivity');
  if (!el) return;
  if (!events || !events.length) {
    el.innerHTML = '<p class="whale-empty">No large trade detected in last 5 candles.</p>';
    return;
  }

  const DIR_META = {
    bullish:            { icon: '🐋', label: 'Bullish Whale',          cls: 'bull', desc: 'Aggressive buying — large long entry' },
    bearish:            { icon: '🐻', label: 'Bearish Whale',          cls: 'bear', desc: 'Aggressive selling — large short entry' },
    absorption_bull:    { icon: '🛡️', label: 'Bull Absorption',        cls: 'bull', desc: 'Heavy buying absorbed at resistance — price held up' },
    absorption_bear:    { icon: '🛡️', label: 'Bear Absorption',        cls: 'bear', desc: 'Heavy selling absorbed at support — price defended' },
    bullish_absorption: { icon: '💪', label: 'Bullish (Bears Failed)', cls: 'bull', desc: 'Large sell into buyers — sellers failed, bullish signal' },
    bearish_rejection:  { icon: '❌', label: 'Bearish Rejection',      cls: 'bear', desc: 'Large buy rejected — failed breakout, bearish signal' },
  };

  el.innerHTML = events.map(e => {
    const m    = DIR_META[e.direction] || { icon: '❓', label: e.direction, cls: '', desc: '' };
    const when = e.candles_ago === 1 ? 'Last candle' : `${e.candles_ago} candles ago`;
    return `<div class="whale-event whale-${m.cls}">
      <div class="whale-event-top">
        <span class="whale-icon">${m.icon}</span>
        <span class="whale-label ${m.cls}">${m.label}</span>
        <span class="whale-ago">${when}</span>
      </div>
      <div class="whale-stats">
        <span class="whale-stat">Vol <strong>${e.vol_multiple}×</strong> avg</span>
        <span class="whale-stat">Taker Buy <strong>${e.taker_ratio}%</strong></span>
        <span class="whale-stat">Body <strong>${e.body_pct > 0 ? '+' : ''}${e.body_pct}%</strong></span>
      </div>
      <div class="whale-desc">${m.desc}</div>
    </div>`;
  }).join('');
}

function renderArkhamPanel(ws) {
  const section = document.getElementById('arkhamSection');
  const tbody   = document.getElementById('arkhamBody');
  const summary = document.getElementById('arkhamSummary');
  const sub     = document.getElementById('arkhamSub');
  if (!section || !tbody) return;

  // Hide when CoinGlass not configured or no data (only BTC/ETH)
  if (!ws || !ws.source) {
    section.style.display = 'none';
    return;
  }

  section.style.display = '';

  const pressure = ws.pressure || 'neutral';
  const netflow  = ws.netflow  || 0;
  const inflow   = ws.inflow   || 0;
  const outflow  = ws.outflow  || 0;
  const pts      = ws.signal_pts || 0;
  const sym      = ws.symbol || '';

  const PRESSURE_META = {
    high:         { cls: 'high',   icon: '🔴', label: 'HIGH SELL PRESSURE'  },
    medium:       { cls: 'medium', icon: '🟠', label: 'MEDIUM SELL PRESSURE' },
    low:          { cls: 'low',    icon: '🟡', label: 'LIGHT SELL FLOW'      },
    neutral:      { cls: 'none',   icon: '⚪', label: 'BALANCED FLOW'        },
    withdrawal:   { cls: 'none',   icon: '🟢', label: 'EXCHANGE WITHDRAWAL'  },
    accumulation: { cls: 'none',   icon: '💚', label: 'STRONG ACCUMULATION'  },
  };
  const meta = PRESSURE_META[pressure] || PRESSURE_META.neutral;

  const sign = netflow >= 0 ? '+' : '';
  const ptsLabel = pts > 0
    ? `<span class="arkham-pts" style="color:var(--bull)">+${pts} signal pts</span>`
    : pts < 0 ? `<span class="arkham-pts">${pts} signal pts</span>` : '';

  summary.innerHTML = `
    <span class="arkham-pressure ${meta.cls}">${meta.icon} ${meta.label}</span>
    <span class="arkham-total">Netflow: <strong>${sign}${Number(netflow).toLocaleString('en-US', {maximumFractionDigits: 1})} ${sym}</strong></span>
    <span class="arkham-total" style="font-size:.68rem">↑ ${Number(inflow).toLocaleString('en-US', {maximumFractionDigits:1})} in · ↓ ${Number(outflow).toLocaleString('en-US', {maximumFractionDigits:1})} out</span>
    ${ptsLabel}
  `;

  if (sub) sub.textContent = `CoinGlass exchange netflow · last ${ws.window || '8h'}`;

  // History table — one row per 8h period
  const history = ws.history || [];
  if (!history.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="arkham-empty">No netflow data available</td></tr>`;
    return;
  }

  // Replace table headers for netflow view
  const thead = tbody.closest('table')?.querySelector('thead tr');
  if (thead) {
    thead.innerHTML = `
      <th>Period</th>
      <th style="text-align:right">Netflow (${sym})</th>
      <th style="text-align:right">Signal</th>
    `;
  }

  tbody.innerHTML = [...history].reverse().map(h => {
    const nf  = h.netflow || 0;
    const cls = nf > 0 ? 'arkham-usd' : nf < 0 ? 'arkham-to' : '';
    const lbl = nf > 500 ? '🔴 sell' : nf > 0 ? '⚠ mild sell' : nf < -500 ? '💚 accumulate' : nf < 0 ? '🟢 withdraw' : '⚪ neutral';
    const dt  = h.timestamp ? new Date(h.timestamp * 1000).toLocaleString('en-US', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '—';
    return `<tr>
      <td class="arkham-ago">${dt}</td>
      <td class="${cls}" style="text-align:right;font-weight:600">${nf >= 0 ? '+' : ''}${Number(nf).toLocaleString('en-US', {maximumFractionDigits:1})}</td>
      <td style="text-align:right;font-size:.68rem">${lbl}</td>
    </tr>`;
  }).join('');
}

function renderLSCard(ls) {
  const el    = document.getElementById('lsRatio');
  const sigEl = document.getElementById('lsSignal');
  const lpEl  = document.getElementById('lsLongPct');
  const spEl  = document.getElementById('lsShortPct');
  const barEl = document.getElementById('lsLongBar');
  if (!el) return;
  if (!ls || !ls.ratio) {
    el.textContent  = 'N/A';
    if (sigEl) sigEl.textContent = 'Unavailable for this pair';
    return;
  }
  const { ratio, long_pct, short_pct } = ls;
  el.textContent = ratio.toFixed(2);
  let sig = 'Neutral positioning', sigColor = 'var(--muted2)';
  if      (ratio < 0.65) { sig = 'Crowd max short — contrarian LONG signal';  sigColor = 'var(--bull)'; }
  else if (ratio < 0.85) { sig = 'Moderate short bias — lean long';            sigColor = 'var(--bull)'; }
  else if (ratio > 2.5)  { sig = 'Crowd max long — contrarian SHORT signal';   sigColor = 'var(--bear)'; }
  else if (ratio > 1.5)  { sig = 'Crowd long-heavy — late-cycle caution';      sigColor = 'var(--bear)'; }
  if (sigEl) { sigEl.textContent = sig; sigEl.style.color = sigColor; }
  if (lpEl)  lpEl.textContent = `Long ${long_pct.toFixed(1)}%`;
  if (spEl)  spEl.textContent = `Short ${short_pct.toFixed(1)}%`;
  if (barEl) barEl.style.width = `${long_pct}%`;
}

/* ─── ETF Flows ───────────────────────────────────────────────────────────── */
function renderEtfFlows(etf, symbol) {
  const sec  = document.getElementById('etfFlowsSection');
  const grid = document.getElementById('etfFlowGrid');
  if (!sec || !grid) return;

  const ETF_SYMBOLS = ['BTC', 'ETH', 'SOL', 'XRP', 'HBAR'];
  if (!ETF_SYMBOLS.includes(symbol)) {
    sec.style.display = 'none';
    return;
  }
  sec.style.display = '';

  if (!etf) {
    // XRP/HBAR: ETFs are live but SoSoValue's Open API doesn't serve their
    // flow history yet (dashboard-only). Link to the live dashboard meanwhile.
    const SLUGS = { BTC: 'us-btc-spot', ETH: 'us-eth-spot', SOL: 'us-sol-spot',
                    XRP: 'us-xrp-spot', HBAR: 'us-hbar-spot' };
    const apiLag = (symbol === 'XRP' || symbol === 'HBAR');
    const slug = SLUGS[symbol];
    grid.innerHTML = `<div class="etf-unavailable">${apiLag
      ? `${symbol} spot ETFs are live, but SoSoValue's API doesn't serve their flow history yet
         (dashboard-only). View live flows at
         <a href="https://sosovalue.com/assets/etf/${slug}" target="_blank" rel="noopener">sosovalue.com/assets/etf/${slug}</a>
         — this card will populate automatically once their API adds coverage.`
      : 'ETF flow data unavailable — data source unreachable, retrying automatically'}</div>`;
    const sub = document.getElementById('etfFlowsSub');
    if (sub) sub.textContent = 'Institutional inflow / outflow';
    return;
  }

  const sub = document.getElementById('etfFlowsSub');
  if (sub) {
    const src = etf.source === 'sosovalue' ? 'SoSoValue' : etf.source === 'coinglass' ? 'CoinGlass' : etf.source || '';
    // Flag if the latest available day is >2 days old (weekend/holiday is
    // normal; older than that suggests the feed itself is behind).
    let asOf = '';
    if (etf.as_of) {
      const ageDays = Math.floor((Date.now() - new Date(etf.as_of + 'T00:00:00Z').getTime()) / 86400000);
      asOf = ` · as of ${etf.as_of}${ageDays >= 3 ? ` (${ageDays}d old)` : ''}`;
    }
    sub.textContent = `Institutional inflow / outflow${src ? ' · ' + src : ''}${asOf}`;
  }

  const fmtM = v => {
    if (v == null) return '—';
    const s = v > 0 ? '+' : v < 0 ? '−' : '';
    return `${s}$${Math.abs(v).toFixed(0)}M`;
  };

  const trendCls = etf.trend === 'inflow' ? 'bull' : etf.trend === 'outflow' ? 'bear' : '';
  const trendLbl = etf.trend === 'inflow' ? '▲ Inflow' : etf.trend === 'outflow' ? '▼ Outflow' : '— Neutral';

  const vsBadge = (vs, win) => {
    if (!vs) return '';
    const map = {
      highest:   [`🔺 Highest in ${win}`, 'etf-hi'],
      lowest:    [`🔻 Lowest in ${win}`,  'etf-lo'],
      above_avg: ['↑ Above avg',           'etf-above'],
      below_avg: ['↓ Below avg',           'etf-below'],
      normal:    ['— Normal',              'etf-normal'],
    };
    const [label, cls] = map[vs] || ['—', ''];
    return `<span class="etf-badge ${cls}">${label}</span>`;
  };

  // Mini bar chart from recent_days
  const days = etf.recent_days || [];
  const bars = days.map(d => {
    const v   = d.m || 0;
    const pct = days.length ? Math.abs(v) / (Math.max(...days.map(x => Math.abs(x.m || 0))) || 1) * 100 : 0;
    const cls = v >= 0 ? 'etf-bar-in' : 'etf-bar-out';
    return `<div class="etf-bar-wrap" title="${fmtM(v)}">
      <div class="etf-bar ${cls}" style="height:${Math.max(4, pct)}%"></div>
    </div>`;
  }).join('');

  // "Today" is misleading when the latest available day is 1-3d back — label
  // it with the actual weekday, or "Today"/"Yesterday" when it truly is.
  const _etfDayLabel = (iso) => {
    const d = Math.floor((Date.now() - new Date(iso + 'T00:00:00Z').getTime()) / 86400000);
    if (d <= 0) return 'Today';
    if (d === 1) return 'Yesterday';
    return new Date(iso + 'T00:00:00Z').toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  };

  grid.innerHTML = `
    <div class="etf-summary">
      <div class="etf-today">
        <div class="etf-today-val ${trendCls}">${fmtM(etf.today_m)}</div>
        <div class="etf-today-lbl">${etf.as_of ? _etfDayLabel(etf.as_of) : 'Latest'}</div>
        <div class="etf-trend-badge ${trendCls}">${trendLbl}</div>
      </div>
      <div class="etf-stats">
        <div class="etf-stat"><span class="etf-stat-lbl">7d total</span><span class="etf-stat-val ${etf.week_total_m >= 0 ? 'bull' : 'bear'}">${fmtM(etf.week_total_m)}</span></div>
        <div class="etf-stat"><span class="etf-stat-lbl">30d total</span><span class="etf-stat-val ${etf.month_total_m >= 0 ? 'bull' : 'bear'}">${fmtM(etf.month_total_m)}</span></div>
        <div class="etf-stat"><span class="etf-stat-lbl">7d avg/day</span><span class="etf-stat-val ${etf.week_avg_m >= 0 ? 'bull' : 'bear'}">${fmtM(etf.week_avg_m)}</span></div>
        <div class="etf-stat"><span class="etf-stat-lbl">30d avg/day</span><span class="etf-stat-val ${etf.month_avg_m >= 0 ? 'bull' : 'bear'}">${fmtM(etf.month_avg_m)}</span></div>
      </div>
      <div class="etf-significance">
        <div class="etf-sig-row">vs 7d: ${vsBadge(etf.vs_week, '7d')}</div>
        <div class="etf-sig-row">vs 30d: ${vsBadge(etf.vs_month, '30d')}</div>
      </div>
    </div>
    ${bars.length ? `<div class="etf-barchart">${bars}</div>
    <div class="etf-bar-legend"><span>← ${days.length}d ago</span><span>Today →</span></div>` : ''}
  `;
}

function renderFNGCard(fg) {
  const valEl = document.getElementById('fngValue');
  const lblEl = document.getElementById('fngLabel');
  if (!valEl) return;
  if (!fg || fg.value == null) { valEl.textContent = '—'; return; }
  const val = fg.value;
  valEl.textContent = val;
  if (lblEl) lblEl.textContent = fg.label || '';
  let color = '#6366f1';
  if      (val <= 25) color = '#10b981';
  else if (val <= 45) color = '#f59e0b';
  else if (val <= 55) color = '#6366f1';
  else if (val <= 75) color = '#f59e0b';
  else                color = '#ef4444';
  valEl.style.color = color;

  const canvas = document.getElementById('fngGauge');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = Math.min(120, canvas.parentElement.clientWidth - 8);
  const H = Math.round(W * 70 / 120);
  canvas.width = W; canvas.height = H;
  ctx.clearRect(0, 0, W, H);
  const cx = W / 2, cy = H - 8, r = Math.min(W, H * 2) / 2 - 6;
  ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI, 0);
  ctx.strokeStyle = '#1e293b'; ctx.lineWidth = 10; ctx.stroke();
  const frac  = val / 100;
  ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI, Math.PI + frac * Math.PI);
  ctx.strokeStyle = color; ctx.lineWidth = 10; ctx.stroke();
  const angle = Math.PI + frac * Math.PI;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + (r - 4) * Math.cos(angle), cy + (r - 4) * Math.sin(angle));
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
}

function renderNewsCard(news) {
  const card    = document.getElementById('newsCard');
  const badge   = document.getElementById('newsSignalBadge');
  const counts  = document.getElementById('newsCounts');
  const list    = document.getElementById('newsList');
  const srcEl   = document.getElementById('newsSource');
  if (!card || !news) return;

  const signal = news.signal || 'neutral';
  const sigLabels = { bullish: 'Bullish', bearish: 'Bearish', neutral: 'Neutral' };
  const sigCls    = { bullish: 'bull',    bearish: 'bear',    neutral: 'muted2' };
  badge.textContent = sigLabels[signal] || 'Neutral';
  badge.className   = `news-signal-badge news-sig-${signal}`;

  const b = news.bullish || 0, bear = news.bearish || 0, n = news.neutral || 0;
  counts.textContent = `${b} bullish · ${bear} bearish · ${n} neutral (last 48h)`;

  if (srcEl) {
    srcEl.textContent = news.source === 'cryptopanic' ? 'CryptoPanic' : news.source === 'rss' ? 'RSS' : '';
  }

  const articles = news.articles || [];
  if (!articles.length) {
    list.innerHTML = '<div class="news-empty">No recent news found for this coin.</div>';
    return;
  }

  const timeAgo = (iso) => {
    try {
      const diff = (Date.now() - new Date(iso).getTime()) / 60000;
      if (!isFinite(diff)) return 'recent';
      if (diff < 60)   return `${Math.round(diff)}m ago`;
      if (diff < 1440) return `${Math.round(diff / 60)}h ago`;
      return `${Math.round(diff / 1440)}d ago`;
    } catch { return ''; }
  };

  const item = a => {
    const sc  = a.sentiment === 'bullish' ? 'bull' : a.sentiment === 'bearish' ? 'bear' : 'muted2';
    const dot = a.sentiment === 'bullish' ? '▲' : a.sentiment === 'bearish' ? '▼' : '·';
    const src = (a.source || '').replace('www.', '');
    const href = a.url ? `href="${a.url}" target="_blank" rel="noopener"` : '';
    return `<div class="news-item">
      <span class="news-dot ${sc}">${dot}</span>
      <div class="news-body">
        <a class="news-title ${href ? '' : 'no-link'}" ${href}>${a.title}</a>
        <span class="news-meta">${src} · ${timeAgo(a.published_at)}</span>
      </div>
    </div>`;
  };
  const head = articles.slice(0, 6).map(item).join('');
  const rest = articles.slice(6).map(item).join('');
  list.innerHTML = head +
    (rest ? `<div id="newsRest" style="display:none">${rest}</div>
      <button class="sn-showall" onclick="const r=document.getElementById('newsRest');
        const open=r.style.display==='none'; r.style.display=open?'':'none';
        this.textContent=open?'Show less':'Show all ${articles.length} articles ▾';">
        Show all ${articles.length} articles ▾</button>` : '');
}

function renderCVDPanel(id, cvd, series, valId, trendId) {
  if (!cvd) return;
  const el = document.getElementById(valId);
  const isFlat = !cvd.series?.some(d => Math.abs(Number(d.cvd || 0)) > 0);
  el.textContent = isFlat ? 'Estimated' : Number(cvd.current).toLocaleString('en-US', { maximumFractionDigits: 2 });
  el.style.color = cvd.trend === 'bullish' ? 'var(--bull)' : cvd.trend === 'bearish' ? 'var(--bear)' : 'var(--neutral)';
  // The big number is the CUMULATIVE total over the visible window (its sign
  // depends on where the window starts); the badge/colour reflect the RECENT
  // flow — surface that recent net so a negative total with a green "bullish"
  // badge isn't confusing.
  const d5 = (cvd.series || []).slice(-5).reduce((a, d) => a + Number(d.delta || 0), 0);
  el.title = `Cumulative net taker flow over the visible window — the sign depends on the window start.\n` +
             `Badge = direction of the RECENT flow (last 5 candles: ${d5 >= 0 ? '+' : ''}${d5.toLocaleString('en-US', { maximumFractionDigits: 0 })}).`;

  // Show how many exchanges contributed to the aggregated spot CVD
  if (id === 'spot' && cvd.label === 'spot_aggregated') {
    const lbl = document.getElementById('spotCvdSourceLabel');
    if (lbl) lbl.textContent = cvd.n_sources > 1 ? `Agg. (${cvd.n_sources} ex.)` : 'Aggregated';
  }

  // Reflect the real futures data source in the "Auto" dropdown option so the
  // user can confirm whether CoinGlass (multi-exchange) or the free aggregation
  // is actually powering the panel.
  if (id === 'fut') {
    const sel = document.getElementById('futCvdSource');
    const auto = sel && sel.querySelector('option[value="auto"]');
    if (auto) {
      const srcLabel = cvd.source === 'coinglass' ? 'Auto · CoinGlass'
                     : cvd.source === 'aggregated' ? `Auto · Agg${cvd.n_sources ? `(${cvd.n_sources})` : ''}`
                     : 'Auto';
      auto.textContent = srcLabel;
    }
  }

  const tEl = document.getElementById(trendId);
  tEl.textContent = isFlat ? 'unavailable' : (cvd.trend || 'neutral');
  tEl.className = `cvd-trend ${cvd.trend || 'neutral'}`;

  if (series && cvd.series?.length) {
    const data = cvd.series
      .filter(d => d.cvd != null)
      .map(d => ({ time: Math.floor(d.timestamp / 1000), value: d.cvd }));
    const unique = [...new Map(data.map(d => [d.time, d])).values()].sort((a, b) => a.time - b.time);
    if (unique.length >= 2) {
      const color = cvd.trend === 'bullish' ? '#10b981' : cvd.trend === 'bearish' ? '#ef4444' : '#6366f1';
      series.applyOptions({ lineColor: color, topColor: color + '33', bottomColor: color + '00' });
      series.setData(unique);
    }
  }
}

/* ─── FVG Table ───────────────────────────────────────────────────────────── */
function renderFVGTable(fvgs) {
  const tbody = document.getElementById('fvgBody');
  const unfilled = (fvgs || []).filter(f => !f.filled);
  document.getElementById('fvgCount').textContent = unfilled.length;

  if (!unfilled.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No unfilled FVGs detected</td></tr>';
    return;
  }

  tbody.innerHTML = unfilled.slice(0, 12).map(f => {
    const cls    = f.type === 'bullish' ? 'bull' : 'bear';
    const isBag  = f.gap_type === 'bag';
    const typeLabel = isBag
      ? `<span class="tag ${cls} tag-bag">BAG</span>`
      : `<span class="tag ${cls}">FVG</span>`;
    const dirLabel = `<span class="tag ${cls}">${f.type}</span>`;
    return `<tr${isBag ? ' class="fvg-bag-row"' : ''}>
      <td>${typeLabel}</td>
      <td>$${Number(f.top).toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
      <td>$${Number(f.bottom).toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
      <td>${f.size_pct.toFixed(3)}%</td>
      <td class="${Number(f.distance_pct) >= 0 ? 'bull' : 'bear'}">${pct(f.distance_pct)}</td>
      <td>${dirLabel}</td>
    </tr>`;
  }).join('');
}

/* ─── Active Trade Monitor ────────────────────────────────────────────────── */
function renderActiveTrade(a, t) {
  const isLong  = t.direction === 'LONG';
  const cur     = a.candles?.length ? a.candles[a.candles.length - 1].close : null;
  const fmt     = fmtPrice;
  const days    = Math.floor((Date.now() - t.timestamp) / 86400000);
  const dated   = new Date(t.timestamp).toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' });

  // Live P&L
  let pnlHtml = '<span class="tm-pnl muted">—</span>';
  if (cur) {
    const pnl = isLong ? (cur - t.entry) / t.entry * 100 : (t.entry - cur) / t.entry * 100;
    const cls = pnl >= 0 ? 'bull' : 'bear';
    pnlHtml = `<span class="tm-pnl ${cls}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%</span>`;
  }

  // Level status: hit / approaching / open
  function levelStatus(target, label) {
    if (!cur || !target) return { cls: '', badge: '' };
    const dist = isLong ? (target - cur) / cur * 100 : (cur - target) / cur * 100;
    if (label === 'SL') {
      // SL is bad when hit (for longs: cur < sl)
      const hit = isLong ? cur <= target : cur >= target;
      if (hit) return { cls: 'bear', badge: '<span class="tm-lvl-badge hit-sl">⚠ Hit</span>' };
      if (Math.abs(dist) < 3) return { cls: 'bear', badge: '<span class="tm-lvl-badge near-sl">Near</span>' };
      return { cls: 'bear', badge: `<span class="tm-lvl-badge">${Math.abs(dist).toFixed(1)}% away</span>` };
    } else {
      // TP is good when hit (for longs: cur >= tp)
      const hit = isLong ? cur >= target : cur <= target;
      if (hit) return { cls: 'bull', badge: '<span class="tm-lvl-badge hit-tp">✓ Hit</span>' };
      if (Math.abs(dist) < 3) return { cls: 'bull', badge: '<span class="tm-lvl-badge near-tp">Close</span>' };
      return { cls: 'bull', badge: `<span class="tm-lvl-badge">${Math.abs(dist).toFixed(1)}% away</span>` };
    }
  }

  const sl  = levelStatus(t.sl,  'SL');
  const tp1 = levelStatus(t.tp1, 'TP1');
  const tp2 = levelStatus(t.tp2, 'TP2');
  const tp3 = levelStatus(t.tp3, 'TP3');

  // Signal change indicator
  const curDir = a.signal?.direction || 'NEUTRAL';
  const curStr = a.signal?.strength  || 0;
  const sigChanged = curDir !== t.direction;
  const sigCls  = curDir === 'LONG' ? 'bull' : curDir === 'SHORT' ? 'bear' : 'muted';
  const sigHtml = sigChanged
    ? `<span class="tm-sig-warn">⚠ Signal now <span class="${sigCls}">${curDir} (${curStr}/100)</span> — was ${t.direction} (${t.strength}/100) when entered</span>`
    : `<span class="tm-sig-ok">✓ Signal still ${curDir} (${curStr}/100)</span>`;

  // Exit rules from snapshot
  const er = t.exit_rules || {};
  const exitRulesHtml = er.rule1 ? `
    <details class="tm-er-details">
      <summary>Your Exit Rules</summary>
      <ol class="tm-er-list">
        <li>${er.rule1}</li>
        <li>${er.rule2}</li>
        <li>${er.rule3}</li>
        <li>${er.rule4}</li>
      </ol>
      <div class="tm-er-timing">
        <span>⏱ ${er.timing}</span>
        <span>📅 ${er.hold}</span>
      </div>
    </details>` : '';

  return `
    <div class="tm-active-banner">
      <span class="tm-active-label">📌 Active Trade</span>
      <span class="tm-active-meta">Entered ${dated} · ${days} day${days !== 1 ? 's' : ''} open</span>
      ${pnlHtml}
    </div>
    <div class="tm-sig-row">${sigHtml}</div>
    <div class="tm-active-grid">
      <div class="tm-col">
        <div class="tm-section-title">Your Levels</div>
        <div class="tm-row">
          <span class="tm-label">Entry</span>
          <span class="tm-val">${fmt(t.entry)}</span>
        </div>
        ${cur ? `<div class="tm-row">
          <span class="tm-label">Current Price</span>
          <span class="tm-val">${fmt(cur)}</span>
        </div>` : ''}
        <div class="tm-divider"></div>
        <div class="tm-row">
          <span class="tm-label">Stop Loss</span>
          <span class="tm-val ${sl.cls}">${fmt(t.sl)} ${sl.badge}</span>
        </div>
        <div class="tm-row">
          <span class="tm-label">TP 1 <span style="color:var(--muted);font-size:.68rem">50%</span></span>
          <span class="tm-val ${tp1.cls}">${fmt(t.tp1)} ${tp1.badge}</span>
        </div>
        <div class="tm-row">
          <span class="tm-label">TP 2 <span style="color:var(--muted);font-size:.68rem">30%</span></span>
          <span class="tm-val ${tp2.cls}">${fmt(t.tp2)} ${tp2.badge}</span>
        </div>
        <div class="tm-row">
          <span class="tm-label">TP 3 <span style="color:var(--muted);font-size:.68rem">20%</span></span>
          <span class="tm-val ${tp3.cls}">${fmt(t.tp3)} ${tp3.badge}</span>
        </div>
        ${t.rr ? `<div class="tm-divider"></div>
        <div class="tm-row"><span class="tm-label">R/R at entry</span><span class="tm-val">${t.rr}:1</span></div>` : ''}
      </div>
      <div class="tm-col">
        ${exitRulesHtml}
        <div class="tm-active-actions">
          <button class="btn-tc btn-tc-close" onclick="showCloseForm('${t.id}');renderMyTrades()">Close Trade</button>
          <div id="cf-tm-${t.id}" class="tc-close-form" style="display:none">
            <input id="cp-tm-${t.id}" class="tc-price-input" type="number" placeholder="Exit price" step="any"/>
            <button class="btn-tc btn-tc-confirm" onclick="confirmCloseTM('${t.id}')">Confirm</button>
            <button class="btn-tc" onclick="document.getElementById('cf-tm-${t.id}').style.display='none'">Cancel</button>
          </div>
        </div>
      </div>
    </div>`;
}

/* ─── Trade Management ────────────────────────────────────────────────────── */
const TF_CLOSE_RULES = {
  // candle       : close-trigger candle label
  // hold         : expected trade duration
  // check        : how often to review
  // trail        : swing unit for trailing SL after TP2
  // be1          : breakeven note after TP1
  // sidewaysCount: consecutive sideways candles before escalating
  // sidewaysDesc : human label for that count
  // checkTF      : next higher timeframe to consult when sideways
  '1H':  { candle: '1H candle',      hold: '4 – 24 hours',   check: 'every 1 h',        trail: '1H swing',      be1: 'move SL to entry quickly — intraday trade',          sidewaysCount: 4, sidewaysDesc: '4 consecutive sideways 1H candles (= 4 hours)',          checkTF: '4H'  },
  '2H':  { candle: '2H candle',      hold: '8 – 48 hours',   check: 'every 2 h',        trail: '2H swing',      be1: 'move SL to entry — short-term trade; protect quickly',sidewaysCount: 3, sidewaysDesc: '3 consecutive sideways 2H candles (= 6 hours)',          checkTF: '4H'  },
  '4H':  { candle: '4H candle',      hold: '1 – 5 days',     check: 'every 4 h',        trail: '4H swing',      be1: 'move SL to entry — short TF; protect quickly',       sidewaysCount: 3, sidewaysDesc: '3 consecutive sideways 4H candles (= 1 12H candle)', checkTF: '12H' },
  '8H':  { candle: '8H candle',      hold: '3 – 10 days',    check: 'every 8 h',        trail: '8H swing',      be1: 'move SL to entry (breakeven)',                        sidewaysCount: 3, sidewaysDesc: '3 consecutive sideways 8H candles (= 1 day)',        checkTF: '1D'  },
  '12H': { candle: '12H candle',     hold: '5 – 14 days',    check: 'twice daily',      trail: '12H swing',     be1: 'move SL to entry (breakeven)',                        sidewaysCount: 2, sidewaysDesc: '2 consecutive sideways 12H candles (= 1 day)',       checkTF: '1D'  },
  '1D':  { candle: 'daily candle',   hold: '1 – 4 weeks',    check: 'daily at close',   trail: 'daily candle',  be1: 'move SL to entry (breakeven)',                        sidewaysCount: 3, sidewaysDesc: '3 consecutive sideways daily candles (= 3 days)',    checkTF: '1W'  },
  '1W':  { candle: 'weekly candle',  hold: '1 – 3 months',   check: 'weekly at close',  trail: 'weekly candle', be1: 'move SL to entry (breakeven)',                        sidewaysCount: 2, sidewaysDesc: '2 consecutive sideways weekly candles (= 2 weeks)',  checkTF: '1M'  },
  '2W':  { candle: '2W candle',      hold: '2 – 6 months',   check: 'every 2 weeks',    trail: '2W candle',     be1: 'move SL to entry — wide TF; be patient',              sidewaysCount: 2, sidewaysDesc: '2 consecutive sideways 2W candles (= 1 month)',      checkTF: '1M'  },
  '3W':  { candle: '3W candle',      hold: '2 – 6 months',   check: 'every 3 weeks',    trail: '3W candle',     be1: 'move SL to entry — wide TF; be patient',              sidewaysCount: 2, sidewaysDesc: '2 consecutive sideways 3W candles (= 6 weeks)',      checkTF: '1M'  },
  '1M':  { candle: 'monthly candle', hold: '3 – 12 months',  check: 'monthly at close', trail: 'monthly candle',be1: 'move SL to entry — macro trade; hold conviction',     sidewaysCount: 2, sidewaysDesc: '2 consecutive sideways monthly candles (= 2 months)', checkTF: 'Quarterly' },
};

function renderTradeManagement(a) {
  const body  = document.getElementById('tradeMgmtBody');
  const dirEl = document.getElementById('tradeMgmtDir');
  const tfEl  = document.getElementById('tradeMgmtTf');
  const logBtn = document.getElementById('logTradeBtn');
  if (!body) return;

  const sig = a.signal || {};
  const tf  = a.timeframe || '1W';

  // ── Check for an open logged trade for this symbol + timeframe ─────────────
  const activeTrade = getTrades().find(t =>
    t.status === 'open' && t.symbol === a.symbol && t.timeframe === tf
  );

  if (activeTrade) {
    // Active trade monitor mode
    dirEl.textContent = activeTrade.direction;
    dirEl.className   = 'trade-mgmt-dir ' + activeTrade.direction.toLowerCase();
    tfEl.textContent  = tf;
    if (logBtn) logBtn.style.display = 'none';
    body.innerHTML = renderActiveTrade(a, activeTrade);
    return;
  }

  // ── No active trade — show fresh signal ────────────────────────────────────
  const dir  = sig.direction || 'NEUTRAL';
  const rule = TF_CLOSE_RULES[tf] || TF_CLOSE_RULES['1W'];

  dirEl.textContent = dir;
  dirEl.className   = 'trade-mgmt-dir ' + dir.toLowerCase();
  tfEl.textContent  = tf;
  if (logBtn) logBtn.style.display = (dir === 'NEUTRAL' || !sig.entry) ? 'none' : '';

  if (dir === 'NEUTRAL' || !sig.entry) {
    body.innerHTML = '<p class="empty">No directional signal — wait for confirmation before entering</p>';
    return;
  }

  const isLong   = dir === 'LONG';
  const entry    = sig.entry;
  const sl       = sig.sl;
  const tps      = sig.tp_targets || [];
  const rr       = sig.rr_ratio;
  const slPct    = sig.sl_pct;
  const tpPcts   = sig.tp_pcts || [];
  const volTier  = sig.vol_tier_label || '';
  const lev      = sig.leverage;

  // Best active flag for this direction
  const matchFlag = (a.flags || []).find(f =>
    f.is_active && f.direction === (isLong ? 'bullish' : 'bearish')
  );

  const p  = fmtPrice;
  const pct = (a, b)    => b ? ((a - b) / b * 100).toFixed(1) + '%' : '';
  const pctHtml = (v, ref, good) => {
    const s = pct(v, ref);
    const cls = good ? 'bull' : 'bear';
    return s ? `<span class="tm-pct ${cls}">${s}</span>` : '';
  };

  // Close-trigger price: whichever hits first (closer to entry wins)
  // For LONG: stop fires when price drops — use the higher of flag_low vs SL
  // For SHORT: stop fires when price rises — use the lower of flag_high vs SL
  const triggerPrice = isLong
    ? (matchFlag ? Math.max(matchFlag.flag_low, sl)  : sl)
    : (matchFlag ? Math.min(matchFlag.flag_high, sl) : sl);

  const flagTarget = matchFlag ? matchFlag.target : null;

  const pctTag = (v, good) => v != null
    ? `<span class="tm-pct ${good ? 'bull' : 'bear'}">${good ? '+' : '-'}${v}%</span>` : '';

  const levelsHTML = `
    <div class="tm-col">
      <div class="tm-section-title">
        Levels
        ${volTier ? `<span class="vol-tier-badge">${volTier}</span>` : ''}
      </div>
      <div class="tm-row">
        <span class="tm-label">Entry</span>
        <span class="tm-val">${p(entry)}</span>
      </div>
      <div class="tm-row">
        <span class="tm-label">Stop Loss</span>
        <span class="tm-val bear">${p(sl)} ${pctTag(slPct, false)}</span>
      </div>
      <div class="tm-divider"></div>
      <div class="tm-row">
        <span class="tm-label">TP 1 <span style="color:var(--muted);font-size:.68rem">— sell 50%</span></span>
        <span class="tm-val bull">${tps[0] != null ? p(tps[0]) : '<span class="muted">N/A</span>'} ${pctTag(tpPcts[0], true)}</span>
      </div>
      <div class="tm-row">
        <span class="tm-label">TP 2 <span style="color:var(--muted);font-size:.68rem">— sell 30%</span></span>
        <span class="tm-val bull">${tps[1] != null ? p(tps[1]) : '<span class="muted">N/A</span>'} ${pctTag(tpPcts[1], true)}</span>
      </div>
      <div class="tm-row">
        <span class="tm-label">TP 3 <span style="color:var(--muted);font-size:.68rem">— sell 20%</span></span>
        <span class="tm-val bull">${tps[2] != null ? p(tps[2]) : '<span class="muted">N/A</span>'} ${pctTag(tpPcts[2], true)}</span>
      </div>
      ${flagTarget ? `
      <div class="tm-row">
        <span class="tm-label">Flag Target</span>
        <span class="tm-val gold">${p(flagTarget)} ${pctHtml(flagTarget, entry, isLong)}</span>
      </div>` : ''}
      ${rr ? `
      <div class="tm-divider"></div>
      <div class="tm-row">
        <span class="tm-label">R / R Ratio</span>
        <span class="tm-val ${rr >= 2 ? 'bull' : rr >= 1.5 ? '' : 'bear'}">${rr} : 1</span>
      </div>` : ''}
      ${lev ? `
      <div class="tm-row">
        <span class="tm-label">Suggested Leverage <span style="color:var(--muted);font-size:.68rem">2% risk</span></span>
        <span class="tm-val ${lev >= 5 ? 'bull' : lev >= 3 ? '' : 'bear'}">${lev}×</span>
      </div>` : ''}
    </div>`;

  const rulesHTML = `
    <div class="tm-col">
      <div class="tm-section-title">Exit Rules</div>
      <div class="tm-rules">
        <div class="tm-rule active">
          <span class="tm-rule-icon">1.</span>
          <span>Hit TP1 → close 50%, ${rule.be1} at <strong>${p(entry)}</strong></span>
        </div>
        <div class="tm-rule active">
          <span class="tm-rule-icon">2.</span>
          <span>Hit TP2 → close 30%, trail remaining SL ${isLong ? 'below each new higher' : 'above each new lower'} <strong>${rule.trail} ${isLong ? 'low' : 'high'}</strong></span>
        </div>
        <div class="tm-rule active">
          <span class="tm-rule-icon">3.</span>
          <span><strong>${rule.candle}</strong> closes ${isLong ? 'below' : 'above'} <strong>${p(triggerPrice)}</strong>${(() => {
              if (!matchFlag) return ' (stop loss)';
              const slIsCloser = isLong ? sl >= matchFlag.flag_low : sl <= matchFlag.flag_high;
              return slIsCloser ? ' (stop loss)' : ' (back inside flag)';
            })()} → full exit</span>
        </div>
        <div class="tm-rule active">
          <span class="tm-rule-icon">4.</span>
          <span>${matchFlag
            ? `Flag ${isLong ? 'breakout' : 'breakdown'} fails after ${matchFlag.consolidation_bars + 3}+ ${tf} bars → re-evaluate, reduce size by 50%`
            : `${rule.sidewaysDesc} with no follow-through → check ${rule.checkTF} chart; if ${rule.checkTF} also sideways or ${isLong ? 'bearish' : 'bullish'} → reduce size by 50% or exit`}</span>
        </div>
        <div class="tm-divider"></div>
        <div class="tm-section-title" style="margin-top:4px">Timing</div>
        <div class="tm-rule active">
          <span class="tm-rule-icon">⏱</span>
          <span>Review position <strong>${rule.check}</strong> — only act on closed ${rule.candle}s</span>
        </div>
        <div class="tm-rule active">
          <span class="tm-rule-icon">📅</span>
          <span>Expected hold: <strong>${rule.hold}</strong></span>
        </div>
        <div class="tm-rule" style="margin-top:6px; font-size:.68rem; color:var(--muted); font-style:italic">
          <span class="tm-rule-icon"></span>
          <span>Never close mid-candle on wicks — wait for the ${rule.candle} to fully close before acting</span>
        </div>
      </div>
    </div>`;

  body.innerHTML = levelsHTML + rulesHTML;
}

/* ─── Flag Patterns ───────────────────────────────────────────────────────── */
// Inline SVG schematic of a flag pattern: pole → descending/ascending channel →
// breakout projection to target, drawn to real price proportions.
function flagSvg(f) {
  const W = 320, H = 130, pad = 14;
  const isBull = f.direction === 'bullish';
  const bars   = Math.max(1, f.consolidation_bars || 6);
  const slope  = (f.slope_pct_per_bar || 0) / 100;
  const ps = f.pole_start_price, pe = f.pole_end_price;
  const fh = f.flag_high, fl = f.flag_low, tg = f.target;
  if ([ps, pe, fh, fl, tg].some(v => v == null || v <= 0)) return '';
  const fhE = fh * (1 + slope * bars);   // channel bounds at flag end
  const flE = fl * (1 + slope * bars);
  const all = [ps, pe, fh, fl, tg, fhE, flE];
  const lo = Math.min(...all), hi = Math.max(...all), rng = (hi - lo) || 1;
  const y = v => pad + (H - 2 * pad) * (1 - (v - lo) / rng);
  const xP0 = pad, xPE = W * 0.30, xFE = W * 0.66, xT = W - pad - 2;
  const col = isBull ? '#22c55e' : '#ef4444';
  const dim = isBull ? 'rgba(34,197,94,.55)' : 'rgba(239,68,68,.55)';
  const brk = isBull ? y(fhE) : y(flE);      // breakout point
  const money = v => '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: v < 1 ? 5 : 2 });
  const confirmed = f.confirmed;
  return `<svg viewBox="0 0 ${W} ${H}" class="flag-svg" preserveAspectRatio="none" role="img" aria-label="flag pattern">
    <polygon points="${xPE},${y(fh)} ${xFE},${y(fhE)} ${xFE},${y(flE)} ${xPE},${y(fl)}" fill="${dim}" opacity=".12"/>
    <line x1="${xPE}" y1="${y(tg)}" x2="${xT}" y2="${y(tg)}" stroke="${col}" stroke-width="1" opacity=".35" stroke-dasharray="1 4"/>
    <line x1="${xP0}" y1="${y(ps)}" x2="${xPE}" y2="${y(pe)}" stroke="${col}" stroke-width="2.6"/>
    <line x1="${xPE}" y1="${y(fh)}" x2="${xFE}" y2="${y(fhE)}" stroke="${dim}" stroke-width="1.6" stroke-dasharray="5 3"/>
    <line x1="${xPE}" y1="${y(fl)}" x2="${xFE}" y2="${y(flE)}" stroke="${dim}" stroke-width="1.6" stroke-dasharray="5 3"/>
    <line x1="${xFE}" y1="${brk}" x2="${xT}" y2="${y(tg)}" stroke="${col}" stroke-width="${confirmed ? 2.4 : 1.8}" ${confirmed ? '' : 'stroke-dasharray="2 3"'}/>
    <circle cx="${xFE}" cy="${brk}" r="3" fill="${col}"/>
    <text x="${xT}" y="${y(tg) + (isBull ? -4 : 11)}" fill="${col}" font-size="10" font-weight="600" text-anchor="end">🎯 ${money(tg)}</text>
    <text x="${xP0}" y="${H - 3}" fill="var(--muted2,#94a3b8)" font-size="8.5">pole ${isBull ? '+' : ''}${f.pole_pct}%</text>
    <text x="${(xPE + xFE) / 2}" y="${H - 3}" fill="var(--muted2,#94a3b8)" font-size="8.5" text-anchor="middle">flag ${bars} bars</text>
  </svg>`;
}

function renderFlags(flags, candles, signal) {
  const el    = document.getElementById('flagList');
  const badge = document.getElementById('flagCount');
  // tear down any charts from the previous render (avoid leaks)
  (S.flagCharts || []).forEach(c => { try { c.remove(); } catch (_) {} });
  S.flagCharts = [];
  const p = (v, d = 4) => Number(v).toLocaleString('en-US', { maximumFractionDigits: d });
  const wantDir = signal?.direction === 'LONG' ? 'bullish'
                : signal?.direction === 'SHORT' ? 'bearish' : null;
  const cts    = (candles || []).map(c => +c.timestamp).filter(Boolean).sort((a, b) => a - b);
  const lastTs = cts.length ? cts[cts.length - 1] : 0;
  const barMs  = cts.length > 1 ? (cts[1] - cts[0]) : 6048e5;
  const ageBars = f => { const e = f.flag_end_ts || f.pole_start_ts || lastTs; return barMs ? (lastTs - e) / barMs : 0; };
  const isFresh = f => f.is_active || ageBars(f) <= (f.consolidation_bars || 6) + 3;

  // Only show LIVE flags — hide resolved / stale patterns that already played out
  // months ago (they were only useful as history and clutter the card otherwise).
  const flagsAll = flags || [];
  flags = flagsAll.filter(isFresh);
  badge.textContent = flags.length;
  if (!flags.length) {
    const hadStale = flagsAll.length > 0;
    el.innerHTML = `<p class="empty">${hadStale ? 'No active flag patterns (older ones already resolved)' : 'No flag patterns detected'}</p>`;
    return;
  }
  // Chart only ONE flag: the direction-aligned one first (so a short shows the
  // bearish flag), then dominant, then strength. All candidates are already live.
  let bestIdx = 0, bestScore = -Infinity;
  flags.forEach((f, i) => {
    let s = (f.dominant ? 1e3 : 0) + (f.strength || 0);
    if (wantDir && f.direction === wantDir) s += 1e4;   // direction alignment
    if (s > bestScore) { bestScore = s; bestIdx = i; }
  });
  const best        = flags[bestIdx];
  const bestAligned = wantDir && best.direction === wantDir;
  // Lifecycle status (from the backend's chronological breakout resolution):
  // confirmed = the breakout candle closed beyond the boundary and is marked on
  // the chart; forming = still consolidating, and it scores ZERO signal points
  // until it breaks — so say what to watch for.
  const bestStatus = best.confirmed
    ? '✅ breakout confirmed'
    : `⏳ forming — awaiting a close ${best.direction === 'bullish' ? 'above' : 'below'} $${p(best.direction === 'bullish' ? best.flag_high : best.flag_low)}`;
  const bestNote = (bestAligned
    ? `aligned with the ${signal.direction} signal`
    : (wantDir ? `active pattern · note: your signal is ${signal.direction}` : 'strongest active pattern'))
    + ` · ${bestStatus}`;
  // When the live flag DISAGREES with the trade signal, show both and explain the
  // conflict rather than hiding one side — the market is genuinely mixed here.
  const conflictNote = (wantDir && !bestAligned && signal)
    ? `<div class="flag-conflict">⚠ <b>Mixed signal.</b> The flag structure is <b>${best.direction}</b> (target $${p(best.target)} ${best.direction === 'bullish' ? 'up' : 'down'}), but the current trade signal is <b>${signal.direction}</b> — usually from lower-timeframe weakness. They disagree, so treat the flag as <b>unconfirmed</b>: wait for a decisive ${best.direction === 'bullish' ? 'breakout <b>above</b> the channel to confirm the flag' : 'breakdown <b>below</b> the channel to confirm the flag'}, or a ${signal.direction === 'LONG' ? 'hold above' : 'close below'} the flag zone to confirm the ${signal.direction}.</div>`
    : '';
  el.innerHTML = flags.map((f, idx) => {
    const cls        = f.direction === 'bullish' ? 'bull' : 'bear';
    const domCls     = f.dominant ? ' dominant' : '';
    const isBull     = f.direction === 'bullish';
    const icon       = isBull ? '▲' : '▼';

    // Flag type label includes slope when present
    const slopeWord  = f.flag_slope === 'ascending'  ? ' Ascending'
                     : f.flag_slope === 'descending' ? ' Descending' : '';
    const flagLabel  = `${isBull ? 'Bullish' : 'Bearish'}${slopeWord} Flag`;

    const activeBadge = f.is_active
      ? '<span class="flag-active">Active</span>' : '';
    const domBadge   = f.dominant
      ? '<span class="flag-active" style="background:rgba(245,158,11,.15);color:var(--gold)">Dominant</span>' : '';
    const confirmBadge = f.confirmed
      ? `<span class="flag-confirmed">${f.breakout_dir === 'up' ? '↑' : '↓'} Confirmed</span>` : '';

    const slopeIcon  = f.flag_slope === 'ascending'  ? '↗'
                     : f.flag_slope === 'descending' ? '↘' : '→';
    const slopeCls   = f.flag_slope === 'ascending'  ? 'bull'
                     : f.flag_slope === 'descending' ? 'bear' : '';
    const slopeStat  = f.flag_slope && f.flag_slope !== 'neutral'
      ? `<span class="flag-stat">Channel <span class="${slopeCls}">${slopeIcon} ${f.flag_slope} (${f.slope_pct_per_bar > 0 ? '+' : ''}${f.slope_pct_per_bar}%/bar)</span></span>` : '';

    return `<div class="flag-item ${cls}${domCls}">
      <div class="flag-top">
        <span class="flag-name ${cls}">${icon} ${flagLabel}</span>
        <span class="flag-tf">${f.timeframe}</span>
        ${activeBadge}${domBadge}${confirmBadge}
      </div>
      <div class="flag-stats">
        <span class="flag-stat">Pole <span>${isBull ? '+' : ''}${f.pole_pct}%</span></span>
        <span class="flag-stat">Retrace <span>${f.retrace_pct}%</span></span>
        <span class="flag-stat">Bars <span>${f.consolidation_bars}</span></span>
        <span class="flag-stat">Strength <span>${f.strength}</span></span>
        ${slopeStat}
      </div>
      <div class="flag-target">Target: <span>$${p(f.target)}</span>
        &nbsp;·&nbsp; Flag zone $${p(f.flag_low)} – $${p(f.flag_high)}
      </div>
      ${idx === bestIdx ? `<div class="flag-chart-cap">📊 ${isBull ? 'Bullish' : 'Bearish'}${slopeWord} Flag · ${bestNote}</div>
      ${conflictNote}
      <div class="flag-chart" id="flagChart_${idx}"></div>` : ''}
    </div>`;
  }).join('');

  // One clear candlestick chart for the best flag only. Overlay the trade's
  // Entry/Stop ONLY when the flag AGREES with the trade direction — a bullish
  // flag (target up) with a short's Stop/Entry (levels down) on the same chart
  // is contradictory, so drop them when they disagree.
  const overlaySignal = (!wantDir || bestAligned) ? signal : null;
  renderFlagCharts([flags[bestIdx]], candles, [bestIdx], overlaySignal);
}

// Series primitive (lightweight-charts v4) that shades the flag chart: a
// translucent fill for the channel band (the quad between the two rails) and
// translucent horizontal risk/reward zones (Entry→Target green, Entry→Stop red).
// Drawn on top with low alpha so it tints the candles without hiding them.
class FlagShade {
  constructor(chart, series, data) { this._chart = chart; this._series = series; this._data = data; }
  updateAllViews() {}
  paneViews() {
    const self = this;
    return [{
      zOrder: () => 'top',
      renderer: () => ({
        draw: (target) => {
          target.useMediaCoordinateSpace((scope) => {
            const ctx = scope.context;
            const W   = scope.mediaSize.width;
            const px  = p => self._series.priceToCoordinate(p);
            const tx  = t => self._chart.timeScale().timeToCoordinate(t);
            // full-width horizontal zones (risk / reward)
            (self._data.zones || []).forEach(z => {
              const y1 = px(z.p1), y2 = px(z.p2);
              if (y1 == null || y2 == null) return;
              ctx.fillStyle = z.color;
              ctx.fillRect(0, Math.min(y1, y2), W, Math.abs(y2 - y1));
            });
            // channel band: quad between the up-rail and lo-rail endpoints
            const b = self._data.band;
            if (b) {
              const pts = [b.up[0], b.up[1], b.lo[1], b.lo[0]]
                .map(p => ({ x: tx(p.time), y: px(p.value) }));
              if (pts.every(p => p.x != null && p.y != null)) {
                ctx.beginPath();
                ctx.moveTo(pts[0].x, pts[0].y);
                for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
                ctx.closePath();
                ctx.fillStyle = b.color;
                ctx.fill();
              }
            }
          });
        },
      }),
    }];
  }
}

// One clear candlestick chart for the best flag: the token's actual candles over
// the pole→flag window, with the pole line, the flag channel, and labelled price
// lines for the target and the flag zone. `idxList[k]` is the DOM container index
// for `flagList[k]`.
function renderFlagCharts(flagList, candles, idxList, signal) {
  if (!candles?.length || !window.LightweightCharts) {
    flagList.forEach((f, k) => {
      const el = document.getElementById(`flagChart_${idxList[k]}`);
      if (el) el.innerHTML = flagSvg(f);
    });
    return;
  }
  const rows = candles
    .map(c => ({ time: Math.floor(c.timestamp / 1000), open: +c.open, high: +c.high, low: +c.low, close: +c.close }))
    .sort((a, b) => a.time - b.time)
    .filter((v, i, a) => i === 0 || v.time > a[i - 1].time);
  const interval = rows.length > 1 ? (rows[1].time - rows[0].time) : 604800;
  const money = v => '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: v < 1 ? 5 : 2 });

  flagList.forEach((f, k) => {
    const el = document.getElementById(`flagChart_${idxList[k]}`);
    if (!el) return;
    const isBull = f.direction === 'bullish';
    const col    = isBull ? '#22c55e' : '#ef4444';
    let   bandData = null;            // channel-band geometry for the FlagShade primitive
    const zones    = [];             // risk/reward horizontal shading
    const bars   = Math.max(1, f.consolidation_bars || 6);
    const poleStart = Math.floor((f.pole_start_ts || 0) / 1000);
    const flagEnd   = Math.floor((f.flag_end_ts || 0) / 1000);
    const flagStart = flagEnd - bars * interval;

    // Zoom TIGHTLY to the pattern: a bar before the pole through a few bars past
    // the flag/breakout — never the whole history (which buries a stale pattern
    // under months of unrelated candles). projBars leaves room for the breakout.
    const lastRowT = rows[rows.length - 1].time;
    const projBars = Math.max(3, Math.round((f.consolidation_bars || 6) * 0.6));
    const startT = (poleStart || rows[0].time) - 1 * interval;
    const endT   = Math.min(lastRowT, (flagEnd || lastRowT) + projBars * interval);
    const win = rows.filter(c => c.time >= startT && c.time <= endT);
    if (win.length < 3) { el.innerHTML = flagSvg(f); return; }
    // Entry/Stop belong to the CURRENT candle — only overlay them if this window
    // actually reaches the present, else they'd float disconnected from an old flag.
    const showsCurrent = endT >= lastRowT - interval;

    const chart = LightweightCharts.createChart(el, {
      ...CHART_OPTS,
      layout: { ...CHART_OPTS.layout, fontSize: 11 },
      width: el.clientWidth || 320,
      height: 440,
      // Let the user pan/zoom this chart (drag to move, pinch/wheel to zoom,
      // drag the price axis to scale vertically). It still opens fitted to the
      // pattern via fitContent() below.
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
      handleScale:  { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      rightPriceScale: { ...CHART_OPTS.rightPriceScale, entireTextOnly: true, scaleMargins: { top: 0.12, bottom: 0.12 } },
    });
    const cs = chart.addCandlestickSeries({
      upColor: '#10b981', downColor: '#ef4444', borderUpColor: '#10b981',
      borderDownColor: '#ef4444', wickUpColor: '#10b981', wickDownColor: '#ef4444',
      priceFormat: { type: 'price', precision: f.target < 1 ? 5 : 2, minMove: f.target < 1 ? 0.00001 : 0.01 },
    });
    cs.setData(win);

    const ov = { priceLineVisible: false, lastValueVisible: false,
                 crosshairMarkerVisible: false, autoscaleInfoProvider: () => null };

    // Markers accumulate here — setMarkers() REPLACES the whole set, so pole and
    // breakout markers must go through one call (sorted by time) at the end.
    const markers = [];

    // pole (solid, thick) with a start marker
    if (poleStart && f.pole_start_price && f.pole_end_price) {
      chart.addLineSeries({ ...ov, color: col, lineWidth: 3 })
        .setData([{ time: poleStart, value: +f.pole_start_price },
                  { time: Math.max(poleStart + interval, flagStart), value: +f.pole_end_price }]);
      markers.push({ time: poleStart,
        position: isBull ? 'belowBar' : 'aboveBar',
        color: col, shape: isBull ? 'arrowUp' : 'arrowDown', text: 'pole' });
    }

    // Breakout marker — the backend now reports the exact candle whose close
    // broke the flag boundary (breakout_ts). Flag it on the chart so the eye
    // lands on WHERE the pattern triggered, not just the projection line.
    const breakoutT = f.breakout_ts ? Math.floor(f.breakout_ts / 1000) : null;
    if (f.confirmed && breakoutT && win.some(c => c.time === breakoutT)) {
      const up = f.breakout_dir === 'up';
      markers.push({ time: breakoutT,
        position: up ? 'belowBar' : 'aboveBar',
        color: up ? '#10b981' : '#ef4444',
        shape: up ? 'arrowUp' : 'arrowDown', text: 'breakout' });
    }

    // Flag channel FITTED to the actual consolidation candles (least-squares on
    // the highs for the upper line, the lows for the lower line) so it hugs price
    // instead of the flag's absolute extremes diverging into empty space.
    const flagC = win.filter(c => c.time >= flagStart && c.time <= flagEnd);
    const fit = (pts) => {           // pts: [{x, y}] → returns x => y
      const n = pts.length; if (!n) return () => 0;
      let sx = 0, sy = 0, sxx = 0, sxy = 0;
      pts.forEach(p => { sx += p.x; sy += p.y; sxx += p.x * p.x; sxy += p.x * p.y; });
      const den = n * sxx - sx * sx;
      const m = den ? (n * sxy - sx * sy) / den : 0;
      const b = (sy - m * sx) / n;
      return x => m * x + b;
    };
    // Build a PARALLEL regression channel that ENVELOPES the candles: one shared
    // slope (fit through the bar midpoints = the channel's drift), then offset the
    // two rails so the upper touches the highest high and the lower touches the
    // lowest low. A plain best-fit line through highs/lows runs through the middle
    // of the points, leaving ~half the candles poking out on each side — this
    // instead bounds every candle inside the channel.
    let upLine = null, loLine = null;
    if (flagC.length >= 2) {
      const midFit = fit(flagC.map((c, i) => ({ x: i, y: (c.high + c.low) / 2 })));
      const slope  = midFit(1) - midFit(0);
      let upB = -Infinity, loB = Infinity;
      flagC.forEach((c, i) => {
        upB = Math.max(upB, c.high - slope * i);
        loB = Math.min(loB, c.low  - slope * i);
      });
      upLine = i => slope * i + upB;
      loLine = i => slope * i + loB;
      const n = flagC.length - 1;
      // Extend both rails forward along their slope to the current candle so the
      // channel reaches the live price on the right instead of stopping at the
      // end of the consolidation.
      const lastT     = win[win.length - 1].time;
      const extraBars = interval ? Math.max(0, Math.round((lastT - flagC[n].time) / interval)) : 0;
      const endIdx    = n + extraBars;
      // Textbook flag channel: two STRICTLY PARALLEL straight rails (one shared
      // slope), each an envelope over the consolidation highs/lows, extended to
      // the current candle. They are NOT clamped to the flag zone — a parallel
      // sloped channel anchored to the swing highs/lows will naturally sit a hair
      // outside the flat flag_high/flag_low lines at the ends, which is correct.
      // The dotted flag_high/flag_low lines remain the actual break levels.
      const rail = (line) => [
        { time: flagC[0].time, value: line(0) },
        { time: lastT,         value: line(endIdx) },
      ];
      const upRail = rail(upLine), loRail = rail(loLine);
      chart.addLineSeries({ ...ov, color: col, lineWidth: 2, lineStyle: 2 }).setData(upRail);
      chart.addLineSeries({ ...ov, color: col, lineWidth: 2, lineStyle: 2 }).setData(loRail);
      bandData = { up: upRail, lo: loRail,
        color: isBull ? 'rgba(34,197,94,.09)' : 'rgba(239,68,68,.09)' };
    }

    // Flag ZONE boundaries — the actual highest high / lowest low the flag traded
    // to (flat horizontal levels). These, NOT the sloping channel rails, are what
    // the pattern's validity is judged against: for a bullish flag a decisive
    // close below flag_low (≈ its value × 0.97) invalidates it. Drawn as muted
    // dotted lines so it's clear where the real break level sits vs. the projected
    // channel, which extrapolates past the price that was actually reached.
    if (f.flag_low)  cs.createPriceLine({ price: +f.flag_low,  color: 'rgba(148,163,184,.55)',
      lineWidth: 1, lineStyle: 3, axisLabelVisible: true, title: 'Flag low' });
    if (f.flag_high) cs.createPriceLine({ price: +f.flag_high, color: 'rgba(148,163,184,.55)',
      lineWidth: 1, lineStyle: 3, axisLabelVisible: true, title: 'Flag high' });

    // target — labelled horizontal line. A createPriceLine alone does NOT stretch
    // the price scale here, so a far-away measured-move target (e.g. a 1W pole
    // projecting ~40% below price) falls off the bottom. Add an invisible anchor
    // series AT the target so the y-range always expands to include it.
    cs.createPriceLine({ price: +f.target, color: col, lineWidth: 2, lineStyle: 0,
      axisLabelVisible: true, title: '🎯 Target' });
    chart.addLineSeries({ color: 'rgba(0,0,0,0)', lineWidth: 1, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false })
      .setData([{ time: win[0].time, value: +f.target },
                { time: win[win.length - 1].time, value: +f.target }]);

    // Breakout projection: a dashed arrow-line from the flag's breakout edge to
    // the target, drawn only once the flag is CONFIRMED (an idealised path, not a
    // claim price will go there). Extends a few bars past the last candle.
    if (f.confirmed && flagC.length >= 2) {
      const up = f.breakout_dir === 'up';
      const n  = flagC.length - 1;
      // Start the breakout from the flag boundary it broke through (flag_high for
      // an up-break, flag_low for a down-break) — that's exactly where the clipped
      // rail ends, so the arrow leaves the same point the eye sees on the chart.
      const edge = up
        ? (f.flag_high != null ? +f.flag_high : (upLine ? upLine(n) : 0))
        : (f.flag_low  != null ? +f.flag_low  : (loLine ? loLine(n) : 0));
      // Project into empty space to the RIGHT of the last candle so the descent
      // has room to actually reach the target level instead of clipping the edge.
      const tgtT = flagC[n].time + Math.max(projBars, 3) * interval;
      chart.addLineSeries({ ...ov, color: col, lineWidth: 2, lineStyle: 2 })
        .setData([{ time: flagC[n].time, value: edge },
                  { time: tgtT, value: +f.target }]);
    }

    // Trade levels from the actual signal — Entry (gold) + Stop (red), labelled.
    // Overlaid so the flag chart doubles as the trade plan. Drive autoscale so
    // both are always visible.
    if (showsCurrent && signal && signal.entry) {
      cs.createPriceLine({ price: +signal.entry, color: '#eab308', lineWidth: 1, lineStyle: 2,
        axisLabelVisible: true, title: 'Entry' });
      if (signal.sl) {
        cs.createPriceLine({ price: +signal.sl, color: '#ef4444', lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: 'Stop' });
        zones.push({ p1: +signal.entry, p2: +signal.sl, color: 'rgba(239,68,68,.08)' });  // risk
      }
      if (f.target != null) {
        zones.push({ p1: +signal.entry, p2: +f.target, color: 'rgba(34,197,94,.08)' });   // reward
      }
    }

    // Shade the channel band + risk/reward zones over the candles.
    if (bandData || zones.length) {
      try { cs.attachPrimitive(new FlagShade(chart, cs, { band: bandData, zones })); }
      catch (_) { /* older lib without primitives — lines/zones just won't shade */ }
    }

    if (markers.length) cs.setMarkers(markers.sort((a, b) => a.time - b.time));

    chart.timeScale().fitContent();
    S.flagCharts.push(chart);
  });
}

/* ─── My Trades ───────────────────────────────────────────────────────────── */
const TRADES_KEY = 'cryptobadshah_trades';

function getTrades() {
  try { return JSON.parse(localStorage.getItem(TRADES_KEY) || '[]'); }
  catch { return []; }
}
function saveTrades(trades) {
  localStorage.setItem(TRADES_KEY, JSON.stringify(trades));
}

function logTrade() {
  const a = S.analysis;
  if (!a?.signal || a.signal.direction === 'NEUTRAL' || !a.signal.entry) return;
  const sig    = a.signal;
  const tf     = a.timeframe;
  const isLong = sig.direction === 'LONG';
  const rule   = TF_CLOSE_RULES[tf] || TF_CLOSE_RULES['1W'];
  const fp     = fmtPrice;

  // Active flag matching the signal direction — same logic as renderTradeManagement
  const matchFlag = (a.flags || []).find(f =>
    f.is_active && f.direction === (isLong ? 'bullish' : 'bearish')
  );
  const triggerPrice = isLong
    ? (matchFlag ? Math.max(matchFlag.flag_low, sig.sl)  : sig.sl)
    : (matchFlag ? Math.min(matchFlag.flag_high, sig.sl) : sig.sl);

  const exit_rules = {
    rule1: `Hit TP1 → close 50%, ${rule.be1} at ${fp(sig.entry)}`,
    rule2: `Hit TP2 → close 30%, trail remaining SL ${isLong ? 'below each new higher' : 'above each new lower'} ${rule.trail} ${isLong ? 'low' : 'high'}`,
    rule3: `${rule.candle} closes ${isLong ? 'below' : 'above'} ${fp(triggerPrice)}${(() => { if (!matchFlag) return ' (stop loss)'; const slIsCloser = isLong ? sig.sl >= matchFlag.flag_low : sig.sl <= matchFlag.flag_high; return slIsCloser ? ' (stop loss)' : ' (back inside flag)'; })()} → full exit`,
    rule4: matchFlag
      ? `Flag ${isLong ? 'breakout' : 'breakdown'} fails after ${matchFlag.consolidation_bars + 3}+ ${tf} bars → re-evaluate, reduce size by 50%`
      : `${rule.sidewaysDesc} with no follow-through → check ${rule.checkTF} chart; if ${rule.checkTF} also sideways or ${isLong ? 'bearish' : 'bullish'} → reduce size by 50% or exit`,
    timing: `Review position ${rule.check} — only act on closed ${rule.candle}s`,
    hold:   `Expected hold: ${rule.hold}`,
    reminder: `Never close mid-candle on wicks — wait for the ${rule.candle} to fully close`,
  };

  const trade = {
    id: `${a.symbol}-${a.timeframe}-${Date.now()}`,
    timestamp: Date.now(),
    symbol: a.symbol,
    timeframe: a.timeframe,
    direction: sig.direction,
    entry:  sig.entry,
    sl:     sig.sl,
    tp1:    sig.tp_targets?.[0] ?? null,
    tp2:    sig.tp_targets?.[1] ?? null,
    tp3:    sig.tp_targets?.[2] ?? null,
    rr:     sig.rr_ratio,
    strength:     sig.strength,
    bull_reasons: sig.bullish_reasons || [],
    bear_reasons: sig.bearish_reasons || [],
    exit_rules,
    status:         'open',
    exit_price:     null,
    exit_timestamp: null,
  };
  const trades = getTrades();
  trades.unshift(trade);
  saveTrades(trades);
  renderMyTrades();
  const btn = document.getElementById('logTradeBtn');
  if (btn) { btn.textContent = '✓ Logged!'; btn.disabled = true;
    setTimeout(() => { btn.textContent = '📌 Log Trade'; btn.disabled = false; }, 2000); }
}

function deleteTrade(id) {
  if (!confirm('Delete this trade log?')) return;
  saveTrades(getTrades().filter(t => t.id !== id));
  renderMyTrades();
}

function clearClosedTrades() {
  saveTrades(getTrades().filter(t => t.status === 'open'));
  renderMyTrades();
}

function showCloseForm(id) {
  const f = document.getElementById('cf-' + id);
  if (f) f.style.display = f.style.display === 'none' ? 'flex' : 'none';
}

function _doClose(id, inputId) {
  const input = document.getElementById(inputId);
  const price = parseFloat(input?.value);
  if (!price || price <= 0) { if (input) input.style.outline = '1px solid var(--bear)'; return; }
  const trades = getTrades();
  const t = trades.find(t => t.id === id);
  if (!t) return;
  t.status = 'closed';
  t.exit_price = price;
  t.exit_timestamp = Date.now();
  saveTrades(trades);
  renderMyTrades();
  // Re-render trade management so it switches back to fresh signal view
  if (S.analysis) renderTradeManagement(S.analysis);
}

function confirmClose(id)   { _doClose(id, 'cp-'    + id); }
function confirmCloseTM(id) {
  _doClose(id, 'cp-tm-' + id);
  // Toggle the TM close form visibility after closing
  const f = document.getElementById('cf-tm-' + id);
  if (f) f.style.display = 'none';
}

function renderMyTrades() {
  const el    = document.getElementById('tradesList');
  const badge = document.getElementById('tradesCount');
  if (!el) return;
  const trades = getTrades();
  badge.textContent = trades.length;
  if (!trades.length) {
    el.innerHTML = '<p class="empty">No trades logged yet — load a signal and click 📌 Log Trade in the Trade Management card</p>';
    return;
  }

  // Current price for the loaded symbol (for live P&L)
  const curPrices = {};
  if (S.analysis?.candles?.length) {
    const c = S.analysis.candles;
    curPrices[S.analysis.symbol] = c[c.length - 1].close;
  }

  const fmt  = fmtPrice;
  const fmtPct = (v, ref, isLong) => {
    if (v == null || ref == null) return '';
    const pct = isLong ? (v - ref) / ref * 100 : (ref - v) / ref * 100;
    const cls = pct >= 0 ? 'bull' : 'bear';
    return `<span class="${cls}" style="font-size:.7rem">${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%</span>`;
  };

  el.innerHTML = trades.map(t => {
    const isLong  = t.direction === 'LONG';
    const dCls    = isLong ? 'bull' : 'bear';
    const cur     = curPrices[t.symbol];
    const days    = Math.floor((Date.now() - t.timestamp) / 86400000);
    const dated   = new Date(t.timestamp).toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' });

    // Live P&L
    let pnlHtml = '';
    if (t.status === 'open' && cur) {
      const pnl = isLong ? (cur - t.entry) / t.entry * 100 : (t.entry - cur) / t.entry * 100;
      const pc  = pnl >= 0 ? 'bull' : 'bear';
      pnlHtml = `<span class="trade-pnl ${pc}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}% live</span>`;
    } else if (t.status === 'closed' && t.exit_price) {
      const pnl = isLong ? (t.exit_price - t.entry) / t.entry * 100 : (t.entry - t.exit_price) / t.entry * 100;
      const pc  = pnl >= 0 ? 'bull' : 'bear';
      pnlHtml = `<span class="trade-pnl ${pc}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}% (exit ${fmt(t.exit_price)})</span>`;
    } else if (t.status === 'open') {
      pnlHtml = `<span class="trade-pnl muted">Load ${t.symbol} to see P&L</span>`;
    }

    // TP progress
    const tpRows = [
      { label:'Entry', val:t.entry,  cls:'' },
      { label:'SL',    val:t.sl,     cls:'bear' },
      { label:'TP1',   val:t.tp1,    cls:'bull' },
      { label:'TP2',   val:t.tp2,    cls:'bull' },
      { label:'TP3',   val:t.tp3,    cls:'bull' },
    ].map(r => `<div class="tl-item">
        <span class="tl-label">${r.label}</span>
        <span class="tl-val ${r.cls}">${fmt(r.val)} ${r.cls ? fmtPct(r.val, t.entry, isLong) : ''}</span>
      </div>`).join('');

    // Original signal reasons
    const reasons = [
      ...(t.bull_reasons || []).map(r => `<li class="bull">▲ ${r}</li>`),
      ...(t.bear_reasons || []).map(r => `<li class="bear">▼ ${r}</li>`),
    ].join('');

    return `<div class="trade-card ${dCls}" id="trade-${t.id}">
      <div class="tc-header">
        <div class="tc-title">
          <span class="tc-symbol">${t.symbol}</span>
          <span class="tc-tf">${t.timeframe}</span>
          <span class="tc-dir ${dCls}">${t.direction}</span>
          <span class="tc-status ${t.status}">${t.status}</span>
          <span class="tc-strength">Signal ${t.strength}/100</span>
        </div>
        <div class="tc-actions">
          ${t.status === 'open' ? `<button class="btn-tc btn-tc-close" onclick="showCloseForm('${t.id}')">Close Trade</button>` : ''}
          <button class="btn-tc btn-tc-del" onclick="deleteTrade('${t.id}')">✕</button>
        </div>
      </div>
      <div class="tc-meta">
        <span>📅 ${dated}</span>
        <span>⏱ ${days} day${days !== 1 ? 's' : ''} ${t.status === 'open' ? 'open' : 'held'}</span>
        ${pnlHtml}
      </div>
      <div class="tc-levels">${tpRows}</div>
      ${t.rr ? `<div class="tc-rr">R/R at entry: <strong>${t.rr}:1</strong></div>` : ''}
      ${reasons ? `<details class="tc-context"><summary>Signal Context</summary><ul class="tc-reasons">${reasons}</ul></details>` : ''}
      ${t.exit_rules ? `<details class="tc-context">
        <summary>Exit Rules (at entry)</summary>
        <ol class="tc-exit-rules">
          <li>${t.exit_rules.rule1}</li>
          <li>${t.exit_rules.rule2}</li>
          <li>${t.exit_rules.rule3}</li>
          <li>${t.exit_rules.rule4}</li>
        </ol>
        <div class="tc-exit-timing">
          <span>⏱ ${t.exit_rules.timing}</span>
          <span>📅 ${t.exit_rules.hold}</span>
          <em>${t.exit_rules.reminder}</em>
        </div>
      </details>` : ''}
      <div id="cf-${t.id}" class="tc-close-form" style="display:none">
        <input id="cp-${t.id}" class="tc-price-input" type="number" placeholder="Exit price" step="any" />
        <button class="btn-tc btn-tc-confirm" onclick="confirmClose('${t.id}')">Confirm Close</button>
        <button class="btn-tc" onclick="showCloseForm('${t.id}')">Cancel</button>
      </div>
    </div>`;
  }).join('');
}

/* ─── Engulfing Patterns ──────────────────────────────────────────────────── */
function renderEngulfing(patterns, timeframe) {
  const section = document.getElementById('engulfingSection');
  const el      = document.getElementById('engulfList');
  const badge   = document.getElementById('engulfCount');
  section.style.display = '';

  if (!patterns?.length) {
    el.innerHTML = '<p class="empty">No confirmed engulfing patterns in the last 4 candles</p>';
    badge.textContent = '0';
    return;
  }

  badge.textContent = patterns.length;
  const fmt = fmtPrice;

  el.innerHTML = patterns.map(p => {
    const isBull  = p.direction === 'bullish';
    const cls     = isBull ? 'bull' : 'bear';
    const icon    = isBull ? '▲' : '▼';
    const label   = isBull ? 'Bullish Engulfing' : 'Bearish Engulfing';
    const agoText = p.candles_ago === 1 ? 'Most recent candle' : `${p.candles_ago} candles ago`;
    const fresh   = p.candles_ago <= 1;
    return `<div class="engulf-item ${cls}${fresh ? ' engulf-fresh' : ''}">
      <div class="engulf-top">
        <span class="engulf-name ${cls}">${icon} ${label}</span>
        <span class="engulf-badge">✓ Confirmed</span>
        ${fresh ? '<span class="engulf-badge engulf-new">Latest</span>' : ''}
      </div>
      <div class="engulf-stats">
        <span class="engulf-stat">Prev candle <span>${fmtPrice(p.prev_open)} → ${fmtPrice(p.prev_close)}</span></span>
        <span class="engulf-stat">Engulf candle <span>${fmtPrice(p.engulf_open)} → ${fmtPrice(p.engulf_close)}</span></span>
        <span class="engulf-stat">Body ratio <span>${p.body_ratio}×</span></span>
        <span class="engulf-stat">When <span>${agoText}</span></span>
      </div>
      <div class="engulf-note ${cls}">
        ${isBull
          ? `Bearish candle fully engulfed — potential reversal to the upside`
          : `Bullish candle fully engulfed — potential reversal to the downside`}
      </div>
    </div>`;
  }).join('');
}

/* ─── Elliott Wave ────────────────────────────────────────────────────────── */
function renderElliottWave(e) {
  if (!e) return;
  document.getElementById('waveLabel').textContent = e.wave_count || '—';
  document.getElementById('waveDesc').textContent  = e.description || '';

  const tEl  = document.getElementById('waveTargets');
  const bias = e.bias || 'neutral';
  const targets = e.targets || [];
  if (!targets.length) {
    tEl.innerHTML = `<span style="color:var(--muted);font-size:.82rem">No targets ahead of current price</span>`;
  } else {
    tEl.innerHTML = targets.map((t, i) =>
      `<div class="wave-target ${bias}">T${i + 1}: $${Number(t).toLocaleString('en-US', { maximumFractionDigits: 4 })}</div>`
    ).join('');
  }

  // ── Wave markers on the main candlestick chart ──────────────────────────
  if (!S.candleSeries || !e.pivots?.length) return;

  // Wave label sequence: 1 2 3 4 5 A B C cycling every 8 swings.
  // The last pivot corresponds to the current wave position (e.current_wave).
  const WAVE_NAMES = ['1','2','3','4','5','A','B','C'];
  const pivots   = e.pivots;
  const n        = pivots.length;
  const curIdx   = ((e.current_wave || 1) - 1 + 8) % 8;  // 0-based index in WAVE_NAMES

  const markers = pivots.map((p, i) => {
    // Walk backwards from the current wave label for older pivots
    const labelIdx = ((curIdx - (n - 1 - i)) % 8 + 8) % 8;
    const label    = WAVE_NAMES[labelIdx];
    const isHigh   = p.type === 'H';

    // Impulse waves (1,3,5,B) get gold; corrective (2,4,A,C) get muted purple
    const impulse  = ['1','3','5','B'].includes(label);
    const color    = isHigh
      ? (impulse ? '#ef4444' : '#f59e0b')
      : (impulse ? '#10b981' : '#6366f1');

    return {
      time:     Math.floor(p.time / 1000),
      position: isHigh ? 'aboveBar' : 'belowBar',
      color,
      shape:    isHigh ? 'arrowDown' : 'arrowUp',
      text:     label,
      size:     1,
    };
  }).sort((a, b) => a.time - b.time);

  // Deduplicate by time (LightweightCharts requires unique timestamps per series)
  const unique = [...new Map(markers.map(m => [m.time, m])).values()];
  S.candleSeries.setMarkers(unique);
}

/* ─── HTF Confluence card ─────────────────────────────────────────────────── */
function renderHtfConfluence(a) {
  const section = document.getElementById('htfSection');
  const card    = document.getElementById('htfConfluence');
  if (!section || !card) return;

  const htf = a.htf_confluence;
  if (!htf || !htf.deps || Object.keys(htf.deps).length === 0) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';

  const icon = d => d === 'LONG' ? '▲' : d === 'SHORT' ? '▼' : '—';
  const mainDir = htf.main_dir;

  const items = Object.entries(htf.deps).map(([tf, dir]) => {
    const cls = dir === mainDir ? 'htf-aligned'
              : dir === 'NEUTRAL' ? 'htf-neutral'
              : 'htf-against';
    return `<span class="htf-item ${cls}">${tf} ${icon(dir)}</span>`;
  }).join('');

  const alignedCount = htf.aligned.length;
  const totalDeps    = Object.keys(htf.deps).length;

  let badge = '';
  if (htf.confirmed) {
    badge = `<span class="htf-badge htf-badge-confirm">✓ HTF Confirmed (${alignedCount}/${totalDeps} aligned)</span>`;
  } else if (htf.warning) {
    badge = `<span class="htf-badge htf-badge-warn">⚠ Counter-trend on ${htf.against.join(', ')} — possible reversal or fakeout</span>`;
  } else {
    badge = `<span class="htf-badge htf-badge-neutral">${alignedCount}/${totalDeps} HTFs aligned</span>`;
  }

  card.innerHTML = `
    <div class="htf-header">
      <span class="card-title" style="margin:0">Higher Timeframe Confluence</span>
      <span class="htf-main-dir htf-${mainDir.toLowerCase()}">${icon(mainDir)} ${mainDir} on ${a.timeframe}</span>
    </div>
    <div class="htf-items">${items}</div>
    <div class="htf-footer">${badge}</div>`;
}

/* ─── BTC Market Context banner ──────────────────────────────────────────── */
function renderBtcContext(a) {
  const el = document.getElementById('btcContextBanner');
  if (!el) return;
  const ctx = a.btc_context;
  if (!ctx || ctx.direction === 'NEUTRAL' || a.symbol === 'BTC') {
    el.style.display = 'none';
    return;
  }
  el.style.display = '';
  const icon   = ctx.direction === 'LONG' ? '▲' : '▼';
  const corPct = Math.round((ctx.corr_factor || 1) * 100);
  if (ctx.aligned) {
    el.className = 'btc-ctx-banner btc-ctx-aligned';
    el.innerHTML = `<strong>✓ BTC ${icon} ${ctx.direction}</strong> — aligns with this signal · ${corPct}% BTC-correlated`;
  } else {
    el.className = 'btc-ctx-banner btc-ctx-conflict';
    el.innerHTML = `<strong>⚠ BTC ${icon} ${ctx.direction}</strong> — opposes this signal · ${corPct}% BTC-correlated · watch for reversal / fakeout`;
  }
}

/* ─── Confluence lists ────────────────────────────────────────────────────── */
function renderConfluence(s) {
  if (!s) return;
  const bullEl  = document.getElementById('bullList');
  const bearEl  = document.getElementById('bearList');
  const labelEl = document.getElementById('confluenceTfLabel');

  // Show which TF this confluence is computed from
  if (labelEl) labelEl.textContent = `· ${S.symbol} ${S.timeframe}`;

  // Split intraday-relevant reasons from daily+ cycle context (tagged 🗓️ by
  // the backend on low timeframes). Show live signals first, then the cycle
  // context dimmed under its own subheader so 1H/2H reads aren't cluttered by
  // multi-week/month signals that can't move an intraday candle.
  const li = (txt) => `<li>${txt}</li>`;
  const isHtf = (r) => r.includes('🗓️') || r.includes('daily+ context');
  const build = (reasons, emptyMsg) => {
    const list = reasons?.length ? reasons : [emptyMsg];
    const live = list.filter(r => !isHtf(r));
    const htf  = list.filter(isHtf);
    let html = (live.length ? live : [emptyMsg]).map(li).join('');
    if (htf.length) {
      html += `<li class="conf-htf-head">🗓️ Higher-timeframe context (daily+ · down-weighted on ${S.timeframe})</li>`;
      html += htf.map(r => `<li class="conf-htf">${r}</li>`).join('');
    }
    return html;
  };
  bullEl.innerHTML = build(s.bullish_reasons, 'No bullish confluence');
  bearEl.innerHTML = build(s.bearish_reasons, 'No bearish confluence');
}

/* ─── Copy signal as a blog/post write-up ─────────────────────────────────────
 * Turns the terse confluence factors into clean, full-sentence prose the user
 * can paste straight into a blog or post. Cosmetic-only: strips the dashboard's
 * arrows/emoji/abbreviations and adds authored framing — it never changes the
 * underlying signal.                                                            */
function _cleanFactor(t) {
  let s = String(t || '').trim();
  // strip decorative emoji/symbols anywhere in the line
  s = s.replace(/[⚡🗓️▲▼⚖️🔄🌡️⏳✅🟢🔴📋💤🎯]/g, ' ');
  // drop a leading symbol / bullet
  s = s.replace(/^[^A-Za-z0-9$(]+/, '').trim();
  // numeric arrow (e.g. +49→+48) reads as "to"; causal arrows as a connector
  s = s.replace(/([+\-]?\d[\d,.]*)\s*→\s*([+\-]?\d)/g, '$1 to $2');
  s = s.replace(/\s*→\s*/g, ', which points to ');
  // em/en dash used as "subject — explanation" → a colon on the first, comma after
  s = s.replace(/\s+[—–]\s+/, ': ').replace(/\s+[—–]\s+/g, ', ');
  // expand the common shorthand
  const abbr = [
    [/\bFVGs?\b/g, 'fair-value gap'], [/\bHTF\b/g, 'higher-timeframe'],
    [/\bliq\.\s*/gi, 'liquidity '], [/\bL\/S\b/g, 'long/short'],
    [/\bOI\b/g, 'open interest'], [/\bavg\b/gi, 'average'],
    [/\bpct\b/gi, 'percent'], [/\bmkt\b/gi, 'market'],
  ];
  abbr.forEach(([re, rep]) => { s = s.replace(re, rep); });
  s = s.replace(/\s{2,}/g, ' ').trim();
  if (s) s = s.charAt(0).toUpperCase() + s.slice(1);
  if (s && !/[.!?]$/.test(s)) s += '.';
  return s;
}

function buildSignalPost() {
  const a = S.analysis;
  const s = a && a.signal;
  if (!s) return '';
  const sym = (a.symbol || S.symbol || '').toUpperCase();
  const tf  = a.timeframe || S.timeframe || '';
  const dir = (s.direction || 'NEUTRAL').toUpperCase();
  const dirWord = dir === 'LONG' ? 'Bullish' : dir === 'SHORT' ? 'Bearish' : 'Neutral';
  const strength = s.strength != null ? `${s.strength}/100` : '—';
  const tier = s.tier ? ` (${s.tier})` : '';

  const bull = (s.bullish_reasons || []).map(_cleanFactor).filter(Boolean);
  const bear = (s.bearish_reasons || []).map(_cleanFactor).filter(Boolean);

  const L = [];
  L.push(`${sym}/USDT — ${dirWord} setup (${tf})`);
  L.push(`Signal strength: ${strength}${tier}.`);
  L.push('');

  if (dir === 'NEUTRAL') {
    L.push('The read is currently neutral — supporting and opposing factors roughly balance, so there is no high-conviction trade here yet.');
    L.push('');
  }

  if (bull.length) {
    L.push(dir === 'SHORT' ? 'Counter-signals (bullish):' : 'What is supporting the move:');
    bull.forEach(r => L.push(`• ${r}`));
    L.push('');
  }
  if (bear.length) {
    L.push(dir === 'SHORT' ? 'What is driving the move lower:' : 'Risks and counter-signals:');
    bear.forEach(r => L.push(`• ${r}`));
    L.push('');
  }

  // Trade plan
  const money = v => v != null ? `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 8 })}` : '—';
  if (dir !== 'NEUTRAL' && (s.entry != null || (s.tp_targets || []).length)) {
    L.push('Trade plan:');
    if (s.entry != null) L.push(`• Entry: ${money(s.entry)}`);
    if (s.sl != null)    L.push(`• Stop loss: ${money(s.sl)}${s.sl_pct != null ? ` (${s.sl_pct > 0 ? '' : ''}${s.sl_pct}% risk)` : ''}`);
    (s.tp_targets || []).forEach((tp, i) => L.push(`• Target ${i + 1}: ${money(tp)}`));
    if (s.rr_ratio != null)  L.push(`• Reward/risk: ${s.rr_ratio}`);
    if (s.leverage)          L.push(`• Suggested leverage: ${s.leverage}`);
    L.push('');
  }

  L.push('Not financial advice — always do your own research and manage risk.');
  return L.join('\n');
}

async function copySignalPost() {
  const btn = document.getElementById('copyPostBtn');
  const text = buildSignalPost();
  if (!text) { if (btn) btn.textContent = 'No signal yet'; return; }
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch (_) {
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      ok = document.execCommand('copy');
      document.body.removeChild(ta);
    } catch (_) { ok = false; }
  }
  if (btn) {
    const original = '📋 Copy for post';
    btn.textContent = ok ? '✅ Copied!' : '⚠️ Copy failed';
    setTimeout(() => { btn.textContent = original; }, 1800);
  }
}

/* ─── X Posts — Signal Confluence ────────────────────────────────────────── */
async function generateXPosts() {
  const btn     = document.getElementById('generateBtn');
  const loading = document.getElementById('journalLoading');
  const output  = document.getElementById('journalOutput');

  btn.disabled = true;
  loading.classList.remove('hidden');
  output.classList.add('hidden');

  try {
    const res = await fetch(`${API}/twitter/posts`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Failed');
    document.getElementById('xPost1').textContent = data.post1;
    document.getElementById('xPost2').textContent = data.post2;
    output.classList.remove('hidden');
  } catch (e) {
    alert('Failed to generate X posts: ' + e.message);
  } finally {
    btn.disabled = false;
    loading.classList.add('hidden');
  }
}

function copyXPost(n) {
  const el  = document.getElementById(`xPost${n}`);
  const btn = el?.closest('.x-post-block')?.querySelector('.copy-btn');
  if (!el || !btn) return;
  navigator.clipboard.writeText(el.textContent).then(() => {
    btn.textContent = '✅ Copied!';
    setTimeout(() => { btn.textContent = `📋 Copy Post ${n}`; }, 2000);
  });
}

/* ─── D-ID Video Generation ──────────────────────────────────────────────── */
async function generateVideo() {
  const scriptEl = document.getElementById('journalScript');
  const script   = scriptEl?.innerText?.trim();
  if (!script) {
    alert('Generate the journal script first, then click Generate Video.');
    return;
  }

  const btn     = document.getElementById('genVideoBtn');
  const status  = document.getElementById('videoStatus');
  const warn    = document.getElementById('videoTruncateWarn');
  const output  = document.getElementById('videoOutput');

  btn.disabled     = true;
  btn.textContent  = '⏳ Submitting…';
  status.textContent = '';
  warn.classList.add('hidden');
  output.classList.add('hidden');
  output.innerHTML = '';

  try {
    const res  = await fetch(`${API}/video/create`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ script }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (data.truncated) warn.classList.remove('hidden');

    btn.textContent = '⏳ Rendering…';
    status.textContent = 'D-ID is rendering your video — usually 1–3 min…';
    await _pollVideo(data.talk_id);
  } catch (e) {
    status.textContent = '❌ ' + e.message;
    btn.disabled    = false;
    btn.textContent = '🎬 Generate Video';
  }
}

async function _pollVideo(talkId) {
  const btn    = document.getElementById('genVideoBtn');
  const status = document.getElementById('videoStatus');
  const output = document.getElementById('videoOutput');
  let   secs   = 0;

  for (let i = 0; i < 72; i++) {   // max 6 min (72 × 5 s)
    await new Promise(r => setTimeout(r, 5000));
    secs += 5;

    const res  = await fetch(`${API}/video/status/${talkId}`);
    const data = await res.json();

    if (data.error && data.status !== 'created' && data.status !== 'started') {
      throw new Error(data.error || 'D-ID rendering failed');
    }

    if (data.status === 'done' && data.result_url) {
      status.textContent = '✅ Video ready!';
      output.classList.remove('hidden');
      output.innerHTML = `
        <video class="did-video" src="${data.result_url}" controls playsinline></video>
        <div class="did-video-actions">
          <a class="btn-outline" href="${data.result_url}" download target="_blank">⬇️ Download MP4</a>
          <button class="btn-outline" onclick="navigator.clipboard.writeText('${data.result_url}').then(()=>this.textContent='✅ Copied!').catch(()=>{})">🔗 Copy URL</button>
        </div>`;
      btn.disabled    = false;
      btn.textContent = '🎬 Generate Video';
      return;
    }

    status.textContent = `Rendering… ${secs}s elapsed (${data.status})`;
  }
  throw new Error('Timed out after 6 min — check your D-ID dashboard for the video');
}

/* ─── UI helpers ──────────────────────────────────────────────────────────── */
function setLoading(on) {
  document.getElementById('loadingOverlay').classList.toggle('hidden', !on);
}

function showError(msg) {
  const el = document.getElementById('priceValue');
  if (el) el.textContent = 'Error: ' + msg;
}

/* ─── CVD source loader ───────────────────────────────────────────────────── */
async function loadCvdFromSource(cvdType) {
  const isSpot   = cvdType === 'spot';
  const source   = isSpot ? S.spotCvdSource : S.futCvdSource;
  const selId    = isSpot ? 'spotCvdSource' : 'futCvdSource';
  const sel      = document.getElementById(selId);
  const series   = isSpot ? S.spotCvdSeries : S.futCvdSeries;
  const valId    = isSpot ? 'spotCvdVal'   : 'futCvdVal';
  const trendId  = isSpot ? 'spotCvdTrend' : 'futCvdTrend';

  if (source === 'auto') {
    // Use data already loaded from the main analysis
    if (S.analysis) {
      const cvd = isSpot ? S.analysis.spot_cvd : (S.analysis.agg_cvd || S.analysis.futures_cvd);
      if (!cvd && !isSpot) {
        // No perp market for this token/timeframe — show a clear N/A instead of
        // leaving a stale "error" badge from a previously-failed source fetch.
        setFutCvdNA();
      } else {
        renderCVDPanel(isSpot ? 'spot' : 'fut', cvd, series, valId, trendId);
      }
    }
    return;
  }

  sel.classList.add('cvd-source-loading');
  try {
    const res = await fetch(
      `${API}/cvd/${S.symbol}?source=${source}&type=${cvdType}&timeframe=${S.timeframe}`
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    const cvd = await res.json();
    renderCVDPanel(isSpot ? 'spot' : 'fut', cvd, series, valId, trendId);
  } catch (e) {
    // A specific exchange source has no perp market for this token (common for
    // alts like TAO on some venues) — show "N/A · not on <source>" not "error".
    const tEl = document.getElementById(trendId);
    if (tEl) { tEl.textContent = `not on ${source}`; tEl.className = 'cvd-trend neutral'; }
    const vEl = document.getElementById(valId);
    if (vEl) { vEl.textContent = 'N/A'; vEl.style.color = 'var(--muted)'; }
    if (series) series.setData([]);
    console.warn(`CVD source '${source}' failed:`, e.message);
  } finally {
    sel.classList.remove('cvd-source-loading');
  }
}

// Show a clear "no perpetual market" state on the futures CVD panel.
function setFutCvdNA() {
  const v = document.getElementById('futCvdVal');
  const t = document.getElementById('futCvdTrend');
  if (v) { v.textContent = 'N/A'; v.style.color = 'var(--muted)'; }
  if (t) { t.textContent = 'No perp market'; t.className = 'cvd-trend neutral'; }
  if (S.futCvdSeries) S.futCvdSeries.setData([]);
  const auto = document.querySelector('#futCvdSource option[value="auto"]');
  if (auto) auto.textContent = 'Auto';
}

/* ─── Selector wiring ─────────────────────────────────────────────────────── */
function wireSelectors() {
  document.getElementById('assetTabs').addEventListener('click', e => {
    const btn = e.target.closest('.asset-tab');
    if (!btn) return;
    document.querySelectorAll('.asset-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    S.symbol = btn.dataset.sym;
    loadAnalysis();
  });

  document.getElementById('tfTabs').addEventListener('click', e => {
    const btn = e.target.closest('.tf-tab');
    if (!btn) return;
    document.querySelectorAll('.tf-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    S.timeframe = btn.dataset.tf;
    loadAnalysis();
  });

  document.getElementById('futCvdSource').addEventListener('change', e => {
    S.futCvdSource = e.target.value;
    loadCvdFromSource('futures');
  });
}

/* ─── Strength Change Monitor ─────────────────────────────────────────────── */
const ALL_TOKENS = ['BTC','ETH','LINK','SOL','XRP','TAO','HYPE','SUI','KAS','ALGO','XMR','GRAM','ONDO','AAVE','RENDER','BNB','BLUR','ZEC','TRX','ADA','XLM','AVAX','HBAR','QNT','INJ','FET','ICP','ENJ','TNSR'];
const STRENGTH_THRESHOLD = 20;
const _STRENGTH_SNAP_KEY  = 'strength_snap_v1';
const _STRENGTH_SEEN_KEY  = 'strength_seen_v1';
let   _strengthAlerts     = [];
let   _whaleAlerts        = [];

function _getStrengthSnap() {
  try { return JSON.parse(localStorage.getItem(_STRENGTH_SNAP_KEY) || '{}'); }
  catch (_) { return {}; }
}
function _saveStrengthSnap(snap) {
  try { localStorage.setItem(_STRENGTH_SNAP_KEY, JSON.stringify(snap)); }
  catch (_) {}
}
function _getStrengthSeen() {
  try { return JSON.parse(localStorage.getItem(_STRENGTH_SEEN_KEY) || '{}'); }
  catch (_) { return {}; }
}
function _markStrengthSeen(id) {
  const seen = _getStrengthSeen();
  seen[id] = Date.now();
  const cutoff = Date.now() - 7 * 86400 * 1000;
  Object.keys(seen).forEach(k => { if (seen[k] < cutoff) delete seen[k]; });
  localStorage.setItem(_STRENGTH_SEEN_KEY, JSON.stringify(seen));
}

async function checkStrengthChanges() {
  const snap = _getStrengthSnap();
  const newAlerts = [];

  for (const sym of ALL_TOKENS) {
    try {
      const res  = await fetch(`${API}/analysis?symbol=${sym}&timeframe=1H`);
      if (!res.ok) continue;
      const data = await res.json();
      const sig  = data?.signal;
      if (!sig || sig.direction === 'NEUTRAL' || sig.strength == null) continue;

      const key      = `${sym}_1H`;
      const prev     = snap[key];
      const curr     = sig.strength;
      const dir      = sig.direction;
      const now      = Date.now();

      // First run — just snapshot, no alert
      if (prev == null) {
        snap[key] = { strength: curr, dir, ts: now };
        continue;
      }

      const delta = curr - prev.strength;
      if (Math.abs(delta) >= STRENGTH_THRESHOLD) {
        const alertId  = `str_${sym}_${now}`;
        newAlerts.push({
          id:       alertId,
          symbol:   sym,
          dir,
          from:     prev.strength,
          to:       curr,
          delta,
          ts:       now,
        });
        snap[key] = { strength: curr, dir, ts: now };
      } else {
        snap[key] = { strength: curr, dir, ts: now };
      }
    } catch (_) { /* network error for one token — skip */ }
  }

  _saveStrengthSnap(snap);

  if (newAlerts.length) {
    _strengthAlerts = [...newAlerts, ..._strengthAlerts].slice(0, 30);
    _renderNotifList();
    const seen   = _getStrengthSeen();
    const unseen = _strengthAlerts.filter(a => !seen[a.id]).length;
    const engulfUnseen = _engulfAlerts.filter(a => !_getSeenAlerts()[`engulf_${a.symbol}_${a.timestamp}`]).length;
    _updateBadge(unseen + engulfUnseen);
  }
}

/* ─── Engulfing Alert Notification Panel (1W) ────────────────────────────── */
const _ENGULF_SEEN_KEY = 'engulf_seen_v2';
let   _engulfAlerts    = [];

function _getSeenAlerts() {
  try { return JSON.parse(localStorage.getItem(_ENGULF_SEEN_KEY) || '{}'); }
  catch (_) { return {}; }
}
function _markSeen(id) {
  const seen = _getSeenAlerts();
  seen[id] = Date.now();
  const cutoff = Date.now() - 21 * 86400 * 1000;
  Object.keys(seen).forEach(k => { if (seen[k] < cutoff) delete seen[k]; });
  localStorage.setItem(_ENGULF_SEEN_KEY, JSON.stringify(seen));
}

function toggleNotifPanel() {
  const panel   = document.getElementById('notifPanel');
  const overlay = document.getElementById('notifOverlay');
  const bell    = document.getElementById('notifBell');
  if (!panel) return;
  const open = panel.classList.toggle('notif-panel-open');
  panel.classList.toggle('hidden', !open);
  overlay.classList.toggle('hidden', !open);
  if (open) {
    _engulfAlerts.forEach(a => _markSeen(`engulf_${a.symbol}_${a.timestamp}`));
    _strengthAlerts.forEach(a => _markStrengthSeen(a.id));
    _whaleAlerts.forEach(a => _markStrengthSeen(`whale_${a.symbol}_${a.timestamp}`));
    _updateBadge(0);
  }
}

function clearAllAlerts() {
  _engulfAlerts.forEach(a => _markSeen(`engulf_${a.symbol}_${a.timestamp}`));
  _strengthAlerts.forEach(a => _markStrengthSeen(a.id));
  _whaleAlerts.forEach(a => _markStrengthSeen(`whale_${a.symbol}_${a.timestamp}`));
  _strengthAlerts = [];
  _whaleAlerts = [];
  _renderNotifList();
  _updateBadge(0);
}

function _updateBadge(count) {
  const badge = document.getElementById('notifBadge');
  const bell  = document.getElementById('notifBell');
  if (!badge) return;
  if (count > 0) {
    badge.textContent = count;
    badge.classList.remove('hidden');
    bell?.classList.add('notif-bell-active');
  } else {
    badge.classList.add('hidden');
    bell?.classList.remove('notif-bell-active');
  }
}

function _renderNotifList() {
  const list = document.getElementById('notifList');
  if (!list) return;

  const engulfSeen   = _getSeenAlerts();
  const strengthSeen = _getStrengthSeen();

  // Build unified items list, newest first
  const items = [];

  _strengthAlerts.forEach(a => {
    const isUp   = a.delta > 0;
    const cls    = isUp ? 'bull' : 'bear';
    const icon   = isUp ? '📈' : '📉';
    const arrow  = isUp ? `+${a.delta}` : `${a.delta}`;
    const dtStr  = new Date(a.ts).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true });
    const isNew  = !strengthSeen[a.id];
    items.push({ ts: a.ts, html: `<div class="notif-item notif-item-${cls}${isNew ? ' notif-item-new' : ''}">
      <span class="notif-item-icon">${icon}</span>
      <div class="notif-item-body">
        <div class="notif-item-title">Strength Jump — <strong>${a.symbol}/USDT</strong> <span class="notif-dir-tag">${a.dir}</span></div>
        <div class="notif-item-sub">1H · ${a.from} → ${a.to} <span class="notif-delta ${cls}">(${arrow})</span></div>
        <div class="notif-item-time">🕐 Detected: ${dtStr}</div>
      </div>
      <button class="notif-item-view" onclick="jumpTo('${a.symbol}','1H');toggleNotifPanel()">View →</button>
    </div>` });
  });

  _whaleAlerts.forEach(a => {
    const DIR_META = {
      bullish:            { icon: '🐋', label: 'Bullish Whale',          cls: 'bull' },
      bearish:            { icon: '🐻', label: 'Bearish Whale',          cls: 'bear' },
      absorption_bull:    { icon: '🛡️', label: 'Bull Absorption',        cls: 'bull' },
      absorption_bear:    { icon: '🛡️', label: 'Bear Absorption',        cls: 'bear' },
      bullish_absorption: { icon: '💪', label: 'Bullish (Bears Failed)', cls: 'bull' },
      bearish_rejection:  { icon: '❌', label: 'Bearish Rejection',      cls: 'bear' },
    };
    const m    = DIR_META[a.direction] || { icon: '❓', label: a.direction, cls: '' };
    const id   = `whale_${a.symbol}_${a.timestamp}`;
    const seen = _getStrengthSeen();
    const isNew = !seen[id];
    const when  = a.candles_ago === 1 ? 'Last 1H candle' : `${a.candles_ago} candles ago`;
    items.push({ ts: a.timestamp, html: `<div class="notif-item notif-item-${m.cls}${isNew ? ' notif-item-new' : ''}">
      <span class="notif-item-icon">${m.icon}</span>
      <div class="notif-item-body">
        <div class="notif-item-title">${m.label} — <strong>${a.symbol}/USDT</strong></div>
        <div class="notif-item-sub">1H · ${a.vol_multiple}× vol · ${a.taker_ratio}% taker buy · ${when}</div>
        <div class="notif-item-time">🕐 ${a.detected_at || ''}</div>
      </div>
      <button class="notif-item-view" onclick="jumpTo('${a.symbol}','1H');toggleNotifPanel()">View →</button>
    </div>` });
  });

  _engulfAlerts.forEach(a => {
    const isBull = a.direction === 'bullish';
    const cls    = isBull ? 'bull' : 'bear';
    const icon   = isBull ? '🟢' : '🔴';
    const label  = isBull ? 'Bullish Engulfing' : 'Bearish Engulfing';
    const when   = a.candles_ago === 1 ? 'last closed 1W candle' : `${a.candles_ago} closed candles ago`;
    const id     = `engulf_${a.symbol}_${a.timestamp}`;
    const isNew  = !engulfSeen[id];
    items.push({ ts: a.timestamp || 0, html: `<div class="notif-item notif-item-${cls}${isNew ? ' notif-item-new' : ''}">
      <span class="notif-item-icon">${icon}</span>
      <div class="notif-item-body">
        <div class="notif-item-title">${label} — <strong>${a.symbol}/USDT</strong></div>
        <div class="notif-item-sub">1W confirmed · ${when} · body ${a.body_ratio}×</div>
        <div class="notif-item-msg">${isBull ? 'Potential bullish reversal' : 'Potential bearish reversal'}</div>
        ${a.detected_at ? `<div class="notif-item-time">🕐 Detected: ${a.detected_at}</div>` : ''}
      </div>
      <button class="notif-item-view" onclick="jumpTo('${a.symbol}','1W');toggleNotifPanel()">View →</button>
    </div>` });
  });

  if (!items.length) {
    list.innerHTML = '<p class="notif-empty">No alerts yet. Strength checked hourly.</p>';
    return;
  }

  items.sort((a, b) => b.ts - a.ts);
  list.innerHTML = items.map(i => i.html).join('');
}

async function loadEngulfAlerts() {
  try {
    const res  = await fetch(`${API}/engulf-alerts`);
    const data = await res.json();
    _engulfAlerts = data.alerts || [];
    _renderNotifList();
    const seen   = _getSeenAlerts();
    const strSeen = _getStrengthSeen();
    const unreadE = _engulfAlerts.filter(a => !seen[`engulf_${a.symbol}_${a.timestamp}`]).length;
    const unreadS = _strengthAlerts.filter(a => !strSeen[a.id]).length;
    _updateBadge(unreadE + unreadS);
  } catch (_) {}
}

async function loadWhaleAlerts() {
  try {
    const res  = await fetch(`${API}/whale-alerts`);
    const data = await res.json();
    _whaleAlerts = data.alerts || [];
    _renderNotifList();
    const seen    = _getStrengthSeen();
    const unseen  = _whaleAlerts.filter(a => !seen[`whale_${a.symbol}_${a.timestamp}`]).length;
    const engulfU = _engulfAlerts.filter(a => !_getSeenAlerts()[`engulf_${a.symbol}_${a.timestamp}`]).length;
    const strengthU = _strengthAlerts.filter(a => !seen[a.id]).length;
    _updateBadge(unseen + engulfU + strengthU);
  } catch (_) {}
}

/* ─── Recommended Trades ─────────────────────────────────────────────────── */


async function sendToTelegram() {
  const btn   = document.getElementById('tgSendBtn');
  const icon  = document.getElementById('tgBtnIcon');
  const label = document.getElementById('tgBtnLabel');
  if (!btn || btn.disabled) return;

  btn.disabled = true;
  icon.textContent  = '⏳';
  label.textContent = 'Sending…';

  try {
    const res  = await fetch(`${API}/telegram/send`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      icon.textContent  = '✅';
      label.textContent = 'Sent!';
      setTimeout(() => {
        icon.textContent  = '✈️';
        label.textContent = 'Send to Telegram';
        btn.disabled = false;
      }, 3000);
    } else {
      throw new Error(data.error || 'Failed');
    }
  } catch (e) {
    icon.textContent  = '❌';
    label.textContent = e.message.includes('not configured') ? 'Bot not configured' : 'Failed — check server logs';
    setTimeout(() => {
      icon.textContent  = '✈️';
      label.textContent = 'Send to Telegram';
      btn.disabled = false;
    }, 4000);
  }
}

// Session starts at 8AM SGT = 00:00 UTC exactly.
// 30-min cache key — invalidates at :00 and :30 of each UTC hour.
function _recCacheKey() {
  const now  = new Date();
  const y    = now.getUTCFullYear();
  const m    = String(now.getUTCMonth() + 1).padStart(2, '0');
  const d    = String(now.getUTCDate()).padStart(2, '0');
  const h    = String(now.getUTCHours()).padStart(2, '0');
  const half = String(Math.floor(now.getUTCMinutes() / 30) * 30).padStart(2, '0');
  return `rec35_mtf_${y}${m}${d}${h}${half}`;
}

function _recCacheGet() {
  try {
    const raw = localStorage.getItem(_recCacheKey());
    return raw ? JSON.parse(raw) : null;
  } catch (_) { return null; }
}

function _recCacheSet(data) {
  try {
    // Prune any old rec_ / rec2_ / rec3_ keys from previous days
    Object.keys(localStorage)
      .filter(k => /^rec\d*_/.test(k) && k !== _recCacheKey())
      .forEach(k => localStorage.removeItem(k));
    localStorage.setItem(_recCacheKey(), JSON.stringify(data));
  } catch (_) {}
}

function _strengthTier(s) {
  if (s >= 69) return 'str-confirmed';
  if (s >= 51) return 'str-strong';
  if (s >= 33) return 'str-moderate';
  return 'str-weak';
}

function _buildRecCard(r, i) {
  const isLong  = r.direction === 'LONG';
  const dirCls  = isLong ? 'bull' : 'bear';
  const dirIcon = isLong ? '▲' : '▼';
  const tps     = r.tp_targets || [];
  const tpPcts  = r.tp_pcts   || [];

  const strengthBar = `<div class="rec-str-track">
    <div class="rec-str-fill ${dirCls}" style="width:${Math.min(r.display_strength ?? r.h2_strength ?? r.strength, 100)}%"></div>
  </div>`;

  const reasons = (r.reasons || []).slice(0, 2).map(rx => {
    const isBull = rx.startsWith('▲');
    return `<li class="${isBull ? 'bull' : 'bear'}">${rx}</li>`;
  }).join('');

  const tp1  = tps[0]     != null ? fmtPrice(tps[0]) : 'N/A';
  const tp2  = tps[1]     != null ? fmtPrice(tps[1]) : 'N/A';
  const tp1p = tpPcts[0]  != null ? `+${tpPcts[0]}%` : '';
  const tp2p = tpPcts[1]  != null ? `+${tpPcts[1]}%` : '';

  // 1H+2H must agree — levels come from 2H (wider targets suit 4-24h holds)
  const tfAlign = r.aligned_tfs
    ? `<span class="rec-tf-align">✅ ${r.aligned_tfs} aligned · ${r.timeframe} levels</span>` : '';
  const btcAdj = r.btc_adj != null ? Math.abs(r.btc_adj) : '';
  const corrFactor = r.btc_corr != null ? r.btc_corr : 1.0;
  const btcWarn = r.btc_conflict
    ? `<span class="rec-btc-conflict">⚠ vs BTC ${r.btc_consensus} −${btcAdj}${corrFactor <= 0.6 ? ' (partial corr)' : ''}</span>`
    : r.btc_aligned
    ? `<span class="rec-btc-aligned">✅ with BTC ${r.btc_consensus} +${btcAdj}</span>`
    : '';

  // Parse tf labels from aligned_tfs (e.g. "1H·2H" or "2H·4H")
  const [tfLabel1, tfLabel2] = (r.aligned_tfs || '').split('·');
  const tfBreakdown = (r.h1_strength != null && tfLabel1 && tfLabel2)
    ? `<div class="rec-tf-breakdown">
        <span>${tfLabel1} <strong>${r.h1_strength}</strong></span>
        <span>${tfLabel2} <strong>${r.h2_strength}</strong></span>
       </div>` : '';

  // Higher-timeframe confluence badge: 1D + 1W + 1M
  const mtfBadge = (() => {
    const dirs  = r.mtf_dirs || {};
    const icons = { LONG: '▲', SHORT: '▼', NEUTRAL: '—' };
    const clses = { LONG: 'bull', SHORT: 'bear', NEUTRAL: 'neutral' };
    const items = ['1D', '1W', '1M'].map(tf => {
      const d = dirs[tf] || 'NEUTRAL';
      return `<span class="mtf-tf ${clses[d]}">${tf}&nbsp;${icons[d]}</span>`;
    }).join('');
    const ct  = r.mtf_aligned ?? 0;
    const adj = r.mtf_adj != null ? (r.mtf_adj >= 0 ? `+${r.mtf_adj}` : `${r.mtf_adj}`) : '';
    const scoreCls = ct >= 2 ? 'bull' : ct === 0 ? 'bear' : 'warn';
    const warn = r.mtf_counter ? `<span class="mtf-counter-warn">⚠ Counter-trend</span>`
               : r.mtf_confirm ? `<span class="mtf-full-confirm">✓ Confirmed</span>` : '';
    return `<div class="rec-mtf-row">
      <div class="mtf-tfs">${items}</div>
      <span class="mtf-score ${scoreCls}">${ct}/3 ${adj}</span>
      ${warn}
    </div>`;
  })();

  // Entry distance from scan-time price — compact format
  const entryDist = (() => {
    if (!r.current_price || !r.entry || r.current_price === r.entry) return '';
    const pct = ((r.entry - r.current_price) / r.current_price * 100);
    return `<span class="rec-entry-dist ${pct > 0 ? 'bear' : 'bull'}">${pct > 0 ? '+' : ''}${pct.toFixed(2)}%</span>`;
  })();
  // Detected timestamp — short format
  const detectedShort = r.detected_at
    ? r.detected_at.replace(/\d{4} · /, '').replace(' SGT', '') : '';

  return `<div class="rec-card rec-card-${dirCls}${r.btc_conflict ? ' rec-card-conflict' : ''}" data-rec-sym="${r.symbol}">
    <div class="rec-card-top">
      <span class="rec-rank">#${i+1}</span>
      <span class="rec-sym">${r.symbol}/USDT</span>
      <span class="rec-dir ${dirCls}">${dirIcon} ${r.direction}</span>
      <span class="rec-strength ${_strengthTier(r.display_strength ?? r.h2_strength)}">${r.display_strength ?? r.h2_strength}/100</span>
    </div>
    ${tfAlign}
    ${btcWarn}
    ${mtfBadge}
    ${tfBreakdown}
    <div class="rec-meta-row">
      ${detectedShort ? `<span class="rec-detected">🕐 ${detectedShort}</span>` : ''}
      <span class="rec-live-price" data-sym="${r.symbol}">—</span>
    </div>
    ${strengthBar}
    <div class="rec-levels">
      <div class="rec-lvl"><span class="rec-lbl">Entry</span><span class="rec-val">${fmtPrice(r.entry)} ${entryDist}</span></div>
      <div class="rec-lvl"><span class="rec-lbl">Stop Loss</span><span class="rec-val bear">${fmtPrice(r.sl)} ${r.sl_pct ? `<small>-${r.sl_pct}%</small>` : ''}</span></div>
      <div class="rec-lvl"><span class="rec-lbl">TP 1</span><span class="rec-val bull">${tp1} ${tp1p ? `<small>${tp1p}</small>` : ''}</span></div>
      <div class="rec-lvl"><span class="rec-lbl">TP 2</span><span class="rec-val bull">${tp2} ${tp2p ? `<small>${tp2p}</small>` : ''}</span></div>
      ${r.rr_ratio ? `<div class="rec-lvl"><span class="rec-lbl">R/R</span><span class="rec-val">${r.rr_ratio} : 1</span></div>` : ''}
      ${r.leverage  ? `<div class="rec-lvl"><span class="rec-lbl">Leverage</span><span class="rec-val ${r.leverage >= 5 ? 'bull' : ''}">${r.leverage}×</span></div>` : ''}
    </div>
    ${reasons ? `<ul class="rec-reasons">${reasons}</ul>` : ''}
    ${r.vol_tier_label ? `<span class="vol-tier-badge" style="margin-top:4px">${r.vol_tier_label}</span>` : ''}
    <button class="rec-go-btn" onclick="jumpTo('${r.symbol}','${r.view_tf || r.timeframe}')">View Analysis →</button>
  </div>`;
}

async function loadRecommendations() {
  const section = document.getElementById('recSection');
  const cards   = document.getElementById('recCards');
  const dateEl  = document.getElementById('recDateLabel');
  const valEl   = document.getElementById('recValidity');
  if (!section || !cards) return;

  try {
    // Use localStorage cache for token list / entry-SL-TP — always refresh scores live
    let data = _recCacheGet();
    if (!data) {
      const res = await fetch(`${API}/recommendations`);
      data = await res.json();
      if (data.recommendations?.length) _recCacheSet(data);
    }
    if (!data.recommendations?.length) {
      // Show section with informative message rather than hiding it
      section.classList.remove('hidden');
      if (dateEl) dateEl.textContent = data.date_label || '';
      cards.innerHTML = `<div class="rec-no-signal">
        <div class="rec-no-signal-icon">📉</div>
        <div class="rec-no-signal-title">No High-Quality Signals Right Now</div>
        <div class="rec-no-signal-desc">All current setups are below the minimum conviction threshold (32/100).
        Waiting for stronger alignment is the correct decision — a bad trade is worse than no trade.</div>
        <div class="rec-no-signal-next">Next scan: ${data.valid_until_fmt || 'next slot'}</div>
      </div>`;
      return;
    }

    if (dateEl) dateEl.textContent = data.date_label || '';
    if (valEl && data.valid_until_fmt) {
      valEl.textContent = `Valid until ${data.valid_until_fmt}`;
    }
    const genEl = document.getElementById('recGenerated');
    if (genEl && data.generated_fmt) {
      genEl.textContent = `⏱ Generated: ${data.generated_fmt}`;
      genEl.title = 'Rating & strength are a snapshot from this exact moment. Only changes at 8AM / 4PM / 8PM SGT.';
    }

    // Options expiry banner — update with full BTC-priced data from recs endpoint
    if (data.options_expiry) renderOptionsBanner(data.options_expiry);

    // BTC consensus banner (replace if already rendered)
    const btcBanner = (() => {
      const bc = data.btc_consensus;
      const bs = data.btc_strength;
      if (!bc || bc === 'NEUTRAL') {
        return `<div class="btc-banner btc-neutral">⚪ BTC: Neutral — no market bias applied</div>`;
      }
      const cls  = bc === 'LONG' ? 'bull' : 'bear';
      const icon = bc === 'LONG' ? '▲' : '▼';
      return `<div class="btc-banner btc-${cls}">${icon} BTC Signal: <strong>${bc}</strong> (${bs}/100) — altcoins opposing this direction penalised −25 pts</div>`;
    })();
    const existingBanner = cards.parentElement.querySelector('.btc-banner');
    if (existingBanner) existingBanner.remove();
    cards.insertAdjacentHTML('beforebegin', btcBanner);

    const recs = data.recommendations || [];
    cards.innerHTML = recs.length
      ? recs.map(_buildRecCard).join('')
      : '<p class="rec-empty">No signals aligned today.</p>';

    section.classList.remove('hidden');
    // Fire-and-forget: refresh prices and live signal scores (non-blocking)
    _refreshRecPrices();
    _refreshRecScores(recs);
  } catch (_) {}
}

async function _refreshRecScores(recs) {
  if (!recs?.length) return;
  const syms = recs.map(r => r.symbol).join(',');
  const tf   = recs[0]?.timeframe || '2H';
  try {
    const scores = await fetch(`${API}/scores?symbols=${syms}&tf=${tf}`).then(r => r.json());
    recs.forEach(r => {
      const live = scores[r.symbol];
      if (!live) return;
      const card = document.querySelector(`.rec-card[data-rec-sym="${r.symbol}"]`);
      if (!card) return;
      const strEl  = card.querySelector('.rec-strength');
      const fillEl = card.querySelector('.rec-str-fill');
      if (strEl)  strEl.textContent = `${live.strength}/100`;
      if (fillEl) fillEl.style.width = `${Math.min(live.strength, 100)}%`;
    });
  } catch (_) {}
}

async function _refreshRecPrices() {
  const els  = [...document.querySelectorAll('.rec-live-price[data-sym]')];
  if (!els.length) return;
  const syms = [...new Set(els.map(el => el.dataset.sym))].join(',');
  try {
    const prices = await fetch(`${API}/prices?symbols=${syms}`).then(r => r.json());
    els.forEach(el => {
      const p = prices[el.dataset.sym];
      if (p == null) return;
      el.textContent = fmtPrice(p);

      // Stale detection: compare live price against entry price stored on card
      const card = el.closest('.rec-card');
      if (!card) return;
      const entryEl = card.querySelector('.rec-val');   // first rec-val = entry
      const entryText = entryEl?.textContent?.replace(/[$,]/g, '').trim();
      const entry = entryText ? parseFloat(entryText) : null;
      if (!entry || !p || p <= 0) return;

      const drift = Math.abs(p - entry) / entry;
      // Remove any existing stale banner
      card.querySelector('.rec-price-stale')?.remove();

      if (drift >= 0.20) {
        const pct   = (drift * 100).toFixed(0);
        const cls   = drift >= 0.35 ? 'rec-price-stale-critical' : 'rec-price-stale-warn';
        const banner = document.createElement('div');
        banner.className = `rec-price-stale ${cls}`;
        banner.innerHTML = drift >= 0.35
          ? `⛔ Entry ${pct}% from live price — signal is stale, do NOT trade`
          : `⚠️ Entry ${pct}% from live price — verify before trading`;
        card.querySelector('.rec-levels')?.before(banner);
        // Dim the entry/SL/TP levels
        card.querySelector('.rec-levels')?.classList.add('rec-levels-stale');
      } else {
        card.querySelector('.rec-levels')?.classList.remove('rec-levels-stale');
      }
    });
  } catch (_) {}
}

function jumpTo(sym, tf) {
  document.querySelectorAll('.asset-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.sym === sym);
  });
  document.querySelectorAll('.tf-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tf === tf);
  });
  S.symbol    = sym;
  S.timeframe = tf;
  loadAnalysis();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function refresh() {
  await loadAnalysis();
  await loadTicker();
}

/* ─── Order Book Walls ────────────────────────────────────────────────────── */
function renderOrderBook(ob) {
  const buyEl   = document.getElementById('bigBuyBody');
  const sellEl  = document.getElementById('bigSellBody');
  const buySrc  = document.getElementById('obBuySource');
  const sellSrc = document.getElementById('obSellSource');
  if (!buyEl || !sellEl) return;

  if (!ob || !ob.biggest_bid) {
    const msg = '<p class="empty">Order book data unavailable</p>';
    buyEl.innerHTML = sellEl.innerHTML = msg;
    if (buySrc)  buySrc.textContent = 'Unavailable';
    if (sellSrc) sellSrc.textContent = 'Unavailable';
    return;
  }

  const srcLabel = ob.source ? ob.source.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Live';
  if (buySrc)  buySrc.textContent = srcLabel;
  if (sellSrc) sellSrc.textContent = srcLabel;

  // ── Imbalance badge ────────────────────────────────────────────────────────
  const ratio = ob.bid_ask_ratio || 1;
  const imb   = ob.imbalance || 'balanced';
  const imbCfg = {
    strong_bid: { color: '#10b981', bg: 'rgba(16,185,129,.15)', label: `▲▲ Strong Buy Pressure  ${ratio.toFixed(2)}×`, icon: '🟢' },
    bid_heavy:  { color: '#34d399', bg: 'rgba(52,211,153,.12)', label: `▲ Bid-Heavy  ${ratio.toFixed(2)}×`,            icon: '🟢' },
    balanced:   { color: 'var(--muted)', bg: 'transparent',    label: `Balanced  ${ratio.toFixed(2)}×`,               icon: '⚪' },
    ask_heavy:  { color: '#f87171', bg: 'rgba(248,113,113,.12)',label: `▼ Ask-Heavy  ${(1/ratio).toFixed(2)}×`,        icon: '🔴' },
    strong_ask: { color: '#ef4444', bg: 'rgba(239,68,68,.15)', label: `▼▼ Strong Sell Pressure  ${(1/ratio).toFixed(2)}×`, icon: '🔴' },
  };
  const ic = imbCfg[imb] || imbCfg.balanced;
  const imbHTML = `<div class="ob-imbalance" style="background:${ic.bg};border-color:${ic.color};color:${ic.color}">
    ${ic.icon} ${ic.label}
    <span class="ob-imb-detail">${fmtK(ob.near_bid_usd)} bid vs ${fmtK(ob.near_ask_usd)} ask within ±2%</span>
  </div>`;

  // ── Wall renderer (depth bars) ─────────────────────────────────────────────
  function wallsHTML(walls, kind) {
    if (!walls || !walls.length) return '<p class="empty">—</p>';
    const maxUsd = walls[0].usd_value;
    return walls.map((w, i) => {
      const barPct  = Math.round(w.usd_value / maxUsd * 100);
      const usdVal  = w.usd_value >= 1e9 ? `$${(w.usd_value/1e9).toFixed(2)}B`
                    : w.usd_value >= 1e6 ? `$${(w.usd_value/1e6).toFixed(2)}M`
                    : `$${(w.usd_value/1e3).toFixed(1)}K`;
      const distAbs = Math.abs(w.distance_pct);
      const distStr = distAbs < 0.01 ? 'at market'
                    : w.distance_pct > 0 ? `+${w.distance_pct.toFixed(2)}% above`
                    :                      `${w.distance_pct.toFixed(2)}% below`;
      const sigColors  = { high: '#10b981', medium: '#f59e0b', low: 'var(--muted2)' };
      const barColor   = kind === 'buy' ? '#10b981' : '#ef4444';
      const sigColor   = sigColors[w.significance] || 'var(--muted2)';
      const dlabColors = { Immediate: '#f59e0b', Near: 'var(--muted2)', Far: 'var(--muted2)' };
      const dlabColor  = dlabColors[w.dist_label] || 'var(--muted2)';
      const mcapStr    = w.mcap_pct != null
        ? `<span style="color:${sigColor}">${w.mcap_pct >= 0.01 ? '⚡' : w.mcap_pct >= 0.001 ? '〰' : '·'} ${w.mcap_pct.toFixed(3)}% mcap</span>` : '';
      const topWall    = i === 0 ? ' ob-wall-top' : '';
      return `<div class="ob-wall${topWall}">
        <div class="ob-wall-header">
          <span class="ob-wall-price">${fmtPrice(w.price)}</span>
          <span class="ob-wall-usd">${usdVal}</span>
          <span class="ob-wall-dlabel" style="color:${dlabColor}">${w.dist_label} · ${distStr}</span>
        </div>
        <div class="ob-bar-track">
          <div class="ob-bar-fill" style="width:${barPct}%;background:${barColor}"></div>
        </div>
        ${mcapStr ? `<div class="ob-wall-mcap">${mcapStr}</div>` : ''}
      </div>`;
    }).join('');
  }

  // ── Air pocket warnings ────────────────────────────────────────────────────
  function airHTML(pocket, side) {
    if (!pocket) return '';
    const dir = side === 'below' ? '📉 below' : '📈 above';
    return `<div class="ob-air-pocket">
      ⚠ Air pocket ${dir}: <strong>${pocket.gap_pct.toFixed(1)}% gap</strong>
      ${fmtPrice(pocket.price_from)} → ${fmtPrice(pocket.price_to)} — thin liquidity, fast move risk
    </div>`;
  }

  buyEl.innerHTML  = imbHTML
    + wallsHTML(ob.top_bids, 'buy')
    + airHTML(ob.air_pocket_below, 'below');
  sellEl.innerHTML = wallsHTML(ob.top_asks, 'sell')
    + airHTML(ob.air_pocket_above, 'above');
}

/* ─── Options Expiry Banner ───────────────────────────────────────────────── */
function renderOptionsBanner(opts) {
  const el = document.getElementById('optionsBanner');
  if (!el) return;
  if (!opts || !opts.next_expiry) { el.classList.add('hidden'); return; }

  const ne      = opts.next_expiry;
  const bias    = opts.bias || {};
  const days    = ne.days_to_expiry;
  const hours   = ne.hours_to_expiry;
  const etype   = ne.type || 'weekly';
  const inWin   = bias.in_window;

  // Only show if quarterly/monthly or within 7 days of weekly
  if (etype === 'weekly' && days > 3) { el.classList.add('hidden'); return; }

  const typeEmoji = { quarterly: '🔴', monthly: '🟡', weekly: '🟢' }[etype] || '📅';
  const typeLabel = { quarterly: 'QUARTERLY', monthly: 'Monthly', weekly: 'Weekly' }[etype] || '';
  const countdown = days === 0 ? `${hours}h left` : `${days}d ${hours}h`;
  const isLive    = opts.data_source === 'deribit';

  // Bias badge
  let biasHtml = '';
  if (bias.bias && bias.bias !== 'neutral') {
    const biasCls  = bias.bias === 'bearish' ? 'opts-bear' : 'opts-bull';
    const biasIcon = bias.bias === 'bearish' ? '▼ Price pinning DOWN' : '▲ Price pinning UP';
    const strLabel = inWin ? ` · signal strength ${bias.strength}/100` : ' (outside pinning window)';
    biasHtml = `<span class="opts-bias-badge ${biasCls}" title="Max pain is ${bias.bias === 'bullish' ? 'above' : 'below'} current price — market makers benefit from price moving ${bias.bias === 'bullish' ? 'up' : 'down'} toward max pain before expiry">${biasIcon}${strLabel}</span>`;
  }

  // Live data row: max pain + put/call + notional
  const fmtN = (v) => {
    if (!v) return null;
    return v >= 1e9 ? `$${(v/1e9).toFixed(1)}B` : `$${(v/1e6).toFixed(0)}M`;
  };
  const maxPain   = bias.max_pain   ? `Max Pain $${Number(bias.max_pain).toLocaleString()}` : null;
  const pc        = bias.put_call_ratio != null ? bias.put_call_ratio : null;
  const pcLabel   = pc != null ? (pc > 1.2 ? 'put-heavy (bearish bets)' : pc < 0.8 ? 'call-heavy (bullish bets)' : 'balanced') : null;
  const pcRatio   = pc != null ? `Put/Call ${pc.toFixed(2)} — ${pcLabel}` : null;
  const notional  = fmtN(ne.notional_usd || opts.total_notional);
  const liveStats = [maxPain, pcRatio, notional].filter(Boolean);
  const liveHtml  = liveStats.length
    ? `<div class="opts-live-stats">${liveStats.map(s => `<span class="opts-stat">${s}</span>`).join('')}${isLive ? '<span class="opts-src">live · Deribit</span>' : ''}</div>`
    : (isLive ? '<div class="opts-live-stats"><span class="opts-src">live · Deribit</span></div>' : '');

  // Upcoming expiries mini-row (show notional if available)
  const upcoming = (opts.upcoming || []).slice(0, 4);
  const upcomingHtml = upcoming.map(u => {
    const cls  = { quarterly: 'opts-q', monthly: 'opts-m', weekly: 'opts-w' }[u.type] || '';
    const pain = u.max_pain ? ` pain $${Number(u.max_pain).toLocaleString()}` : '';
    const not  = u.notional_usd ? ` ${fmtN(u.notional_usd)}` : '';
    return `<span class="opts-cal-pill ${cls}" title="${u.type}${pain}${not}">${u.label} <small>${u.days_to_expiry}d${not}</small></span>`;
  }).join('');

  el.className = `options-banner opts-${etype}${inWin ? ' opts-active' : ''}`;
  el.innerHTML = `
    <div class="opts-main">
      ${typeEmoji} <strong>${typeLabel} Options Expiry</strong>
      <span class="opts-date">${ne.label}</span>
      <span class="opts-countdown">${countdown}</span>
      ${biasHtml}
    </div>
    ${liveHtml}
    ${bias.description ? `<div class="opts-desc">${bias.description}</div>` : ''}
    <div class="opts-cal">${upcomingHtml}</div>
  `;
  el.classList.remove('hidden');
}

/* ─── Holiday Banner ──────────────────────────────────────────────────────── */
function renderHolidayBanner(holidays) {
  const el = document.getElementById('holidayBanner');
  if (!el) return;
  if (!holidays || !holidays.length) {
    el.classList.add('hidden');
    return;
  }

  const pills = holidays.map(h => {
    const when = h.days_away === 0 ? 'Today' :
                 h.days_away === 1 ? 'Tomorrow' :
                 `in ${h.days_away}d`;
    return `<span class="hol-pill impact-${h.impact}" title="${h.region}">
      ${h.name} · ${when}
    </span>`;
  }).join('');

  el.className = 'holiday-banner';
  el.innerHTML = `🔔 <strong>Upcoming Holidays — expect reduced liquidity:</strong>
    <span class="hol-items">${pills}</span>`;
}

/* ─── Bootstrap ───────────────────────────────────────────────────────────── */
async function renderAssetTabs() {
  const container = document.getElementById('assetTabs');
  let symbols = [];
  try {
    const res  = await fetch('/api/market-caps');
    const data = await res.json();           // [{symbol, market_cap}, ...]
    symbols = data.map(d => d.symbol);
  } catch (_) {
    // fallback: use ALL_TOKENS order if endpoint unreachable
    symbols = ALL_TOKENS;
  }
  container.innerHTML = symbols.map((sym, i) =>
    `<button class="asset-tab${i === 0 ? ' active' : ''}" data-sym="${sym}">${sym}</button>`
  ).join('');
  // set active symbol to first in sorted list
  S.symbol = symbols[0] || S.symbol;
}

document.addEventListener('DOMContentLoaded', async () => {
  await renderAssetTabs();   // build tabs sorted by live market cap first
  wireSelectors();
  initCharts();
  renderMyTrades();
  loadTicker();
  loadAnalysis();
  loadRecommendations();
  loadEngulfAlerts();
  checkStrengthChanges();
  loadWhaleAlerts();
  loadMacro();
  loadCalendar();
  setInterval(loadWhaleAlerts, 5 * 60 * 1000);
  setInterval(loadMacro, 6 * 60 * 60 * 1000);   // macro updates a few times/day at most

  // Auto-refresh every 5 minutes (ticker); strength check every 60 minutes
  setInterval(loadTicker, 5 * 60 * 1000);
  setInterval(checkStrengthChanges, 60 * 60 * 1000);
});

/* ─── Macro economic events ───────────────────────────────────────────────── */
async function loadMacro() {
  const sec = document.getElementById('macroSection');
  if (!sec) return;
  try {
    const res = await fetch(`${API}/macro`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderMacro(data);
  } catch (e) {
    console.warn('Macro load failed:', e.message);
    sec.style.display = 'none';
  }
}

/* ─── GOMINING tokenomics ─────────────────────────────────────────────────── */
function renderGtk(gtk, symbol) {
  const sec  = document.getElementById('gtkSection');
  const grid = document.getElementById('gtkGrid');
  if (!sec || !grid) return;
  if (symbol !== 'GOMINING') { sec.style.display = 'none'; return; }
  sec.style.display = '';
  if (!gtk) {
    // Never hide silently — a transient CoinGecko/Etherscan failure looks
    // identical to "feature missing" otherwise. Data retries within 10 min.
    grid.innerHTML = '<div class="etf-unavailable">Tokenomics data temporarily unavailable — retrying automatically (supply via CoinGecko, burns via Etherscan)</div>';
    return;
  }

  const s = gtk.supply || {};
  const b = gtk.burns;
  const supCls = v => v == null ? '' : v <= -0.1 ? 'bull' : v >= 0.1 ? 'bear' : '';
  const fmtPct = v => v == null ? '—' : `${v > 0 ? '+' : ''}${v}%`;

  const tiles = [];
  tiles.push(`
    <div class="etf-stat"><span class="etf-stat-lbl">Circulating supply</span>
      <span class="etf-stat-val">${s.supply_now_m != null ? s.supply_now_m + 'M' : '—'}</span></div>
    <div class="etf-stat"><span class="etf-stat-lbl">Supply 7d</span>
      <span class="etf-stat-val ${supCls(s.supply_7d_pct)}">${fmtPct(s.supply_7d_pct)}</span></div>
    <div class="etf-stat"><span class="etf-stat-lbl">Supply 30d</span>
      <span class="etf-stat-val ${supCls(s.supply_30d_pct)}">${fmtPct(s.supply_30d_pct)}</span></div>
    <div class="etf-stat"><span class="etf-stat-lbl">Next Burn &amp; Mint</span>
      <span class="etf-stat-val">${gtk.next_epoch || '—'} (${gtk.days_to_epoch}d)</span></div>`);
  if (b) {
    tiles.push(`
      <div class="etf-stat"><span class="etf-stat-lbl">Burned 7d (on-chain)</span>
        <span class="etf-stat-val bull">${(b.burn_7d || 0).toLocaleString()} GOMINING</span></div>
      <div class="etf-stat"><span class="etf-stat-lbl">Burned 35d</span>
        <span class="etf-stat-val bull">${(b.burn_35d || 0).toLocaleString()}</span></div>`);
  }
  const mt = gtk.maintenance;
  if (mt) {
    const wowCls = mt.wow_ratio == null ? '' : mt.wow_ratio >= 1.1 ? 'bull' : mt.wow_ratio <= 0.9 ? 'bear' : '';
    tiles.push(`
      <div class="etf-stat"><span class="etf-stat-lbl">Maintenance paid 7d</span>
        <span class="etf-stat-val">${(mt.maint_7d || 0).toLocaleString()} GMT</span></div>
      <div class="etf-stat"><span class="etf-stat-lbl">Maintenance WoW</span>
        <span class="etf-stat-val ${wowCls}">${mt.wow_ratio != null ? ((mt.wow_ratio-1)*100 >= 0 ? '+' : '') + ((mt.wow_ratio-1)*100).toFixed(0) + '%' : '—'}</span></div>`);
  }
  // Daily maintenance-paid trend (on-chain) — mini bar sparkline
  let dailyHtml = '';
  const dd = mt?.daily;
  if (dd && dd.length >= 3) {
    const mx = Math.max(...dd.map(d => d.amount)) || 1;
    const trendCls = (mt.avg_per_day != null && mt.avg_per_day_prev)
      ? (mt.avg_per_day > mt.avg_per_day_prev * 1.05 ? 'bull'
         : mt.avg_per_day < mt.avg_per_day_prev * 0.95 ? 'bear' : '') : '';
    const bars = dd.map(d => `<div class="gtk-bar-wrap" title="${d.date}: ${d.amount.toLocaleString()} GMT">
        <div class="gtk-bar" style="height:${Math.max(4, d.amount / mx * 100)}%"></div></div>`).join('');
    dailyHtml = `
      <div class="gtk-daily-hdr">Daily maintenance paid (21d, on-chain)
        <span class="${trendCls}">avg ${(mt.avg_per_day||0).toLocaleString()}/d${
          mt.avg_per_day_prev ? ` (${mt.avg_per_day>mt.avg_per_day_prev?'+':''}${Math.round((mt.avg_per_day/mt.avg_per_day_prev-1)*100)}% vs prior 7d)` : ''}</span></div>
      <div class="gtk-barchart">${bars}</div>`;
  }

  // Large GMT movements — trend-setters (locks/unlocks/whales)
  let movesHtml = '';
  const mv = gtk.large_moves;
  if (mv && mv.length) {
    const rows = mv.map(m => {
      const ago = Math.round((Date.now() - m.ts) / 86400000);
      const cls = m.kind.includes('burn') ? 'bull' : m.kind === 'whale transfer' ? '' : 'bear';
      return `<div class="gtk-move-row">
        <span class="gtk-move-amt ${cls}">${(m.amount/1e6).toFixed(2)}M</span>
        <span class="gtk-move-kind">${m.kind}</span>
        <span class="gtk-move-cp">${m.from_lbl} → ${m.to_lbl}</span>
        <span class="gtk-move-age">${ago===0?'today':ago+'d'}</span></div>`;
    }).join('');
    movesHtml = `<div class="gtk-daily-hdr" style="margin-top:10px">Large GMT moves (14d, ≥1M) — potential lock/unlock/whale</div>
      <div class="gtk-moves">${rows}</div>`;
  }

  grid.innerHTML = `
    <div class="etf-stats" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">${tiles.join('')}</div>
    ${dailyHtml}
    ${movesHtml}
    <div class="macro-reason" style="margin-top:8px">${
      [gtk.note, gtk.maint_note, gtk.epoch_note,
       gtk.onchain_ready ? '' : '<em>Add ETHERSCAN_API_KEY to unlock on-chain burn tracking</em>']
        .filter(Boolean).join(' · ')}</div>
    ${_renderGtkManual()}`;
}

/* ─── GoMining manual weekly tokenomics (app-only figures, this device) ─────── */
const GTK_MANUAL_KEY = 'gtk_manual_v1';
const _gtkManual = {
  read()  { try { return JSON.parse(localStorage.getItem(GTK_MANUAL_KEY) || '[]'); } catch (_) { return []; } },
  write(l){ try { localStorage.setItem(GTK_MANUAL_KEY, JSON.stringify(l)); } catch (_) {} },
};

function _renderGtkManual() {
  const log = _gtkManual.read();
  const last = log[log.length - 1];
  const prev = log[log.length - 2];
  const arrow = (cur, pv, goodUp) => {
    if (pv == null || cur == null) return '';
    const up = cur > pv, same = cur === pv;
    const good = same ? '' : (up === goodUp ? 'bull' : 'bear');
    return ` <span class="${good}">${same ? '' : up ? '↑' : '↓'}</span>`;
  };
  let latestHtml = '';
  if (last) {
    // mint ratio <1 = deflationary (good); locked/tvl/apr/maintained rising = good
    latestHtml = `
      <div class="etf-stats" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr));margin-top:6px">
        <div class="etf-stat"><span class="etf-stat-lbl">Mint ratio</span>
          <span class="etf-stat-val ${last.mint != null && last.mint < 1 ? 'bull' : last.mint > 1 ? 'bear' : ''}">×${last.mint ?? '—'}${arrow(last.mint, prev?.mint, false)}</span></div>
        <div class="etf-stat"><span class="etf-stat-lbl">Total locked</span>
          <span class="etf-stat-val">${last.locked != null ? last.locked + 'M' : '—'}${arrow(last.locked, prev?.locked, true)}</span></div>
        <div class="etf-stat"><span class="etf-stat-lbl">TVL (locked %)</span>
          <span class="etf-stat-val">${last.tvl != null ? last.tvl + '%' : '—'}${arrow(last.tvl, prev?.tvl, true)}</span></div>
        <div class="etf-stat"><span class="etf-stat-lbl">Maintained %</span>
          <span class="etf-stat-val">${last.maint != null ? last.maint + '%' : '—'}${arrow(last.maint, prev?.maint, true)}</span></div>
        <div class="etf-stat"><span class="etf-stat-lbl">veGOMINING APR</span>
          <span class="etf-stat-val">${last.apr != null ? last.apr + '%' : '—'}${arrow(last.apr, prev?.apr, true)}</span></div>
      </div>
      <div class="macro-reason" style="margin-top:4px">Logged ${last.date}${prev ? ` · vs ${prev.date}${_gtkGap(prev.date, last.date)}` : ''} · from GoMining app (not on-chain)</div>`;
  }
  const today = new Date().toISOString().slice(0, 10);
  // Prior entries as a compact history so a backfill is easy to see
  const hist = log.slice(-6).reverse().map(e =>
    `<span class="gtk-hist-chip" title="mint ×${e.mint ?? '—'} · locked ${e.locked ?? '—'}M · TVL ${e.tvl ?? '—'}%">${e.date}</span>`).join('');
  return `
    <details class="gtk-manual">
      <summary>📝 Log weekly tokenomics (mint ratio · locked · TVL · APR — app-only)</summary>
      <div class="gtk-manual-form">
        <label>Week of<input id="gtkDate" type="date" value="${today}" max="${today}"></label>
        <label>Mint ratio ×<input id="gtkMint" type="number" step="0.001" placeholder="0.979"></label>
        <label>Total locked (M)<input id="gtkLocked" type="number" step="0.1" placeholder="245.9"></label>
        <label>TVL locked %<input id="gtkTvl" type="number" step="0.1" placeholder="61"></label>
        <label>Maintained %<input id="gtkMaint" type="number" step="0.1" placeholder="46.18"></label>
        <label>veGOMINING APR %<input id="gtkApr" type="number" step="0.1" placeholder="21.42"></label>
        <button onclick="saveGtkManual()">Save</button>
      </div>
      <div class="gtk-manual-note">Miss a week? Change the date to backfill it — entries auto-sort and the trend always compares your two most recent dates.</div>
      ${hist ? `<div class="gtk-hist">Logged: ${hist}</div>` : ''}
    </details>
    ${latestHtml}`;
}

// Gap note between two ISO dates, e.g. " · 2wk gap" when a week was skipped
function _gtkGap(a, b) {
  const d = Math.round((new Date(b) - new Date(a)) / 86400000);
  if (d >= 10) return ` · ${Math.round(d / 7)}wk gap`;
  return '';
}

function saveGtkManual() {
  const num = id => { const v = parseFloat(document.getElementById(id)?.value); return isFinite(v) ? v : null; };
  const date = document.getElementById('gtkDate')?.value || new Date().toISOString().slice(0, 10);
  const entry = { date,
    mint: num('gtkMint'), locked: num('gtkLocked'), tvl: num('gtkTvl'),
    maint: num('gtkMaint'), apr: num('gtkApr') };
  if (entry.mint == null && entry.locked == null && entry.tvl == null) {
    alert('Enter at least the mint ratio, locked, or TVL from the GoMining app.');
    return;
  }
  const log = _gtkManual.read();
  const i = log.findIndex(e => e.date === entry.date);   // replace same date
  if (i >= 0) log[i] = entry; else log.push(entry);
  log.sort((x, y) => x.date < y.date ? -1 : 1);          // keep chronological
  while (log.length > 60) log.shift();
  _gtkManual.write(log);
  renderGtk(S.analysis?.gomining_tokenomics, S.analysis?.symbol);   // re-render
}

/* ─── Bittensor / TAO ecosystem ───────────────────────────────────────────── */
/* ─── TAO daily pool-flow dashboard (ETF-flow style) ──────────────────────── */
function renderTaoFlowHist(eco, symbol) {
  const sec  = document.getElementById('taoFlowSection');
  const grid = document.getElementById('taoFlowGrid');
  if (!sec || !grid) return;
  const daily = eco?.flow?.daily || [];
  const fc    = eco?.flow_cmp || {};
  if (symbol !== 'TAO' || (!daily.length && fc.today == null)) { sec.style.display = 'none'; return; }
  sec.style.display = '';

  const fmtT = v => v == null ? '—' :
    `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v) >= 1e6 ? (Math.abs(v)/1e6).toFixed(2)+'M' : Math.abs(v).toLocaleString()} τ`;
  const cls = v => v == null ? '' : v > 0 ? 'bull' : v < 0 ? 'bear' : '';

  // Comparison tiles — today vs prev 24h, vs 7d pace, vs 30d pace
  const tile = (lbl, val, vCls, sub) => `
    <div class="etf-stat tao-stat">
      <span class="etf-stat-lbl">${lbl}</span>
      <span class="etf-stat-val ${vCls || ''}">${val}</span>
      <span class="tao-stat-why">${sub || ''}</span>
    </div>`;
  const tiles = [];
  if (fc.today != null) {
    let sub = '';
    if (fc.prev_24h != null) {
      if (fc.today > 0 && fc.prev_24h > 0)      sub = `×${(fc.today / fc.prev_24h).toFixed(1)} vs prev 24h (${fmtT(fc.prev_24h)})`;
      else if (fc.today > 0 && fc.prev_24h <= 0) sub = `flipped + — prev 24h was ${fmtT(fc.prev_24h)}`;
      else if (fc.today <= 0 && fc.prev_24h > 0) sub = `flipped − — prev 24h was ${fmtT(fc.prev_24h)}`;
      else                                       sub = `prev 24h ${fmtT(fc.prev_24h)}`;
      if (fc.prev_est) sub += ' (est.)';
    }
    tiles.push(tile('Today (24h)', fmtT(fc.today), cls(fc.today), sub));
  }
  if (fc.d7_total != null)
    tiles.push(tile('7 days', fmtT(fc.d7_total), cls(fc.d7_total),
      `avg ${fmtT(fc.d7_daily_avg)}/day${fc.today != null && fc.d7_daily_avg ? ` · today ×${(fc.today / fc.d7_daily_avg).toFixed(1)}` : ''}`));
  if (fc.d30_total != null)
    tiles.push(tile('30 days', fmtT(fc.d30_total), cls(fc.d30_total),
      `avg ${fmtT(fc.d30_daily_avg)}/day`));

  // Daily bars — same visual language as the BTC ETF flow card
  let chart = '';
  if (daily.length >= 2) {
    const mx = Math.max(...daily.map(d => Math.abs(d.net))) || 1;
    const bars = daily.map(d => {
      const h = Math.max(6, Math.round(Math.abs(d.net) / mx * 100));
      return `<div class="etf-bar-wrap" title="${d.date}: ${fmtT(d.net)}">
        <div class="etf-bar ${d.net >= 0 ? 'etf-bar-in' : 'etf-bar-out'}" style="height:${h}%"></div>
      </div>`;
    }).join('');
    chart = `<div class="etf-barchart" style="margin-top:10px">${bars}</div>
      <div class="etf-bar-legend"><span>${daily[0].date}</span>
        <span>net TAO/day · ${daily.length} days of history</span>
        <span>${daily[daily.length - 1].date}</span></div>`;
  } else {
    chart = `<div class="tao-stat-why" style="margin-top:8px">Daily history builds up as Taostats
      snapshots accumulate — bars appear once ≥2 days are available.</div>`;
  }

  grid.innerHTML = `
    <div class="etf-stats" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">${tiles.join('')}</div>
    ${chart}`;
}

function renderTaoEco(eco, symbol) {
  const sec  = document.getElementById('taoEcoSection');
  const grid = document.getElementById('taoEcoGrid');
  if (!sec || !grid) return;
  if (symbol !== 'TAO') { sec.style.display = 'none'; return; }
  sec.style.display = '';
  if (!eco) {
    grid.innerHTML = '<div class="etf-unavailable">Ecosystem data temporarily unavailable — Taostats fetch retrying (rate limit is 5 calls/min; data refreshes within a few minutes)</div>';
    return;
  }

  const st = eco.stats || {}, fl = eco.flow || {}, sn = eco.subnets || {};
  const fmtTao = v => v == null ? '—' :
    `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v) >= 1e6 ? (Math.abs(v)/1e6).toFixed(2)+'M' : Math.abs(v).toLocaleString()} τ`;
  const flCls = v => v == null ? '' : v > 0 ? 'bull' : v < 0 ? 'bear' : '';

  // Each tile carries a one-line explanation so the parameters are
  // self-documenting (mobile has no hover tooltips).
  const tile = (label, val, cls, why) => `
    <div class="etf-stat tao-stat">
      <span class="etf-stat-lbl">${label}</span>
      <span class="etf-stat-val ${cls || ''}">${val}</span>
      <span class="tao-stat-why">${why}</span>
    </div>`;

  const tiles = [];
  if (st.staked_pct != null)
    tiles.push(tile('Supply staked', `${st.staked_pct}%`, '',
      'Share of all TAO locked in staking — high % = thin liquid float, moves amplify both ways'));
  if (st.alpha_share_pct != null)
    tiles.push(tile('Stake in Alphas vs Root', `${st.alpha_share_pct}%`, '',
      'Of staked TAO, how much is in subnet Alpha pools vs parked on root — rising = dTAO adoption, real subnet conviction'));
  if (fl.net_24h_tao != null)
    tiles.push(tile('Subnet pool flow 24h', fmtTao(fl.net_24h_tao), flCls(fl.net_24h_tao),
      'Net TAO entering (+) or leaving (−) Alpha pools today — the ETF-flow equivalent for TAO'));
  if (fl.net_7d_tao != null)
    tiles.push(tile('Subnet pool flow 7d', fmtTao(fl.net_7d_tao), flCls(fl.net_7d_tao),
      'Weekly net: inflow locks supply (bullish), outflow = unstaking headed for exchanges (bearish)'));
  if (sn.count != null)
    tiles.push(tile('Active subnets', sn.count, '',
      'Subnets competing for emissions — ecosystem size'));
  if (sn.breadth_pct != null)
    tiles.push(tile('Alpha breadth (7d up)', `${sn.breadth_pct}%`,
      sn.breadth_pct >= 60 ? 'bull' : sn.breadth_pct <= 30 ? 'bear' : '',
      '% of subnet tokens up this week — ≥60% = broad demand, ≤30% = narrow market, few winners'));
  if (sn.top5_emission_pct != null)
    tiles.push(tile('Top-5 emission share', `${sn.top5_emission_pct}%`, '',
      'How concentrated rewards are — high = winner-take-most, watch the leaders'));

  // 🏆 Subnet inflow leaders + Σ flow momentum (today vs prev 24h / 7d / 30d pace)
  let leadersBox = '';
  const ld = eco.flow_leaders;
  const fc = eco.flow_cmp || {};
  if ((ld && (ld.h24 || ld.d7 || ld.d30)) || fc.today != null) {
    // Per-window Σ total + comparison chip
    const sigma = (win) => {
      if (win === 'h24' && fc.today != null) {
        let cmpTxt = '';
        if (fc.prev_24h != null) {
          if (fc.prev_24h > 0 && fc.today > 0) {
            const r = fc.today / fc.prev_24h;
            cmpTxt = r >= 1.15 ? `×${r.toFixed(1)} vs prev 24h ↑` : r <= 0.85 ? `×${r.toFixed(1)} vs prev 24h ↓` : '≈ prev 24h';
          } else if (fc.today > 0 && fc.prev_24h <= 0) cmpTxt = 'flipped + (prev 24h was outflow)';
          else if (fc.today <= 0 && fc.prev_24h > 0)  cmpTxt = 'flipped − (prev 24h was inflow)';
          else cmpTxt = `prev 24h ${fmtTao(fc.prev_24h)}`;
          cmpTxt += fc.prev_est ? ' ~' : '';
        }
        return `<span class="tao-lead-sigma ${flCls(fc.today)}">Σ ${fmtTao(fc.today)}</span>${cmpTxt ? `<span class="sn-chip">${cmpTxt}</span>` : ''}`;
      }
      if (win === 'd7' && fc.d7_total != null)
        return `<span class="tao-lead-sigma ${flCls(fc.d7_total)}">Σ ${fmtTao(fc.d7_total)}</span><span class="sn-chip">avg ${fmtTao(fc.d7_daily_avg)}/day${fc.today != null && fc.d7_daily_avg > 0 && fc.today > 0 ? ` · today ×${(fc.today / fc.d7_daily_avg).toFixed(1)} the pace` : ''}</span>`;
      if (win === 'd30' && fc.d30_total != null)
        return `<span class="tao-lead-sigma ${flCls(fc.d30_total)}">Σ ${fmtTao(fc.d30_total)}</span><span class="sn-chip">avg ${fmtTao(fc.d30_daily_avg)}/day</span>`;
      return '';
    };
    const rowL = (lbl, win, L) => {
      const sig = sigma(win);
      if (!L && !sig) return '';
      const t = L && L.top && L.top[0];
      const chips = (L && L.top || []).slice(1)
        .map(x => `<span class="sn-chip">SN${x.netuid} ${x.name} ${fmtTao(x.flow)}</span>`).join('');
      const outc = L && L.out
        ? `<span class="sn-chip bear">top outflow: SN${L.out.netuid} ${L.out.name} ${fmtTao(L.out.flow)}</span>` : '';
      return `<div class="tao-lead-row">
        <span class="tao-lead-win">${lbl}</span>
        ${sig}
        ${t ? `<span class="tao-lead-main bull">top: ${fmtTao(t.flow)} → SN${t.netuid} ${t.name}</span>` : ''}
        <span class="tao-lead-chips">${chips}${outc}</span>
      </div>`;
    };
    leadersBox = `<div class="tao-leaders">
      <div class="smc-header">🏆 SUBNET FLOWS — Σ totals, momentum & where the TAO went</div>
      ${rowL('24H', 'h24', ld && ld.h24)}${rowL('7D', 'd7', ld && ld.d7)}${rowL('30D', 'd30', ld && ld.d30)}
      ${ld && ld.basis_24h && ld.basis_24h !== 'api'
        ? `<div class="tao-stat-why">${String(ld.basis_24h).startsWith('snapshot')
            ? `24h figures estimated from an in-app pool snapshot (${ld.basis_24h}) — approximate`
            : `24h per-subnet figures from AMM buy−sell swap volume (net TAO swapped into each pool)`}</div>` : ''}
      ${fc.prev_est ? `<div class="tao-stat-why">"prev 24h" ~ estimated as the average of the prior 6 days</div>` : ''}
    </div>`;
  }

  // Subnet table — full emission-sorted list; top 10 shown, rest expandable
  let table = '';
  if (sn.top && sn.top.length) {
    const row = s => {
      const c7 = s.chg_7d, c1 = s.chg_1d;
      const cCls  = c7 == null ? '' : c7 > 0 ? 'bull' : 'bear';
      const c1Cls = c1 == null ? '' : c1 > 0 ? 'bull' : 'bear';
      return `<tr>
        <td>SN${s.netuid}</td>
        <td>${s.name || '—'}</td>
        <td>${s.alpha_price_tao != null ? s.alpha_price_tao.toFixed(4) + ' τ' : '—'}</td>
        <td>${s.emission_share_pct != null ? s.emission_share_pct + '%' : '—'}</td>
        <td class="${c1Cls}">${c1 != null ? (c1 > 0 ? '+' : '') + c1.toFixed(1) + '%' : '—'}</td>
        <td class="${cCls}">${c7 != null ? (c7 > 0 ? '+' : '') + c7.toFixed(1) + '%' : '—'}</td>
      </tr>`;
    };
    const head = sn.top.slice(0, 10).map(row).join('');
    const rest = sn.top.slice(10).map(row).join('');
    table = `<div style="overflow-x:auto;margin-top:10px">
      <table class="sn-table">
        <thead><tr><th>ID</th><th>Subnet</th><th>Alpha price</th><th>Emission</th><th>1d</th><th>7d</th></tr></thead>
        <tbody>${head}</tbody>
        ${rest ? `<tbody id="snTableRest" style="display:none">${rest}</tbody>` : ''}
      </table></div>
      ${rest ? `<button class="sn-showall" id="snShowAllBtn"
        onclick="const r=document.getElementById('snTableRest');const open=r.style.display==='none';
                 r.style.display=open?'':'none';
                 this.textContent=open?'Hide — show top 10 only':'Show all ${sn.top.length} subnets by emission ▾';">
        Show all ${sn.top.length} subnets by emission ▾</button>` : ''}`;
  }

  if (!table) {
    table = `<div class="etf-unavailable" style="padding:10px 0">Subnet emission table refreshing —
      Taostats rate limit (5 calls/min) hit; retries automatically within ~3 minutes</div>`;
  }

  const notes = (eco.notes || [])
    .map(n => (n && typeof n === 'object') ? n.text : n)
    .filter(Boolean).join(' · ');
  grid.innerHTML = `
    <div class="etf-stats" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">${tiles.join('')}</div>
    ${leadersBox}
    ${table}
    ${notes ? `<div class="macro-reason" style="margin-top:8px">${notes}</div>` : ''}
    <div class="tao-explainer">The dTAO loop: buying a subnet's Alpha deposits TAO into its pool
    (locked supply); emissions follow Alpha prices, so the table shows where the market is voting.
    Pool inflow + broad breadth + rising Alpha share = ecosystem demand pulling TAO off exchanges —
    the bullish setup. Outflow + narrow breadth = rotation/de-risking.</div>`;
}

/* ─── Traditional markets + regime strip ──────────────────────────────────── */
function renderMarketContext(markets, regime) {
  // Traditional-markets pills (DXY / 10Y / SPX) stay in the Macro card —
  // they ARE macro. Crypto-structure pills get their own card below.
  const el = document.getElementById('marketsStrip');
  if (el) {
    const pills = (markets?.markets || []).map(m => {
      const cls = m.impact === 'bullish' ? 'bull' : m.impact === 'bearish' ? 'bear' : '';
      const arr = m.trend === 'up' ? '↑' : m.trend === 'down' ? '↓' : '→';
      return `<span class="ctx-pill ${cls}" title="${m.reason}">${m.label} ${m.value} ${arr}</span>`;
    });
    el.innerHTML = pills.join('');
    el.style.display = pills.length ? '' : 'none';
  }

  // 🌊 Crypto Market Regime card: BTC.D, rotation, stables, OI split
  const sec   = document.getElementById('cryptoRegimeSection');
  const strip = document.getElementById('cryptoRegimeStrip');
  if (!sec || !strip) return;
  const pills = [];
  if (regime) {
    if (regime.btc_dominance != null)
      pills.push(`<span class="ctx-pill" title="Bitcoin market-cap dominance">BTC.D ${regime.btc_dominance}%</span>`);
    const rCls = regime.regime === 'altseason' ? 'bull' : regime.regime === 'btc-led' ? 'bear' : '';
    pills.push(`<span class="ctx-pill ${rCls}" title="${regime.regime_note || ''}">Rotation: ${regime.regime}${regime.alt_spread_7d != null ? ` (${regime.alt_spread_7d > 0 ? '+' : ''}${regime.alt_spread_7d}pp)` : ''}</span>`);
    if (regime.stable_30d_pct != null) {
      const sCls = regime.stable_30d_pct >= 2 ? 'bull' : regime.stable_30d_pct <= -1 ? 'bear' : '';
      pills.push(`<span class="ctx-pill ${sCls}" title="USDT market-cap change over 30d — crypto liquidity proxy">Stables ${regime.stable_30d_pct > 0 ? '+' : ''}${regime.stable_30d_pct}%/30d</span>`);
    }
    const oi = regime.oi;
    if (oi && oi.alt_btc_ratio != null) {
      const oCls = oi.zone === 'room-to-run' ? 'bull' : (oi.zone === 'alt-froth' || oi.zone === 'heating') ? 'bear' : '';
      const oLbl = oi.zone === 'alt-froth' ? 'exit alts' : oi.zone === 'heating' ? 'heating' :
                   oi.zone === 'room-to-run' ? 'room to run' : 'balanced';
      pills.push(`<span class="ctx-pill ${oCls}" title="${oi.note || ''} · BTC OI $${oi.btc_oi_b}B · ETH $${oi.eth_oi_b}B · ALTs $${oi.alt_oi_b}B (OKX perps)">ALT/BTC OI ${oi.alt_btc_ratio} — ${oLbl}</span>`);
    }
  }
  strip.innerHTML = pills.join('');
  sec.style.display = pills.length ? '' : 'none';

  // Rolled-up verdict for alt positions + header sub
  const sumEl = document.getElementById('cryptoRegimeSummary');
  const subEl = document.getElementById('cryptoRegimeSub');
  const s = regime?.summary;
  if (sumEl && s) {
    const bCls = s.bias === 'alt-friendly' ? 'bull' : s.bias === 'alt-hostile' ? 'bear' : '';
    sumEl.style.display = '';
    sumEl.innerHTML = `Alt bias: <span class="${bCls}" style="font-weight:700">${s.bias.toUpperCase()}</span>` +
      `${s.alt_pts ? ` (${s.alt_pts > 0 ? '+' : ''}${s.alt_pts} pts to alt signals)` : ''}` +
      `${s.liq_pts ? ` · liquidity ${s.liq_pts > 0 ? '+' : ''}${s.liq_pts} (all tokens)` : ''}` +
      ` — ${s.text}`;
    if (subEl) subEl.textContent = `Net: ${s.bias.toUpperCase()} for alts`;
  } else if (sumEl) {
    sumEl.style.display = 'none';
  }
}

/* ─── Upcoming economic events ────────────────────────────────────────────── */
async function loadCalendar() {
  const el = document.getElementById('calendarStrip');
  if (!el) return;
  try {
    const res = await fetch(`${API}/calendar`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const evs = data.events || [];
    if (!evs.length) { el.style.display = 'none'; return; }
    el.innerHTML = evs.slice(0, 4).map(e => {
      const soon = e.days_away <= 2 && !e.released;
      const when = e.released ? 'released today'
                 : e.days_away === 0 ? 'TODAY'
                 : e.days_away === 1 ? 'tomorrow'
                 : `in ${e.days_away}d`;
      const icon = e.released ? '✅' : '⏳';
      return `<span class="ctx-pill cal ${soon ? 'cal-soon' : ''}" title="${e.date}">${icon} ${e.name} — ${when}</span>`;
    }).join('');
    el.style.display = '';
  } catch (_) { el.style.display = 'none'; }
}

/* ─── Signal outcome tracker (this device) ────────────────────────────────── */
const SIGLOG_KEY = 'signal_log_v1';
const _sigLog = {
  read()  { try { return JSON.parse(localStorage.getItem(SIGLOG_KEY) || '[]'); } catch (_) { return []; } },
  write(l){ try { localStorage.setItem(SIGLOG_KEY, JSON.stringify(l)); } catch (_) {} },
};

function trackSignal(a) {
  const sig = a?.signal;
  if (!sig || sig.direction === 'NEUTRAL' || !a.candles?.length) return;
  const last = a.candles[a.candles.length - 1];
  const id = `${a.symbol}|${a.timeframe}|${last.timestamp}`;
  const log = _sigLog.read();
  if (log.some(e => e.id === id)) return;
  log.push({ id, sym: a.symbol, tf: a.timeframe, dir: sig.direction,
             tier: sig.tier, strength: sig.strength, price: last.close,
             ts: last.timestamp });
  while (log.length > 400) log.shift();
  _sigLog.write(log);
}

function evaluateSignals(a) {
  const log = _sigLog.read();
  const candles = a?.candles || [];
  let changed = false;
  log.forEach(e => {
    if (e.done || e.sym !== a.symbol || e.tf !== a.timeframe) return;
    const idx = candles.findIndex(c => c.timestamp === e.ts);
    if (idx >= 0) {
      const fwd = candles.slice(idx + 1);
      if (fwd.length >= 4) {   // outcome = 4 closed candles later
        e.ret  = (fwd[3].close / e.price - 1) * 100;
        e.win  = (e.dir === 'LONG') === (e.ret > 0);
        e.done = true; changed = true;
      }
    } else if (candles.length && candles[0].timestamp > e.ts) {
      // signal candle scrolled out of the window — settle against latest close
      e.ret  = (candles[candles.length - 1].close / e.price - 1) * 100;
      e.win  = (e.dir === 'LONG') === (e.ret > 0);
      e.done = true; changed = true;
    }
  });
  if (changed) _sigLog.write(log);
  renderSignalAccuracy(log);
}

function renderSignalAccuracy(log) {
  const sec  = document.getElementById('sigAccSection');
  const grid = document.getElementById('sigAccGrid');
  if (!sec || !grid) return;
  const done = (log || _sigLog.read()).filter(e => e.done);
  if (done.length < 3) { sec.style.display = 'none'; return; }
  sec.style.display = '';

  const tiers = ['Confirmed', 'Strong', 'Moderate', 'Weak'];
  const rows = tiers.map(t => {
    const sub = done.filter(e => e.tier === t);
    if (!sub.length) return '';
    const wins = sub.filter(e => e.win).length;
    const rate = Math.round(wins / sub.length * 100);
    const avg  = sub.reduce((s, e) => s + (e.dir === 'LONG' ? e.ret : -e.ret), 0) / sub.length;
    const cls  = rate >= 55 ? 'bull' : rate <= 45 ? 'bear' : '';
    return `<div class="sigacc-row">
      <span class="sigacc-tier">${t}</span>
      <span class="sigacc-rate ${cls}">${rate}% win</span>
      <span class="sigacc-n">${wins}/${sub.length} signals</span>
      <span class="sigacc-avg ${avg >= 0 ? 'bull' : 'bear'}">${avg >= 0 ? '+' : ''}${avg.toFixed(2)}% avg</span>
    </div>`;
  }).join('');
  const total = done.length, totWins = done.filter(e => e.win).length;
  grid.innerHTML = `${rows}
    <div class="sigacc-row sigacc-total">
      <span class="sigacc-tier">All</span>
      <span class="sigacc-rate">${Math.round(totWins / total * 100)}% win</span>
      <span class="sigacc-n">${totWins}/${total} signals</span>
      <span class="sigacc-avg"></span>
    </div>`;
}

function renderMacro(data) {
  const sec  = document.getElementById('macroSection');
  const grid = document.getElementById('macroGrid');
  const subEl = document.getElementById('macroSub');
  if (!sec || !grid) return;
  if (!data || !data.events || !data.events.length) { sec.style.display = 'none'; return; }
  sec.style.display = '';

  const s = data.summary || {};
  if (subEl) {
    const biasCls = s.bias === 'risk-on' ? 'bull' : s.bias === 'risk-off' ? 'bear' : '';
    let intraday = '';
    if (s.intraday_active && (s.intraday_drivers || []).length) {
      const d = s.intraday_drivers[0];
      const when = d.days_to_next != null && d.days_to_next >= 0
        ? (d.days_to_next <= 0 ? 'due now' : d.days_to_next === 1 ? 'in 1 day' : `in ${d.days_to_next} days`)
        : (d.days_since_release != null ? `${d.days_since_release}d ago` : 'soon');
      intraday = ` · <span class="macro-intraday-flag">⚡ ${d.label.split(' (')[0]} ${when} — impacting intraday</span>`;
    }
    subEl.innerHTML = `Net macro bias: <span class="${biasCls}">${(s.bias || 'mixed').toUpperCase()}</span> ` +
      `· ${s.bullish_count || 0} bullish / ${s.bearish_count || 0} bearish · ${data.source || 'FRED'}${intraday}`;
  }

  const impactCls = i => i === 'bullish' ? 'macro-bull' : i === 'bearish' ? 'macro-bear' : 'macro-neu';
  const impactIcon = i => i === 'bullish' ? '▲' : i === 'bearish' ? '▼' : '—';
  const arrow = d => d === 'up' ? '↑' : d === 'down' ? '↓' : '→';

  // Next-release line: exact date for scheduled series, ~estimate otherwise.
  const nextStr = e => {
    if (!e.next_release) return '';
    const d = e.days_to_next;
    let rel = '';
    if (d != null) {
      rel = d <= 0 ? 'due now' : d === 1 ? 'in 1 day' : `in ${d} days`;
    }
    const tilde = e.scheduled ? '' : '~';
    return `Next: ${tilde}${e.next_release}${rel ? ` (${rel})` : ''}`;
  };
  const immBadge = e => e.imminent
    ? `<span class="macro-imminent" title="A scheduled release is within ±1 day — it moves price even on 1H/2H/4H, so it is weighted on intraday charts">⚡ within ±1 day</span>`
    : '';

  const fmtVal = (v, unit) => {
    if (v == null) return '—';
    const sign = (unit === 'K jobs' || unit === '%') && v > 0 ? '+' : '';
    if (unit === 'K jobs') return `${sign}${Math.round(v)}K`;
    if (unit === 'K')      return `${Math.round(v)}K`;
    if (unit === 'idx')    return v.toFixed(1);
    return `${sign}${v.toFixed(2)}%`;
  };

  grid.innerHTML = data.events.map(e => `
    <div class="macro-item ${impactCls(e.impact)}${e.imminent ? ' macro-item-imminent' : ''}">
      <div class="macro-top">
        <span class="macro-label">${e.label}${e.inflection && e.fresh ? ' <span class="macro-flip" title="This release flipped direction vs the prior one — possible regime change">🔄 TURN</span>' : ''}${immBadge(e)}</span>
        <span class="macro-cadence">${e.cadence}</span>
      </div>
      <div class="macro-vals">
        <span class="macro-cur">${fmtVal(e.current, e.unit)}</span>
        <span class="macro-chg">${arrow(e.direction)} prev ${fmtVal(e.previous, e.unit)}</span>
      </div>
      <div class="macro-impact">
        <span class="macro-badge ${impactCls(e.impact)}">${impactIcon(e.impact)} ${e.impact}</span>
        <span class="macro-asof">${e.as_of ? `as of ${e.as_of}` : ''}</span>
      </div>
      <div class="macro-next">${nextStr(e)}</div>
      <div class="macro-reason">${e.reason || ''}</div>
    </div>
  `).join('');
}
