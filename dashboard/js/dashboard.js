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
  renderRSICard(a.rsi);
  renderFunding(a.funding_rate);
  renderOI(a.open_interest);
  renderLiquidations(a.liquidations);
  renderMarketCap(a.market_cap);
  renderMainChart(a.candles, a.fvgs);
  renderRSIChart(a.rsi_series);
  renderCVDCharts(a.spot_cvd, a.agg_cvd || a.futures_cvd);
  renderFVGTable(a.fvgs);
  renderFlags(a.flags);
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

/* ─── Trade Management ────────────────────────────────────────────────────── */
const TF_CLOSE_RULES = {
  // candle  : close-trigger candle label
  // hold    : expected trade duration
  // check   : how often to review
  // trail   : swing unit for trailing SL after TP2
  // noFollow: how long to wait before calling the move dead
  // be1     : breakeven note after TP1
  '4H':  { candle: '4H candle',      hold: '1 – 5 days',     check: 'every 4 h',        trail: '4H swing',      noFollow: '6+ 4H candles (~1 day)',          be1: 'move SL to entry — short TF; protect quickly' },
  '8H':  { candle: '8H candle',      hold: '3 – 10 days',    check: 'every 8 h',        trail: '8H swing',      noFollow: '4+ 8H candles (~1.5 days)',        be1: 'move SL to entry (breakeven)' },
  '12H': { candle: '12H candle',     hold: '5 – 14 days',    check: 'twice daily',      trail: '12H swing',     noFollow: '3+ 12H candles (~1.5 days)',       be1: 'move SL to entry (breakeven)' },
  '1D':  { candle: 'daily candle',   hold: '1 – 4 weeks',    check: 'daily at close',   trail: 'daily candle',  noFollow: '3+ daily candles (~3 days)',        be1: 'move SL to entry (breakeven)' },
  '1W':  { candle: 'weekly candle',  hold: '1 – 3 months',   check: 'weekly at close',  trail: 'weekly candle', noFollow: '2+ weekly candles (~2 weeks)',      be1: 'move SL to entry (breakeven)' },
  '2W':  { candle: '2W candle',      hold: '2 – 6 months',   check: 'every 2 weeks',    trail: '2W candle',     noFollow: '2+ 2W candles (~1 month)',          be1: 'move SL to entry — wide TF; be patient' },
  '3W':  { candle: '3W candle',      hold: '2 – 6 months',   check: 'every 3 weeks',    trail: '3W candle',     noFollow: '2+ 3W candles (~6 weeks)',          be1: 'move SL to entry — wide TF; be patient' },
  '1M':  { candle: 'monthly candle', hold: '3 – 12 months',  check: 'monthly at close', trail: 'monthly candle',noFollow: '2+ monthly candles (~2 months)',    be1: 'move SL to entry — macro trade; hold conviction' },
};

function renderTradeManagement(a) {
  const body   = document.getElementById('tradeMgmtBody');
  const dirEl  = document.getElementById('tradeMgmtDir');
  const tfEl   = document.getElementById('tradeMgmtTf');
  if (!body) return;

  const sig = a.signal || {};
  const dir = sig.direction || 'NEUTRAL';
  const tf  = a.timeframe  || '1W';
  const rule = TF_CLOSE_RULES[tf] || TF_CLOSE_RULES['1W'];

  dirEl.textContent = dir;
  dirEl.className   = 'trade-mgmt-dir ' + dir.toLowerCase();
  tfEl.textContent  = tf;

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
            ? `Flag ${isLong ? 'breakout' : 'breakdown'} fails after ${matchFlag.consolidation_bars + 3}+ ${tf} bars`
            : `${rule.noFollow} with no follow-through`} → re-evaluate, reduce size by 50%</span>
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
    const cls       = f.direction === 'bullish' ? 'bull' : 'bear';
    const domCls    = f.dominant ? ' dominant' : '';
    const icon      = f.direction === 'bullish' ? '▲' : '▼';
    const activeBadge = f.is_active
      ? '<span class="flag-active">Active</span>' : '';
    const domBadge  = f.dominant
      ? '<span class="flag-active" style="background:rgba(245,158,11,.15);color:var(--gold)">Dominant</span>' : '';
    return `<div class="flag-item ${cls}${domCls}">
      <div class="flag-top">
        <span class="flag-name ${cls}">${icon} ${f.direction === 'bullish' ? 'Bullish' : 'Bearish'} Flag</span>
        <span class="flag-tf">${f.timeframe}</span>
        ${activeBadge}${domBadge}
      </div>
      <div class="flag-stats">
        <span class="flag-stat">Pole <span>${f.direction === 'bullish' ? '+' : ''}${f.pole_pct}%</span></span>
        <span class="flag-stat">Retrace <span>${f.retrace_pct}%</span></span>
        <span class="flag-stat">Bars <span>${f.consolidation_bars}</span></span>
        <span class="flag-stat">Strength <span>${f.strength}</span></span>
      </div>
      <div class="flag-target">Target: <span>$${p(f.target)}</span>
        &nbsp;·&nbsp; Flag zone $${p(f.flag_low)} – $${p(f.flag_high)}
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
  loadTicker();
  loadAnalysis();

  // Auto-refresh every 5 minutes
  setInterval(loadTicker, 5 * 60 * 1000);
});
