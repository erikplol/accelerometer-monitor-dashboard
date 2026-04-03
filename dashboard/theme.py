"""Shared visual theme primitives for the Dash dashboard."""

APP_INDEX_STRING = '''
<!DOCTYPE html>
<html>
<head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');

  :root {
    --bg-0: #0b111c;
    --bg-1: #0f1725;
    --panel: #121b2a;
    --panel-border: #1f2b3f;
    --text-dim: #8b96a8;
    --space-1: 8px;
    --space-2: 12px;
    --space-3: 16px;
    --radius-1: 10px;
    --radius-2: 12px;
  }

  *, *::before, *::after { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background:
      radial-gradient(circle at 18% -12%, rgba(56,139,253,0.1) 0%, rgba(56,139,253,0) 36%),
      linear-gradient(180deg, var(--bg-1) 0%, var(--bg-0) 100%);
    height: 100%; overflow: hidden;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }

  #root, #react-entry-point {
    height: 100%;
  }

  /* ── Scrollbar ──────────────────────────────────────── */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: #0f1117; }
  ::-webkit-scrollbar-thumb { background: #2a2d35; border-radius: 3px; }

  .app-shell {
    width: 100%;
    max-width: 1880px;
    margin: 0 auto;
    padding: 0;
  }

  .app-header {
    background: linear-gradient(180deg, rgba(18,28,43,0.9) 0%, rgba(16,24,36,0.9) 100%);
    border-bottom: 1px solid var(--panel-border);
  }

  .status-pill {
    padding: 4px 10px;
    border-radius: 999px;
    border: 1px solid #263249;
    background: rgba(11,17,27,0.75);
    line-height: 1.2;
  }

  .card-title {
    color: #9cb0cc;
    letter-spacing: 1px;
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    margin-bottom: 8px;
  }

  .metric-value {
    display: flex;
    align-items: baseline;
    justify-content: center;
    gap: 6px;
  }

  .metric-unit {
    color: #9aabbe;
    font-size: 0.9rem;
    padding-bottom: 4px;
  }

  /* ── Input focus ring ───────────────────────────────── */
  input[type=number]:focus {
    outline: none;
    border-color: #4f8ef7 !important;
    box-shadow: 0 0 0 3px rgba(79,142,247,0.15);
  }
  input[type=number]::-webkit-inner-spin-button,
  input[type=number]::-webkit-outer-spin-button { opacity: 0.4; }

  /* ── Button hover effects ───────────────────────────── */
  .btn-start:hover,
  .btn-stop:hover,
  .btn-pause:hover,
  .btn-download:hover { filter: brightness(1.12); transform: translateY(-1px); }
  .btn-start, .btn-stop, .btn-pause, .btn-download {
    transition: filter 0.15s, transform 0.15s;
  }

  .btn-download {
    background: #0d1117;
    color: #c9d1d9;
    border: 1px solid var(--panel-border);
  }

  .top-strip {
    gap: var(--space-2) !important;
    padding: 10px 14px 8px !important;
  }

  .card-narrow,
  .card-wide {
    background: linear-gradient(180deg, rgba(21,31,47,0.88) 0%, rgba(15,24,38,0.88) 100%) !important;
    border-color: var(--panel-border) !important;
  }

  .card-narrow {
    flex: 1 1 170px !important;
    max-width: 220px !important;
    min-height: 162px !important;
  }

  .card-wide {
    flex: 1 1 360px !important;
    min-width: 320px !important;
    min-height: 162px !important;
  }

  .log-form-row {
    display: flex;
    align-items: flex-end;
    justify-content: center;
    flex-wrap: wrap;
    gap: 10px;
    width: 100%;
    margin-top: 8px;
  }

  .log-actions,
  .download-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .input-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .log-status-row {
    width: 100%;
    margin-top: 8px;
    text-align: center;
    color: #8ea0bb;
    font-size: 0.8rem;
  }

  .graph-card-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 4px;
  }

  .graph-sub {
    color: #8ea0bb;
    font-size: 0.72rem;
    letter-spacing: 0.4px;
  }

  .download-actions { margin-top: 8px; }

  .graphs-row {
    padding: 8px 14px 12px !important;
    gap: var(--space-2) !important;
  }

  /* ── Responsive ─────────────────────────────────────── */
  @media (max-width: 1200px) {
    .top-strip { flex-wrap: wrap !important; }
    .top-strip > div.card-narrow { flex: 1 1 150px !important; min-width: 130px !important; max-width: 220px !important; }
    .top-strip > div.card-wide   { flex: 1 1 320px !important; min-width: 280px !important; }
  }
  @media (max-width: 768px) {
    .app-shell { max-width: 100%; }
    .top-strip { flex-wrap: wrap !important; }
    .top-strip > div { flex: 1 1 100% !important; max-width: 100% !important; }
    .status-pill { width: 100%; text-align: center; }
    .log-actions,
    .download-actions { width: 100%; justify-content: center; }
    .log-actions > button,
    .download-actions > button { flex: 1 1 46%; }
    .graphs-row { flex-wrap: wrap !important; overflow-y: auto !important; }
    .graphs-row > div { flex: 1 1 100% !important; min-height: 260px; }
    html, body { overflow: auto !important; height: auto !important; }
    #root > div { height: auto !important; overflow: auto !important; }
  }
</style>
</head>
<body>{%app_entry%}{%config%}{%scripts%}{%renderer%}</body>
</html>
'''

BG = '#0f1117'
CARD_BG = '#161b22'
BORDER = '#21262d'
TICK_CLR = '#8b949e'
GRID_CLR = '#21262d'
ZERO_CLR = '#30363d'

CARD = {
    'background': CARD_BG,
    'borderRadius': 12,
    'border': f'1px solid {BORDER}',
    'padding': '14px 20px',
    'boxSizing': 'border-box',
}

LABEL = {
    'color': '#6e7681',
    'fontSize': '0.78rem',
    'fontWeight': 600,
    'textTransform': 'uppercase',
    'letterSpacing': '1px',
    'marginBottom': 6,
    'flexShrink': 0,
}

INPUT = {
    'background': '#0d1117',
    'color': '#e6edf3',
    'border': f'1px solid {BORDER}',
    'borderRadius': 8,
    'padding': '7px 11px',
    'fontSize': '0.95rem',
    'width': 130,
    'outline': 'none',
    'boxSizing': 'border-box',
    'transition': 'border-color 0.2s',
}

YAXIS_VEL = dict(
    gridcolor=GRID_CLR,
    color=TICK_CLR,
    zeroline=True,
    zerolinecolor=ZERO_CLR,
    zerolinewidth=1,
    title=dict(text='m/s²', font=dict(size=10, color=TICK_CLR)),
    tickfont=dict(size=10),
    showgrid=True,
)


def light_style(active: bool, color: str, glow: str) -> dict:
    if active:
        return {
            'width': 40,
            'height': 40,
            'borderRadius': '50%',
            'backgroundColor': color,
            'boxShadow': f'0 0 0 4px rgba(255,255,255,0.06), 0 0 18px 4px {glow}',
            'transition': 'background-color 0.3s, box-shadow 0.3s',
        }
    return {
        'width': 40,
        'height': 40,
        'borderRadius': '50%',
        'backgroundColor': '#1c2128',
        'border': f'1px solid {BORDER}',
        'boxShadow': 'none',
        'transition': 'background-color 0.3s, box-shadow 0.3s',
    }
