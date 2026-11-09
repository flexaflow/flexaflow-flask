"""Development/simple production entry point (waitress). For production prefer gunicorn: see docs/DEPLOYMENT.md."""
import os

from waitress import serve

from app import app

serve(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")))
