"""Promote tested commits through native Vercel Git integration and verify live apps."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def request(
    url: str, *, method: str = "GET", data: Any = None, token: str = ""
) -> tuple[int, bytes, Any]:
    headers = {"User-Agent": "project-release-check", "Cache-Control": "no-cache"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with build_opener(NoRedirect).open(req, timeout=30) as response:
            return response.status, response.read(), response.headers
    except HTTPError as error:
        return error.code, error.read(), error.headers


def github(path: str, *, method: str = "GET", data: Any = None, missing_ok: bool = False) -> Any:
    repository = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RuntimeError("Invalid repository")
    status, body, _ = request(
        f"https://api.github.com/repos/{repository}/{path}",
        method=method,
        data=data,
        token=os.environ["GH_TOKEN"],
    )
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise RuntimeError(f"GitHub API {method} failed with HTTP {status}")
    return json.loads(body)


def check_live(base_url: str, kind: str, revision: str | None = None) -> None:
    if not re.fullmatch(r"https://[a-z0-9-]+\.vercel\.app", base_url):
        raise RuntimeError("Expected an HTTPS Vercel application origin")
    health_path = "/healthz" if kind == "civicops" else "/health"
    status, body, _ = request(base_url + health_path)
    if status != 200:
        raise RuntimeError(f"Readiness failed with HTTP {status}")
    health = json.loads(body)
    if health.get("status") != "ok":
        raise RuntimeError("Service is not ready")
    if kind == "civicops":
        if health.get("model_loaded") is not True or health.get("storage_ready") is not True:
            raise RuntimeError("Model or durable storage is not ready")
        checks = [
            ("/", (302, 303)),
            ("/api/v1/session", (401,)),
            ("/docs", (401,)),
            ("/openapi.json", (401,)),
        ]
    else:
        if health.get("database") != "ok" or health.get("environment") != "production":
            raise RuntimeError("Production database is not ready")
        checks = [("/dashboard", (200,)), ("/docs", (200,)), ("/openapi.json", (200,))]
    if revision is not None and health.get("revision") != revision:
        raise RuntimeError("Live application does not match the tested commit")
    for path, expected in checks:
        status, body, headers = request(base_url + path)
        if status not in expected:
            raise RuntimeError(f"{path} returned HTTP {status}")
        if path == "/" and urljoin(base_url, headers.get("Location", "")) != base_url + "/login":
            raise RuntimeError("Console does not redirect to application sign-in")
        if kind == "dataops" and path == "/dashboard" and b"DataOps" not in body:
            raise RuntimeError("Dashboard HTML is missing")
        if kind == "dataops" and path == "/docs" and b"swagger-ui" not in body:
            raise RuntimeError("Interactive API documentation is missing")
    if kind == "dataops":
        # An empty payload cannot create a record even if authentication regresses.
        status, _, _ = request(base_url + "/api/v1/pipeline-runs", method="POST", data={})
        if status != 401:
            raise RuntimeError("Anonymous ingestion is not rejected")
    print("Live readiness, pages, and access-control checks passed")


def publish(base_url: str, kind: str) -> None:
    if (
        os.environ.get("GITHUB_EVENT_NAME") != "push"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise RuntimeError("Production promotion requires a main-branch push")
    sha = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Invalid commit SHA")
    if github("git/ref/heads/main")["object"]["sha"] != sha:
        print("Main has advanced; this release is superseded")
        return
    production = github("git/ref/heads/production", missing_ok=True)
    if production is None:
        github("git/refs", method="POST", data={"ref": "refs/heads/production", "sha": sha})
    elif production["object"]["sha"] != sha:
        # GitHub rejects non-fast-forward updates; never rewrite release history.
        github("git/refs/heads/production", method="PATCH", data={"sha": sha, "force": False})
    print(f"Production branch pinned to tested commit {sha}")
    for _ in range(60):
        # Native Git connections reliably report a commit status; a separate
        # GitHub Deployment object is optional in Vercel's integration.
        result = github(f"commits/{sha}/status")
        for deployment in result["statuses"]:
            if deployment["context"] != "Vercel":
                continue
            state = deployment["state"]
            if state in ("error", "failure"):
                raise RuntimeError("Vercel production deployment failed")
            if state == "success":
                for attempt in range(12):
                    try:
                        check_live(base_url, kind, sha)
                        return
                    except (OSError, RuntimeError, ValueError):
                        if attempt == 11:
                            raise
                        time.sleep(10)
        time.sleep(15)
    raise RuntimeError("Timed out waiting for Vercel production deployment")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "publish"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--kind", required=True, choices=("civicops", "dataops"))
    args = parser.parse_args()
    if args.operation == "publish":
        publish(args.url, args.kind)
    else:
        for attempt in range(3):
            try:
                check_live(args.url, args.kind)
                break
            except (OSError, RuntimeError, ValueError):
                if attempt == 2:
                    raise
                time.sleep(10)


if __name__ == "__main__":
    main()
