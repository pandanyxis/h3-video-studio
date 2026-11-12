"""Expose the Runpod Studio production queue to local MCP clients."""
import base64
import json
import os
import re
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from mcp.server.fastmcp import FastMCP


STUDIO_URL = "http://127.0.0.1:8190"
studio = build_opener(ProxyHandler({}))
mcp = FastMCP("H3 Video Studio", host="127.0.0.1", port=8191, stateless_http=True, json_response=True)


def studio_request(path, *, post=False):
    password = os.environ.get("STUDIO_PASSWORD", "")
    credentials = base64.b64encode(("studio:" + password).encode()).decode()
    headers = {"Accept": "application/json", "Authorization": "Basic " + credentials}
    if post:
        pod = os.environ.get("RUNPOD_POD_ID", "")
        origin = f"https://{pod}-8190.proxy.runpod.net" if pod else STUDIO_URL
        headers.update({"Origin": origin, "Content-Type": "application/json"})
    request = Request(STUDIO_URL + path, data=b"{}" if post else None, headers=headers)
    try:
        with studio.open(request, timeout=10) as response:
            return json.load(response)
    except HTTPError as error:
        with error:
            try:
                message = json.load(error).get("error")
            except ValueError:
                message = None
        raise RuntimeError(message or f"Studio returned HTTP {error.code}.") from None
    except OSError as error:
        raise RuntimeError("The local Studio service is unavailable.") from error


def project_path(project_id):
    if not re.fullmatch(r"[a-f0-9]{32}", project_id):
        raise ValueError("Use the project ID returned by list_productions.")
    return "/projects/" + project_id


@mcp.tool()
def list_productions() -> dict[str, Any]:
    """List the projects in the Studio production queue and their current status."""
    return studio_request("/projects")


@mcp.tool()
def production_status(project_id: str) -> dict[str, Any]:
    """Read live shot progress, prompt adjustments, and transition reviews for a production."""
    return studio_request(project_path(project_id))


@mcp.tool()
def resume_production(project_id: str) -> dict[str, Any]:
    """Resume an interrupted production from its saved render or clip review."""
    return studio_request(project_path(project_id) + "/resume", post=True)


@mcp.tool()
def stop_production(project_id: str) -> dict[str, Any]:
    """Request that Studio stop an active production and keep its completed clips."""
    return studio_request(project_path(project_id) + "/cancel", post=True)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
