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
  layout: { background: { color: '#111827' }, textColor: '#94a3b8' },
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

  window.addEventListener('resize', () => {
    S.mainChart.resize(mainEl.clientWidth, mainEl.clientHeight || 380);
    S.rsiChart.resize(rsiEl.clientWidth, 120);
  });
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
  renderLSCard(a.long_short);
  renderWhaleActivity(a.whale_activity || []);
  renderFNGCard(a.fear_greed);
  renderRSICard(a.rsi);
  renderFunding(a.funding_rate);
  renderOI(a.open_interest);
  renderLiquidations(a.liquidations);
  renderMarketCap(a.market_cap);
  renderMainChart(a.candles, a.fvgs);
  renderRSIChart(a.rsi_series);
  renderCVDCharts(a.spot_cvd, a.agg_cvd || a.futures_cvd, a.futures_available);
  renderCVDDivergence(a.cvd_divergence);
  renderFVGTable(a.fvgs);
  renderFlags(a.flags);
  renderEngulfing(a.engulfing, a.timeframe);
  renderTradeManagement(a);
  renderElliottWave(a.elliott_wave);
  renderConfluence(a.signal);
  renderHtfConfluence(a);
  renderBtcContext(a);
  renderOrderBook(a.order_book);
  renderHolidayBanner(a.upcoming_holidays);
  document.getElementById('chartTitle').textContent = `${a.symbol}/USDT · ${a.timeframe}`;
}

/* ─── Price panel ─────────────────────────────────────────────────────────── */
function renderPrice(a) {
  const c = a.candles;
  if (!c?.length) return;
  const last = c[c.length - 1];
  const prev = c.length > 1 ? c[c.length - 2] : last;
  const chg = (last.close - prev.close) / prev.close * 100;
  const up = chg >= 0;

  document.getElementById('priceSymbol').textContent = `${a.symbol}/USDT`;
  document.getElementById('priceValue').textContent = fmtPrice(last.close);
  const chgEl = document.getElementById('priceChange');
  chgEl.textContent = `${up ? '▲' : '▼'} ${pct(chg)}`;
  chgEl.className = `price-change ${up ? 'up' : 'dn'}`;
  const periodEl = document.getElementById('priceChangePeriod');
  if (periodEl) periodEl.textContent = `${a.timeframe} change`;
  document.getElementById('priceHigh').textContent = `H: ${fmtPrice(last.high)}`;
  document.getElementById('priceLow').textContent  = `L: ${fmtPrice(last.low)}`;
  document.getElementById('priceVol').textContent  = `Vol: ${fmtK(last.volume)}`;
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
function renderOI(oi) {
  if (!oi) return;
  document.getElementById('oiValue').textContent = fmtK(oi.value);
  const badge = document.getElementById('oiChange');
  const chg = oi.change_pct ?? 0;
  badge.textContent = pct(chg);
  badge.className = `metric-badge ${chg >= 0 ? 'up' : 'dn'}`;

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

function renderMainChart(candles, fvgs) {
  if (!candles?.length || !S.candleSeries) return;

  // Clear FVG price lines and wave markers from the previous token/TF.
  S.fvgPriceLines.forEach(pl => { try { S.candleSeries.removePriceLine(pl); } catch (_) {} });
  S.fvgPriceLines = [];
  S.candleSeries.setMarkers([]);   // wave markers — replaced later by renderElliottWave

  // Show hours on the time axis for intraday TFs; dates only for daily+
  const intraday = ['1H', '2H', '4H', '8H', '12H'].includes(S.timeframe);
  S.mainChart.applyOptions({
    timeScale: { borderColor: '#1e2d44', timeVisible: intraday, secondsVisible: false },
  });

  const data = candles.map(c => ({
    time: Math.floor(c.timestamp / 1000),
    open: c.open, high: c.high, low: c.low, close: c.close,
  }));
  const unique = [...new Map(data.map(d => [d.time, d])).values()].sort((a, b) => a.time - b.time);
  S.candleSeries.setData(unique);

  // FVG overlays — draw after setData so Y-axis is already anchored to real prices.
  // Each FVG is shown as three lines: top boundary, midpoint (labelled), bottom boundary.
  if (fvgs?.length) {
    const unfilled = fvgs.filter(f => !f.filled).slice(0, 6);
    unfilled.forEach(f => {
      const isBull = f.type === 'bullish';
      const color  = isBull ? 'rgba(16,185,129,0.6)' : 'rgba(239,68,68,0.6)';
      const dimCol = isBull ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)';
      const arrow  = isBull ? '↑' : '↓';
      // Top boundary
      S.fvgPriceLines.push(S.candleSeries.createPriceLine({ price: f.top,      color: dimCol, lineWidth: 1, lineStyle: 2, title: '' }));
      // Midpoint — labelled
      S.fvgPriceLines.push(S.candleSeries.createPriceLine({ price: f.midpoint, color,         lineWidth: 1, lineStyle: 3, title: `${arrow} FVG ${f.size_pct.toFixed(1)}%` }));
      // Bottom boundary
      S.fvgPriceLines.push(S.candleSeries.createPriceLine({ price: f.bottom,   color: dimCol, lineWidth: 1, lineStyle: 2, title: '' }));
    });
  }

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
  const bull = div.type === 'bullish';
  typeEl.textContent = bull ? '🔼 Bullish Divergence' : '🔽 Bearish Divergence';
  typeEl.style.color = bull ? 'var(--bull)' : 'var(--bear)';
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

  const prof = mining.profitability_ratio;
  let profCls = '', profLabel = '—';
  if (prof != null) {
    if (prof >= 2.0)       { profCls = 'bull'; profLabel = `${prof}× (Very profitable)`; }
    else if (prof >= 1.3)  { profCls = 'bull'; profLabel = `${prof}× (Profitable)`; }
    else if (prof < 1.05)  { profCls = 'bear'; profLabel = `${prof}× (Near break-even!)`; }
    else                   { profCls = '';      profLabel = `${prof}×`; }
  }

  const diff = mining.difficulty_change;
  const diffStr = diff != null ? (diff >= 0 ? `+${diff.toFixed(1)}%` : `${diff.toFixed(1)}%`) : '—';
  const diffCls = diff == null ? '' : diff >= 3 ? 'bull' : diff <= -3 ? 'bear' : '';

  const be = mining.break_even_usd;
  const beStr = be != null ? `$${be.toLocaleString()}` : '—';

  const rev = mining.miner_revenue_usd;
  const revStr = rev != null ? `$${(rev / 1e6).toFixed(1)}M / day` : '—';

  rows.innerHTML = `
    <div class="btcm-row"><span class="btcm-label">Hash Ribbon</span><span class="btcm-val ${rm.cls}">${rm.icon} ${rm.label}</span></div>
    <div class="btcm-sub">${rm.desc}</div>
    <div class="btcm-row"><span class="btcm-label">Halving Phase</span><span class="btcm-val ${pm.cls}">${pm.label}</span></div>
    <div class="btcm-sub">${months} since halving · ${daysUntil} until next · ${pm.desc}</div>
    <div class="btcm-row"><span class="btcm-label">Miner Profitability</span><span class="btcm-val ${profCls}">${profLabel}</span></div>
    <div class="btcm-sub">Break-even est. ${beStr} · Revenue ${revStr}</div>
    <div class="btcm-row"><span class="btcm-label">Difficulty Change</span><span class="btcm-val ${diffCls}">${diffStr}</span></div>
    <div class="btcm-sub">Expected at next adjustment</div>
  `;
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
      if (diff < 60)   return `${Math.round(diff)}m ago`;
      if (diff < 1440) return `${Math.round(diff / 60)}h ago`;
      return `${Math.round(diff / 1440)}d ago`;
    } catch { return ''; }
  };

  list.innerHTML = articles.slice(0, 6).map(a => {
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
  }).join('');
}

function renderCVDPanel(id, cvd, series, valId, trendId) {
  if (!cvd) return;
  const el = document.getElementById(valId);
  const isFlat = !cvd.series?.some(d => Math.abs(Number(d.cvd || 0)) > 0);
  el.textContent = isFlat ? 'Estimated' : Number(cvd.current).toLocaleString('en-US', { maximumFractionDigits: 2 });
  el.style.color = cvd.trend === 'bullish' ? 'var(--bull)' : cvd.trend === 'bearish' ? 'var(--bear)' : 'var(--neutral)';

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
  document.getElementById('fvgCount').textContent = (fvgs || []).filter(f => !f.filled).length;

  if (!fvgs?.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No FVGs detected</td></tr>';
    return;
  }

  tbody.innerHTML = fvgs.slice(0, 12).map(f => {
    const cls = f.type === 'bullish' ? 'bull' : 'bear';
    const status = f.filled
      ? '<span class="tag filled">Filled</span>'
      : `<span class="tag ${cls}">${f.type}</span>`;
    return `<tr>
      <td><span class="tag ${cls}">${f.type}</span></td>
      <td>$${Number(f.top).toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
      <td>$${Number(f.bottom).toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
      <td>${f.size_pct.toFixed(3)}%</td>
      <td class="${Number(f.distance_pct) >= 0 ? 'bull' : 'bear'}">${pct(f.distance_pct)}</td>
      <td>${status}</td>
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
function renderFlags(flags) {
  const el    = document.getElementById('flagList');
  const badge = document.getElementById('flagCount');
  if (!flags?.length) {
    el.innerHTML = '<p class="empty">No flag patterns detected</p>';
    badge.textContent = '0';
    return;
  }
  badge.textContent = flags.length;
  const p = (v, d = 4) => Number(v).toLocaleString('en-US', { maximumFractionDigits: d });
  el.innerHTML = flags.map(f => {
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
    </div>`;
  }).join('');
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

  const li = (txt) => `<li>${txt}</li>`;
  bullEl.innerHTML = (s.bullish_reasons?.length ? s.bullish_reasons : ['No bullish confluence']).map(li).join('');
  bearEl.innerHTML = (s.bearish_reasons?.length ? s.bearish_reasons : ['No bearish confluence']).map(li).join('');
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
      renderCVDPanel(isSpot ? 'spot' : 'fut', cvd, series, valId, trendId);
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
    const tEl = document.getElementById(trendId);
    if (tEl) { tEl.textContent = 'error'; tEl.className = 'cvd-trend neutral'; }
    const vEl = document.getElementById(valId);
    if (vEl) vEl.textContent = '—';
    console.warn(`CVD source '${source}' failed:`, e.message);
  } finally {
    sel.classList.remove('cvd-source-loading');
  }
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

  document.getElementById('spotCvdSource').addEventListener('change', e => {
    S.spotCvdSource = e.target.value;
    loadCvdFromSource('spot');
  });

  document.getElementById('futCvdSource').addEventListener('change', e => {
    S.futCvdSource = e.target.value;
    loadCvdFromSource('futures');
  });
}

/* ─── Strength Change Monitor ─────────────────────────────────────────────── */
const ALL_TOKENS = ['BTC','ETH','LINK','SOL','XRP','TAO','HYPE','SUI','KAS','ALGO','XMR','TON','ONDO','AAVE','RENDER','BNB','BLUR','ZEC','TRX','ADA','XLM','AVAX','HBAR','QNT','INJ','FET'];
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
    const when   = a.candles_ago === 1 ? 'current candle' : `${a.candles_ago} candles ago`;
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
  return `rec26_mtf_${y}${m}${d}${h}${half}`;
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

  // Exhaustion alert banner
  const exhBanner = (() => {
    const exh = r.exhaustion_alert;
    if (!exh) return '';
    const isPump  = exh.type === 'pump';
    const icon    = isPump ? '🔴' : '🟢';
    const label   = isPump ? 'Pump Exhaustion' : 'Dump Exhaustion';
    const rocAbs  = Math.abs(exh.price_roc ?? 0).toFixed(1);
    const cls     = isPump ? 'exh-pump' : 'exh-dump';
    return `<div class="rec-exhaustion-alert ${cls}">
      🚨 <strong>${label}</strong> ${exh.signals}/7 signals (${exh.tf}) — ${icon} price ${isPump ? 'up' : 'down'} ${rocAbs}%
      <div class="exh-detail">${exh.detail}</div>
    </div>`;
  })();

  return `<div class="rec-card rec-card-${dirCls}${r.btc_conflict ? ' rec-card-conflict' : ''}" data-rec-sym="${r.symbol}">
    <div class="rec-card-top">
      <span class="rec-rank">#${i+1}</span>
      <span class="rec-sym">${r.symbol}/USDT</span>
      <span class="rec-dir ${dirCls}">${dirIcon} ${r.direction}</span>
      <span class="rec-strength">${r.display_strength ?? r.h2_strength}/100</span>
    </div>
    ${exhBanner}
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

async function loadRecommendations(force = false) {
  const section = document.getElementById('recSection');
  const cards   = document.getElementById('recCards');
  const dateEl  = document.getElementById('recDateLabel');
  const valEl   = document.getElementById('recValidity');
  if (!section || !cards) return;

  try {
    let data = force ? null : _recCacheGet();
    if (!data) {
      const url = `${API}/recommendations` + (force ? '?force=1' : '');
      const res = await fetch(url);
      data = await res.json();
      if (data.recommendations?.length) _recCacheSet(data);
    }
    if (!data.recommendations?.length) return;

    if (dateEl) dateEl.textContent = data.date_label || '';
    if (valEl && data.valid_until_fmt) {
      valEl.textContent = `Valid until ${data.valid_until_fmt}`;
    }

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
      if (p != null) el.textContent = fmtPrice(p);
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
  // Clear rec localStorage cache so fresh data (with latest leverage) is fetched
  Object.keys(localStorage).filter(k => /^rec\d*_/.test(k)).forEach(k => localStorage.removeItem(k));
  await loadAnalysis();
  await loadTicker();
  await loadRecommendations(true);
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
  setInterval(loadWhaleAlerts, 5 * 60 * 1000);

  // Auto-refresh every 5 minutes (ticker); strength check every 60 minutes
  setInterval(loadTicker, 5 * 60 * 1000);
  setInterval(checkStrengthChanges, 60 * 60 * 1000);
});
