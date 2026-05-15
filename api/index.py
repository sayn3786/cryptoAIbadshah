"""
Vercel serverless entry point.
Adds backend/ to sys.path so app.py and its imports resolve correctly,
then re-exports the Flask app so Vercel's Python runtime can find it.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import app  # noqa: F401  — Vercel detects Flask apps by this name
