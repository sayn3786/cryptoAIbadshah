"""Regression coverage for the public CryptoStarsSpace app branding."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_and_installable_app_use_cryptostarsspace_name():
    html = (ROOT / "dashboard/index.html").read_text()
    manifest = json.loads((ROOT / "dashboard/manifest.json").read_text())
    capacitor = json.loads((ROOT / "capacitor.config.json").read_text())

    assert "<title>CryptoStarsSpace — AI Analysis Dashboard</title>" in html
    assert '<h1 class="brand">CryptoStarsSpace</h1>' in html
    assert 'content="CryptoStarsSpace"' in html
    assert manifest["name"] == "CryptoStarsSpace"
    assert manifest["short_name"] == "CryptoStarsSpace"
    assert capacitor["appName"] == "CryptoStarsSpace"


def test_branding_does_not_change_compatibility_identifiers():
    package = json.loads((ROOT / "package.json").read_text())
    capacitor = json.loads((ROOT / "capacitor.config.json").read_text())
    dashboard = (ROOT / "dashboard/js/dashboard.js").read_text()

    assert package["name"] == "cryptoaibadshah"
    assert capacitor["appId"] == "com.cryptobadshah.app"
    assert "const TRADES_KEY = 'cryptobadshah_trades'" in dashboard
    assert "const TK_OPEN_KEY = 'cryptomonk.tracker.open'" in dashboard
    assert "const TK_SECTION_KEY = 'cryptomonk.tracker.section'" in dashboard

