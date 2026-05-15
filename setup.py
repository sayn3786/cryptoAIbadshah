"""
Interactive first-time setup for CryptoBadshah.
Run: python setup.py
"""
import os
import sys
import subprocess
import shutil


def main():
    print("\n" + "=" * 50)
    print("  CryptoBadshah — First-Time Setup")
    print("=" * 50 + "\n")

    root = os.path.dirname(os.path.abspath(__file__))

    # ── 1. Python version check ─────────────────────────────────────────────
    if sys.version_info < (3, 10):
        print("❌  Python 3.10+ required. Current:", sys.version)
        sys.exit(1)
    print(f"✅  Python {sys.version.split()[0]}")

    # ── 2. Install requirements ─────────────────────────────────────────────
    print("\n📦  Installing Python packages...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r",
         os.path.join(root, "requirements.txt"), "-q"]
    )
    print("✅  Packages installed")

    # ── 3. .env setup ───────────────────────────────────────────────────────
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        shutil.copy(os.path.join(root, ".env.example"), env_path)

    with open(env_path) as f:
        content = f.read()

    has_key = "sk-ant-" in content and "your-key-here" not in content

    print("\n🔑  Anthropic API Key (for AI YouTube journal generation)")
    if has_key:
        print("✅  API key already set in .env")
    else:
        print("   Get yours free at: https://console.anthropic.com")
        key = input("   Paste key (or press Enter to skip): ").strip()
        if key.startswith("sk-ant-"):
            new_content = content.replace(
                "ANTHROPIC_API_KEY=sk-ant-your-key-here",
                f"ANTHROPIC_API_KEY={key}"
            )
            with open(env_path, "w") as f:
                f.write(new_content)
            print("✅  API key saved to .env")
        else:
            print("⚠️   Skipped — journal will use fallback template mode")

    # ── 4. Port check ───────────────────────────────────────────────────────
    import socket
    port = 8000
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        in_use = s.connect_ex(("localhost", port)) == 0
    if in_use:
        print(f"\n⚠️   Port {port} already in use — another instance may be running")
    else:
        print(f"\n✅  Port {port} is free")

    # ── 5. Launch ────────────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("  Setup complete! Launching server...")
    print("=" * 50)
    print(f"\n  Dashboard → http://localhost:{port}/dashboard/")
    print(f"  API docs  → http://localhost:{port}/docs")
    print("\n  Press Ctrl+C to stop\n")

    os.chdir(os.path.join(root, "backend"))
    from dotenv import load_dotenv
    load_dotenv(env_path)

    import uvicorn
    import importlib.util
    spec = importlib.util.spec_from_file_location("app", "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    uvicorn.run(mod.app, host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
