"""Thin Seerr API client covering the seams tv-decider needs.

Endpoints verified against the Seerr v3 OpenAPI spec (seerr-team/seerr):
- GET  /api/v1/request/{id}                 read a request
- PUT  /api/v1/request/{id}                 update (mediaType required; profileId, seasons, ...)
- POST /api/v1/request/{id}/{approve|decline}
- GET  /api/v1/request?filter=pending       list pending requests
- GET  /api/v1/service/sonarr               list Sonarr servers
- GET  /api/v1/service/sonarr/{id}          quality profiles / root folders
- GET  /api/v1/tv/{tmdbId}                  TV details (TMDB proxy)
"""

from __future__ import annotations

from typing import Any

import httpx


class SeerrError(Exception):
    pass


class SeerrClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"X-Api-Key": api_key},
        )

    def _get(self, path: str, **params: Any) -> Any:
        try:
            response = self._client.get(f"/api/v1{path}", params=params or None)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise SeerrError(f"GET {path} failed: {exc}") from exc

    # -- reads ------------------------------------------------------------

    def get_request(self, request_id: int) -> dict:
        return self._get(f"/request/{request_id}")

    def list_requests(self, filter: str = "pending", take: int = 50, skip: int = 0) -> list[dict]:
        data = self._get("/request", filter=filter, take=take, skip=skip)
        return data.get("results", [])

    def get_tv_details(self, tmdb_id: int) -> dict:
        return self._get(f"/tv/{tmdb_id}")

    def list_sonarr_servers(self) -> list[dict]:
        return self._get("/service/sonarr")

    def get_sonarr_service(self, sonarr_id: int) -> dict:
        return self._get(f"/service/sonarr/{sonarr_id}")

    def resolve_profile_id(self, profile_name: str, sonarr_id: int | None = None) -> int:
        """Resolve a quality profile name to its ID via Seerr service discovery."""
        if sonarr_id is None:
            servers = self.list_sonarr_servers()
            if not servers:
                raise SeerrError("no Sonarr servers configured in Seerr")
            default = next((s for s in servers if s.get("isDefault")), servers[0])
            sonarr_id = default["id"]
        service = self.get_sonarr_service(sonarr_id)
        wanted = profile_name.strip().lower()
        for profile in service.get("profiles", []):
            if str(profile.get("name", "")).strip().lower() == wanted:
                return int(profile["id"])
        raise SeerrError(
            f"profile '{profile_name}' not found on Sonarr server {sonarr_id}; "
            f"available: {[p.get('name') for p in service.get('profiles', [])]}"
        )

    # -- writes (executor-only; never called in shadow/recommend) ---------

    def update_request_profile(
        self, request_id: int, profile_id: int, seasons: list[int] | None = None
    ) -> dict:
        """PUT /request/{id}. mediaType is required by the API; seasons preserved when given."""
        body: dict[str, Any] = {"mediaType": "tv", "profileId": profile_id}
        if seasons:
            body["seasons"] = seasons
        try:
            response = self._client.put(f"/api/v1/request/{request_id}", json=body)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise SeerrError(f"PUT /request/{request_id} failed: {exc}") from exc

    def set_request_status(self, request_id: int, status: str) -> dict:
        if status not in ("approve", "decline"):
            raise SeerrError(f"invalid request status '{status}'")
        try:
            response = self._client.post(f"/api/v1/request/{request_id}/{status}")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise SeerrError(f"POST /request/{request_id}/{status} failed: {exc}") from exc

    def approve_request(self, request_id: int) -> dict:
        return self.set_request_status(request_id, "approve")
