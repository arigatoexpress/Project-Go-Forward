"""
Config Loader — Reads config.yaml and provides business configuration to all modules.
"""

import os
import yaml

_config = None

def get_config():
    """Load and cache config.yaml."""
    global _config
    if _config is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
        with open(config_path, "r") as f:
            _config = yaml.safe_load(f)
    return _config

def get_business():
    """Get business section of config."""
    return get_config().get("business", {})

def get_agent_config():
    """Get agent section of config."""
    return get_config().get("agent", {})

def get_product_config():
    """Get product section of config."""
    return get_config().get("product", {})

def get_frontend_config():
    """Get frontend section of config."""
    return get_config().get("frontend", {})

def get_deployment_config():
    """Get deployment section of config."""
    return get_config().get("deployment", {})

# Convenience accessors
def business_name():
    return get_business().get("name", "My Business")

def business_address():
    return get_business().get("address", "")

def business_phone():
    return get_business().get("phone", "")

def business_hours():
    hours = get_business().get("hours", {})
    return f"{hours.get('weekday', 'Mon-Fri 9-5')}, {hours.get('saturday', '')}, {hours.get('sunday', '')}"

def agent_name():
    return get_agent_config().get("assistant_name", "AI Assistant")

def model_name():
    return get_agent_config().get("model", "gemini-2.0-flash-001")

def product_type():
    return get_product_config().get("type", "products")

def product_singular():
    return get_product_config().get("singular", "product")

def product_plural():
    return get_product_config().get("plural", "products")
