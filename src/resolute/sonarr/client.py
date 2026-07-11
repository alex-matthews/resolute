"""Thin Sonarr v3 API client. Read-mostly: audit is the norm, mutation the exception."""

from __future__ import annotations

from typing import Any

import httpx


class SonarrError(Exception):
    pass


class SonarrClient:
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
            response = self._client.get(f"/api/v3{path}", params=params or None)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise SonarrError(f"GET {path} failed: {exc}") from exc

    def list_quality_profiles(self) -> list[dict]:
        return self._get("/qualityprofile")

    def get_quality_profile(self, profile_id: int) -> dict:
        """Full profile including its quality list (ADR-0002 invariant check)."""
        return self._get(f"/qualityprofile/{profile_id}")

    def resolve_profile_id(self, profile_name: str) -> int:
        wanted = profile_name.strip().lower()
        for profile in self.list_quality_profiles():
            if str(profile.get("name", "")).strip().lower() == wanted:
                return int(profile["id"])
        raise SonarrError(f"quality profile '{profile_name}' not found in Sonarr")

    def get_series_by_tvdb(self, tvdb_id: int) -> dict | None:
        results = self._get("/series", tvdbId=tvdb_id)
        return results[0] if results else None

    def list_series(self) -> list[dict]:
        return self._get("/series")

    def get_series(self, series_id: int) -> dict:
        return self._get(f"/series/{series_id}")

    def list_episode_files(self, series_id: int) -> list[dict]:
        """Resident files with size and quality (reclaim inventory/estimate)."""
        return self._get("/episodefile", seriesId=series_id)

    def get_queue_details(self, series_id: int) -> list[dict]:
        """In-flight grabs/imports for a series (downgrade precondition check)."""
        return self._get("/queue/details", seriesId=series_id)

    # -- writes ------------------------------------------------------------
    # Discipline (ADR-0001 + ADR-0002): resolute never deletes files. The
    # profile write is only safe when no search is in flight, and the *only*
    # search resolute ever triggers is the downgrade executor's monitored
    # search (ADR-0002), which drives Sonarr's own import-then-delete
    # upgrade flow. Both paths are gated far above this client.

    def update_series_profile(self, series_id: int, profile_id: int) -> dict:
        series = self.get_series(series_id)
        series["qualityProfileId"] = profile_id
        try:
            response = self._client.put(f"/api/v3/series/{series_id}", json=series)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise SonarrError(f"PUT /series/{series_id} failed: {exc}") from exc

    def trigger_series_search(self, series_id: int) -> dict:
        """POST /command SeriesSearch: the ADR-0002 reclaim trigger. Sonarr's
        normal upgrade flow then grabs an in-profile release, imports it, and
        deletes the out-of-profile resident (import-then-delete)."""
        try:
            response = self._client.post(
                "/api/v3/command", json={"name": "SeriesSearch", "seriesId": series_id}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise SonarrError(f"POST /command SeriesSearch failed: {exc}") from exc
