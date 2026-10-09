"""search-first: an MCP server that looks for existing public projects before you build."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("search-first-mcp")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0+unknown"
