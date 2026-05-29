"""
Wrapper to import warp-api.py (hyphen in name -> can't use normal import).
"""
import importlib.util
import sys
import os

spec = importlib.util.spec_from_file_location(
    "warp_api",
    os.path.join(os.path.dirname(__file__), "warp-api.py"),
)
mod = importlib.util.module_from_spec(spec)
sys.modules["warp_api"] = mod
spec.loader.exec_module(mod)

# Re-export only what exists
app = mod.app
get_proxy_configs = mod.get_proxy_configs
SERVERS = mod.SERVERS
SERVERS_CNT = mod.SERVERS_CNT
