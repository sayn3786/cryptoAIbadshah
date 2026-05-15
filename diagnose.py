"""Run this to diagnose why APIs are failing: python diagnose.py"""
import sys
import os

print(f"\nPython: {sys.version}")
print(f"Platform: {sys.platform}\n")

try:
    import requests
    print(f"requests: {requests.__version__} ✅")
except ImportError:
    print("requests: NOT INSTALLED ❌  — run: pip install requests")
    sys.exit(1)

TESTS = [
    ("Basic HTTP",   "http://httpbin.org/get"),
    ("Basic HTTPS",  "https://httpbin.org/get"),
    ("Binance ping", "https://api.binance.com/api/v3/ping"),
    ("CoinGecko",    "https://api.coingecko.com/api/v3/ping"),
    ("Kraken",       "https://api.kraken.com/0/public/Time"),
    ("Gate.io",      "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BTC_USDT"),
    ("KuCoin",       "https://api.kucoin.com/api/v1/market/candles?type=1week&symbol=BTC-USDT"),
    ("CoinGlass",    "https://open-api.coinglass.com/public/v2/open_interest?symbol=BTC"),
]

print("Testing connectivity:\n")
working = []
for name, url in TESTS:
    try:
        r = requests.get(url, timeout=10, verify=True)
        if r.status_code == 200:
            print(f"  ✅  {name:20} → HTTP {r.status_code}")
            working.append(name)
        else:
            print(f"  ⚠️  {name:20} → HTTP {r.status_code}")
    except requests.exceptions.SSLError as e:
        print(f"  ❌  {name:20} → SSL Error: {e}")
    except requests.exceptions.ConnectionError as e:
        print(f"  ❌  {name:20} → Connection refused / DNS failed")
    except requests.exceptions.Timeout:
        print(f"  ❌  {name:20} → Timed out")
    except Exception as e:
        print(f"  ❌  {name:20} → {type(e).__name__}: {e}")

print(f"\nWorking: {working or ['NONE — check your internet/firewall/VPN']}")

if working:
    print("\n✅ Internet is reachable. The app will auto-detect the best source.")
else:
    print("\n❌ No external APIs reachable.")
    print("   Possible causes:")
    print("   1. No internet connection")
    print("   2. Firewall / antivirus blocking outbound HTTPS")
    print("   3. VPN / proxy misconfiguration")
    print("   4. Corporate network restrictions")
    print("\n   Try: disable VPN, whitelist python.exe in firewall, then re-run start.bat")

input("\nPress Enter to close...")
