"""Deployment entry point for the isolated Retail or Supplier Agent image."""
from app.agent_app import create_agent_app
from app.config import settings

app = create_agent_app(settings)
