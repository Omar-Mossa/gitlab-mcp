from gitlab_ci_mcp.config import Settings
from gitlab_ci_mcp.server import create_server

mcp = create_server(Settings.from_env())
