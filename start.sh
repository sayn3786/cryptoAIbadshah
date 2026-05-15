#!/usr/bin/env bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CryptoBadshah AI Analysis Platform"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "❌  Python 3 is required. Install from https://python3.org"
  exit 1
fi

# Copy .env if missing
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "📋  Created .env from .env.example — add your ANTHROPIC_API_KEY for AI journal generation"
fi

# Install dependencies
echo "📦  Installing Python dependencies…"
pip install -r requirements.txt -q

# Launch backend
echo "🚀  Starting server…"
cd backend
python3 app.py
