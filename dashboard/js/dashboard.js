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
};

const API = location.port === '' || location.port === '80' || location.port === '443'
  ? '/api'
  : `${location.protocol}//${location.hostname}:8000/api`;

/* ─── Formatting helpers ──────────────────────────────────────────────────── */
const fmt = (v, d = 4) => v == null ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
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
  try {
    const res = await fetch(`${API}/dashboard`);
    if (!res.ok) return;
    const data = await res.json();
    const bar = document.getElementById('tickerBar');
    bar.innerHTML = Object.entries(data).map(([sym, d]) => {
      if (d.error) return '';
      const chg = d.change_pct ?? 0;
      const cls = chg >= 0 ? 'up' : 'dn';
      return `<div class="ticker-item">
        <span class="ticker-sym">${sym}</span>
        <span class="ticker-price">$${Number(d.price || 0).toLocaleString('en-US', { maximumFractionDigits: 2 })}</span>
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
  renderEMACard(a.ema_trend);
  renderLSCard(a.long_short);
  renderFNGCard(a.fear_greed);
  renderRSICard(a.rsi);
  renderFunding(a.funding_rate);
  renderOI(a.open_interest);
  renderLiquidations(a.liquidations);
  renderMarketCap(a.market_cap);
  renderMainChart(a.candles, a.fvgs);
  renderRSIChart(a.rsi_series);
  renderCVDCharts(a.spot_cvd, a.agg_cvd || a.futures_cvd);
  renderCVDDivergence(a.cvd_divergence);
  renderFVGTable(a.fvgs);
  renderFlags(a.flags);
  renderEngulfing(a.engulfing, a.timeframe);
  renderTradeManagement(a);
  renderElliottWave(a.elliott_wave);
  renderConfluence(a.signal);
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
  document.getElementById('priceValue').textContent = `$${last.close.toLocaleString('en-US', { maximumFractionDigits: 4 })}`;
  const chgEl = document.getElementById('priceChange');
  chgEl.textContent = `${up ? '▲' : '▼'} ${pct(chg)}`;
  chgEl.className = `price-change ${up ? 'up' : 'dn'}`;
  document.getElementById('priceHigh').textContent = `H: $${last.high.toLocaleString('en-US', { maximumFractionDigits: 4 })}`;
  document.getElementById('priceLow').textContent  = `L: $${last.low.toLocaleString('en-US',  { maximumFractionDigits: 4 })}`;
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
  const W = canvas.width, H = canvas.height;
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

  const data = candles.map(c => ({
    time: Math.floor(c.timestamp / 1000),
    open: c.open, high: c.high, low: c.low, close: c.close,
  }));
  // Deduplicate by time (just in case)
  const unique = [...new Map(data.map(d => [d.time, d])).values()].sort((a, b) => a.time - b.time);
  S.candleSeries.setData(unique);

  // FVG overlays as price lines
  if (fvgs?.length && S.mainChart) {
    const unfilled = fvgs.filter(f => !f.filled).slice(0, 8);
    unfilled.forEach(f => {
      const color = f.type === 'bullish' ? 'rgba(16,185,129,0.5)' : 'rgba(239,68,68,0.5)';
      S.candleSeries.createPriceLine({ price: f.midpoint, color, lineWidth: 1, lineStyle: 3,
        title: `${f.type === 'bullish' ? '↑' : '↓'} FVG ${f.size_pct.toFixed(2)}%` });
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
function renderCVDCharts(spot, fut) {
  renderCVDPanel('spot', spot, S.spotCvdSeries, 'spotCvdVal', 'spotCvdTrend');
  renderCVDPanel('fut', fut, S.futCvdSeries, 'futCvdVal', 'futCvdTrend');
}

function renderCVDDivergence(div) {
  const el = document.getElementById('cvdDivBanner');
  if (!el) return;
  if (!div || !div.type || div.type === 'neutral') { el.style.display = 'none'; return; }

  const icons = {
    futures_led_up:   '⚠',
    spot_led_up:      '✓',
    confirmed_up:     '✓✓',
    futures_led_down: '⚠',
    spot_led_down:    '↓',
    confirmed_down:   '↓↓',
  };
  const sigCls = div.signal === 'bullish' ? 'bull' : div.signal === 'bearish' ? 'bear' : '';
  el.style.display = '';
  el.className = `cvd-div-banner cvd-div-${div.signal}`;
  el.innerHTML = `
    <span class="cvd-div-icon">${icons[div.type] || '·'}</span>
    <span class="cvd-div-label ${sigCls}">${div.label}</span>
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
  const W = canvas.width, H = canvas.height;
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
  const fmt     = v => v != null ? '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }) : '—';
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

  const isLong = dir === 'LONG';
  const entry  = sig.entry;
  const sl     = sig.sl;
  const tps    = sig.tp_targets || [];
  const rr     = sig.rr_ratio;

  // Best active flag for this direction
  const matchFlag = (a.flags || []).find(f =>
    f.is_active && f.direction === (isLong ? 'bullish' : 'bearish')
  );

  const p  = (v, d = 2) => v != null ? '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: d }) : '—';
  const pct = (a, b)    => b ? ((a - b) / b * 100).toFixed(1) + '%' : '';
  const pctHtml = (v, ref, good) => {
    const s = pct(v, ref);
    const cls = good ? 'bull' : 'bear';
    return s ? `<span class="tm-pct ${cls}">${s}</span>` : '';
  };

  // Close-trigger price: flag_low for long, flag_high for short (or fallback to sl)
  const triggerPrice = isLong
    ? (matchFlag ? matchFlag.flag_low  : sl)
    : (matchFlag ? matchFlag.flag_high : sl);

  const flagTarget = matchFlag ? matchFlag.target : null;

  const levelsHTML = `
    <div class="tm-col">
      <div class="tm-section-title">Levels</div>
      <div class="tm-row">
        <span class="tm-label">Entry</span>
        <span class="tm-val">${p(entry)}</span>
      </div>
      <div class="tm-row">
        <span class="tm-label">Stop Loss</span>
        <span class="tm-val bear">${p(sl)} ${pctHtml(sl, entry, false)}</span>
      </div>
      <div class="tm-divider"></div>
      <div class="tm-row">
        <span class="tm-label">TP 1 <span style="color:var(--muted);font-size:.68rem">— sell 50%</span></span>
        <span class="tm-val bull">${p(tps[0])} ${pctHtml(tps[0], entry, true)}</span>
      </div>
      <div class="tm-row">
        <span class="tm-label">TP 2 <span style="color:var(--muted);font-size:.68rem">— sell 30%</span></span>
        <span class="tm-val bull">${p(tps[1])} ${pctHtml(tps[1], entry, true)}</span>
      </div>
      <div class="tm-row">
        <span class="tm-label">TP 3 <span style="color:var(--muted);font-size:.68rem">— sell 20%</span></span>
        <span class="tm-val bull">${p(tps[2])} ${pctHtml(tps[2], entry, true)}</span>
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
          <span><strong>${rule.candle}</strong> closes ${isLong ? 'below' : 'above'} <strong>${p(triggerPrice)}</strong>${matchFlag ? ' (back inside flag)' : ' (stop loss)'} → full exit</span>
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
  const fp     = v => v != null ? '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }) : '—';

  // Active flag matching the signal direction — same logic as renderTradeManagement
  const matchFlag = (a.flags || []).find(f =>
    f.is_active && f.direction === (isLong ? 'bullish' : 'bearish')
  );
  const triggerPrice = isLong
    ? (matchFlag ? matchFlag.flag_low  : sig.sl)
    : (matchFlag ? matchFlag.flag_high : sig.sl);

  const exit_rules = {
    rule1: `Hit TP1 → close 50%, ${rule.be1} at ${fp(sig.entry)}`,
    rule2: `Hit TP2 → close 30%, trail remaining SL ${isLong ? 'below each new higher' : 'above each new lower'} ${rule.trail} ${isLong ? 'low' : 'high'}`,
    rule3: `${rule.candle} closes ${isLong ? 'below' : 'above'} ${fp(triggerPrice)}${matchFlag ? ' (back inside flag)' : ' (stop loss)'} → full exit`,
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

  const fmt  = v => v != null ? '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }) : '—';
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
  const fmt = v => '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });

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
        <span class="engulf-stat">Prev candle <span>${fmt(p.prev_open)} → ${fmt(p.prev_close)}</span></span>
        <span class="engulf-stat">Engulf candle <span>${fmt(p.engulf_open)} → ${fmt(p.engulf_close)}</span></span>
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

  const tEl = document.getElementById('waveTargets');
  const bias = e.bias || 'neutral';
  const targets = (e.targets || []);
  if (!targets.length) {
    tEl.innerHTML = `<span style="color:var(--muted);font-size:.82rem">No targets ahead of current price</span>`;
  } else {
    tEl.innerHTML = targets.map((t, i) =>
      `<div class="wave-target ${bias}">Target ${i + 1}: $${Number(t).toLocaleString('en-US', { maximumFractionDigits: 4 })}</div>`
    ).join('');
  }
}

/* ─── Confluence lists ────────────────────────────────────────────────────── */
function renderConfluence(s) {
  if (!s) return;
  const bullEl = document.getElementById('bullList');
  const bearEl = document.getElementById('bearList');
  const li = (txt) => `<li>${txt}</li>`;
  bullEl.innerHTML = (s.bullish_reasons?.length ? s.bullish_reasons : ['No bullish confluence']).map(li).join('');
  bearEl.innerHTML = (s.bearish_reasons?.length ? s.bearish_reasons : ['No bearish confluence']).map(li).join('');
}

/* ─── Journal generation ──────────────────────────────────────────────────── */
async function generateJournal() {
  const btn = document.getElementById('generateBtn');
  const loading = document.getElementById('journalLoading');
  const output = document.getElementById('journalOutput');

  btn.disabled = true;
  loading.classList.remove('hidden');
  output.classList.add('hidden');

  try {
    const res = await fetch(`${API}/journal/${S.symbol}?timeframe=${S.timeframe}`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    S.journalData = data.journal;
    renderJournal(data.journal);
    output.classList.remove('hidden');
  } catch (e) {
    alert('Journal generation failed: ' + e.message);
  } finally {
    btn.disabled = false;
    loading.classList.add('hidden');
  }
}

function renderJournal(j) {
  if (!j) return;
  document.getElementById('journalMeta').textContent =
    `Generated: ${new Date(j.generated_at).toLocaleString()} · Model: ${j.model}`;

  const fmt = (txt) => txt?.replace(/##\s(.+)/g, '<h2>$1</h2>').replace(/###\s(.+)/g, '<h3>$1</h3>') || '—';
  document.getElementById('journalScript').innerHTML = fmt(j.script);
  document.getElementById('journalTitle').innerHTML  = fmt(j.title);
  document.getElementById('journalDesc').innerHTML   = fmt(j.description);
}

function showJTab(btn, tab) {
  document.querySelectorAll('.jtab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  ['journalScript', 'journalTitle', 'journalDesc'].forEach(id => {
    document.getElementById(id).classList.add('hidden');
  });
  const map = { script: 'journalScript', title: 'journalTitle', desc: 'journalDesc' };
  document.getElementById(map[tab]).classList.remove('hidden');
}

function copyJournal() {
  const active = document.querySelector('.journal-content:not(.hidden)');
  if (!active) return;
  navigator.clipboard.writeText(active.innerText).then(() => {
    const btn = document.querySelector('.copy-btn');
    btn.textContent = '✅ Copied!';
    setTimeout(() => { btn.textContent = '📋 Copy to Clipboard'; }, 2000);
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
    const msg = '<p class="empty">Order book data unavailable — no supported exchange has depth data for this asset</p>';
    buyEl.innerHTML = sellEl.innerHTML = msg;
    if (buySrc)  buySrc.textContent  = 'Unavailable';
    if (sellSrc) sellSrc.textContent = 'Unavailable';
    return;
  }

  const srcLabel = ob.source ? ob.source.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Live Order Book';
  if (buySrc)  buySrc.textContent  = srcLabel;
  if (sellSrc) sellSrc.textContent = srcLabel;

  const ratio = ob.bid_ask_ratio || 1;
  const ratioLabel = ratio > 1.2 ? '🟢 Bid-heavy (bullish pressure)' :
                     ratio < 0.8 ? '🔴 Ask-heavy (selling pressure)' :
                     '⚪ Balanced';

  const mcapStr = ob.market_cap
    ? ob.market_cap >= 1e12 ? `$${(ob.market_cap/1e12).toFixed(2)}T`
    : ob.market_cap >= 1e9  ? `$${(ob.market_cap/1e9).toFixed(1)}B`
    : `$${(ob.market_cap/1e6).toFixed(0)}M`
    : null;

  function wallHTML(w, kind) {
    const dist = w.distance_pct;
    const distStr = dist === 0 ? 'at market' :
                    dist > 0   ? `+${dist.toFixed(3)}% above` :
                                 `${dist.toFixed(3)}% below`;
    const usdVal = w.usd_value >= 1e9 ? `$${(w.usd_value/1e9).toFixed(2)}B`
                 : w.usd_value >= 1e6 ? `$${(w.usd_value/1e6).toFixed(2)}M`
                 : `$${(w.usd_value/1e3).toFixed(1)}K`;

    const sigColors = { high: 'var(--bull)', medium: '#f59e0b', low: 'var(--muted)' };
    const sigLabels = { high: '⚡ High impact', medium: '〰 Medium impact', low: '· Low impact' };
    const sigColor  = sigColors[w.significance] || 'var(--muted)';
    const sigLabel  = sigLabels[w.significance] || '—';

    const mcapRow = w.mcap_pct != null ? `
      <div class="spike-row">
        <span class="spike-label">Market Cap impact</span>
        <span class="spike-val" style="color:${sigColor}">${w.mcap_pct.toFixed(4)}% &nbsp;${sigLabel}</span>
      </div>` : '';
    const mcapRefRow = mcapStr ? `
      <div class="spike-row">
        <span class="spike-label">Market Cap</span>
        <span class="spike-val" style="color:var(--muted)">${mcapStr}</span>
      </div>` : '';

    return `
      <div class="spike-ratio ${kind}">${usdVal}</div>
      <div class="spike-row"><span class="spike-label">Price Level</span><span class="spike-val">$${w.price.toLocaleString('en-US', {maximumFractionDigits: 2})}</span></div>
      <div class="spike-row"><span class="spike-label">Qty (coins)</span><span class="spike-val">${w.qty.toLocaleString('en-US', {maximumFractionDigits: 4})}</span></div>
      <div class="spike-row"><span class="spike-label">Distance</span><span class="spike-val">${distStr}</span></div>
      ${mcapRow}${mcapRefRow}
    `;
  }

  buyEl.innerHTML  = wallHTML(ob.biggest_bid, 'buy')  + `<div class="spike-row" style="margin-top:6px;padding-top:6px;border-top:1px solid var(--border)"><span class="spike-label">±2% Imbalance</span><span class="spike-val" style="font-size:.8rem">${ratioLabel}</span></div>`;
  sellEl.innerHTML = wallHTML(ob.biggest_ask, 'sell') + `<div class="spike-row" style="margin-top:6px;padding-top:6px;border-top:1px solid var(--border)"><span class="spike-label">Bid vs Ask (±2%)</span><span class="spike-val">${fmtK(ob.near_bid_usd)} <span style="color:var(--muted)">vs</span> ${fmtK(ob.near_ask_usd)}</span></div>`;
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
document.addEventListener('DOMContentLoaded', () => {
  wireSelectors();
  initCharts();
  renderMyTrades();
  loadTicker();
  loadAnalysis();

  // Auto-refresh every 5 minutes
  setInterval(loadTicker, 5 * 60 * 1000);
});
