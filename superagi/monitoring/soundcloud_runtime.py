"""Managed SoundCloud authentication for the XuniHub read-only engagement monitor.

Secrets are read from environment variables and never logged. The runtime can use
an existing access token or obtain/refresh a public-resource client-credentials
token while the process is alive.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from superagi.monitoring.soundcloud_engagement import (
    DEFAULT_POLL_SECONDS,
    DEFAULT_PROFILE_URL,
    MonitorConfig,
    MonitorRuntime,
    SoundCloudAPIClient,
    SoundCloudAuthenticationError,
    SoundCloudMonitor,
    SoundCloudMonitorError,
    SoundCloudRateLimitError,
)

TOKEN_URL = "https://secure.soundcloud.com/oauth/token"
TOKEN_REFRESH_SKEW_SECONDS = 120


@dataclass(frozen=True)
class SoundCloudCredentialConfig:
    access_token: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]

    @classmethod
    def from_env(cls) -> Optional["SoundCloudCredentialConfig"]:
        access_token = os.getenv("SOUNDCLOUD_ACCESS_TOKEN", "").strip() or None
        client_id = os.getenv("SOUNDCLOUD_CLIENT_ID", "").strip() or None
        client_secret = os.getenv("SOUNDCLOUD_CLIENT_SECRET", "").strip() or None

        if not access_token and not (client_id and client_secret):
            return None
        if bool(client_id) != bool(client_secret):
            raise SoundCloudMonitorError(
                "SOUNDCLOUD_CLIENT_ID and SOUNDCLOUD_CLIENT_SECRET must be configured together"
            )
        return cls(
            access_token=access_token,
            client_id=client_id,
            client_secret=client_secret,
        )


class SoundCloudTokenProvider:
    """Caches and refreshes SoundCloud tokens without emitting secret material."""

    def __init__(
        self,
        credentials: SoundCloudCredentialConfig,
        session: Optional[requests.Session] = None,
        request_timeout_seconds: int = 20,
        max_retries: int = 3,
    ):
        self.credentials = credentials
        self.session = session or requests.Session()
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self._access_token = credentials.access_token
        self._refresh_token: Optional[str] = None
        self._expires_at: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def can_refresh(self) -> bool:
        return bool(self.credentials.client_id and self.credentials.client_secret)

    @property
    def auth_mode(self) -> str:
        if self.can_refresh:
            return "managed_client_credentials"
        return "static_access_token"

    def _token_request(self, *, data: Dict[str, str], use_basic_auth: bool) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                kwargs: Dict[str, Any] = {
                    "data": data,
                    "headers": {
                        "Accept": "application/json; charset=utf-8",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    "timeout": self.request_timeout_seconds,
                }
                if use_basic_auth:
                    kwargs["auth"] = (
                        self.credentials.client_id,
                        self.credentials.client_secret,
                    )
                response = self.session.post(TOKEN_URL, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise SoundCloudMonitorError(
                        "SoundCloud token endpoint request failed"
                    ) from exc
                time.sleep(min(2 ** attempt, 8))
                continue

            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise SoundCloudRateLimitError(
                        "SoundCloud token exchange rate limit persisted after bounded retries"
                    )
                retry_after = response.headers.get("Retry-After")
                try:
                    wait_seconds = float(retry_after) if retry_after else 2 ** attempt
                except (TypeError, ValueError):
                    wait_seconds = 2 ** attempt
                time.sleep(max(1.0, min(wait_seconds, 120.0)))
                continue

            if response.status_code in (400, 401, 403):
                raise SoundCloudAuthenticationError(
                    f"SoundCloud token exchange failed with HTTP {response.status_code}"
                )
            if 500 <= response.status_code < 600:
                if attempt >= self.max_retries:
                    raise SoundCloudMonitorError(
                        f"SoundCloud token service returned HTTP {response.status_code}"
                    )
                time.sleep(min(2 ** attempt, 8))
                continue
            if not response.ok:
                raise SoundCloudMonitorError(
                    f"SoundCloud token endpoint returned HTTP {response.status_code}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise SoundCloudMonitorError(
                    "SoundCloud token endpoint returned a non-JSON response"
                ) from exc
            if not isinstance(payload, dict) or not payload.get("access_token"):
                raise SoundCloudAuthenticationError(
                    "SoundCloud token response did not contain an access token"
                )
            return payload

        if last_error:
            raise SoundCloudMonitorError("SoundCloud token request failed") from last_error
        raise SoundCloudMonitorError("SoundCloud token request failed")

    def _apply_payload(self, payload: Dict[str, Any]) -> str:
        token = str(payload["access_token"])
        refresh = payload.get("refresh_token")
        expires_in = payload.get("expires_in")
        try:
            lifetime = max(60, int(expires_in))
        except (TypeError, ValueError):
            lifetime = 3600
        self._access_token = token
        self._refresh_token = str(refresh) if refresh else None
        self._expires_at = time.time() + lifetime
        return token

    def _exchange_client_credentials(self) -> str:
        if not self.can_refresh:
            raise SoundCloudAuthenticationError(
                "SoundCloud client credentials are not configured"
            )
        payload = self._token_request(
            data={"grant_type": "client_credentials"},
            use_basic_auth=True,
        )
        return self._apply_payload(payload)

    def _exchange_refresh_token(self) -> str:
        if not self.can_refresh or not self._refresh_token:
            return self._exchange_client_credentials()
        payload = self._token_request(
            data={
                "grant_type": "refresh_token",
                "client_id": str(self.credentials.client_id),
                "client_secret": str(self.credentials.client_secret),
                "refresh_token": self._refresh_token,
            },
            use_basic_auth=False,
        )
        return self._apply_payload(payload)

    def get_access_token(self) -> str:
        with self._lock:
            if self._access_token:
                if self._expires_at is None:
                    return self._access_token
                if time.time() < self._expires_at - TOKEN_REFRESH_SKEW_SECONDS:
                    return self._access_token
            if not self.can_refresh:
                raise SoundCloudAuthenticationError(
                    "Configured SoundCloud access token is unavailable or expired"
                )
            if self._refresh_token:
                return self._exchange_refresh_token()
            return self._exchange_client_credentials()

    def force_refresh(self) -> str:
        with self._lock:
            if not self.can_refresh:
                raise SoundCloudAuthenticationError(
                    "SoundCloud token cannot be refreshed without client credentials"
                )
            if self._refresh_token:
                return self._exchange_refresh_token()
            return self._exchange_client_credentials()


class RefreshingSoundCloudAPIClient(SoundCloudAPIClient):
    """SoundCloud API client that retries one auth failure after a safe refresh."""

    def __init__(
        self,
        config: MonitorConfig,
        token_provider: SoundCloudTokenProvider,
        session: Optional[requests.Session] = None,
    ):
        super().__init__(config, session=session)
        self.token_provider = token_provider
        self.session.headers.pop("Authorization", None)

    def _request_json(self, url_or_path: str, *, params=None):
        for auth_attempt in range(2):
            token = self.token_provider.get_access_token()
            self.session.headers["Authorization"] = f"OAuth {token}"
            try:
                return super()._request_json(url_or_path, params=params)
            except SoundCloudAuthenticationError:
                if auth_attempt == 0 and self.token_provider.can_refresh:
                    self.token_provider.force_refresh()
                    continue
                raise
        raise SoundCloudAuthenticationError("SoundCloud authentication failed")


class ManagedMonitorRuntime(MonitorRuntime):
    def __init__(self, monitor: Optional[SoundCloudMonitor], provider: Optional[SoundCloudTokenProvider]):
        super().__init__(monitor)
        self.provider = provider

    def health(self) -> Dict[str, Any]:
        data = super().health()
        data["auth_mode"] = self.provider.auth_mode if self.provider else "unconfigured"
        data["automatic_token_refresh"] = bool(self.provider and self.provider.can_refresh)
        return data


def runtime_from_env() -> MonitorRuntime:
    credentials = SoundCloudCredentialConfig.from_env()
    if credentials is None:
        return ManagedMonitorRuntime(None, None)

    request_timeout = max(5, int(os.getenv("SOUNDCLOUD_REQUEST_TIMEOUT_SECONDS", "20")))
    max_retries = max(0, int(os.getenv("SOUNDCLOUD_MAX_RETRIES", "3")))
    provider = SoundCloudTokenProvider(
        credentials,
        request_timeout_seconds=request_timeout,
        max_retries=max_retries,
    )

    config = MonitorConfig(
        access_token="managed-by-xunihub",
        profile_url=os.getenv("SOUNDCLOUD_PROFILE_URL", DEFAULT_PROFILE_URL).strip(),
        user_urn=os.getenv("SOUNDCLOUD_USER_URN") or None,
        poll_seconds=max(15, int(os.getenv("SOUNDCLOUD_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))),
        database_path=os.getenv(
            "SOUNDCLOUD_MONITOR_DB",
            "workspace/xunihub_soundcloud_engagement.sqlite3",
        ),
        request_timeout_seconds=request_timeout,
        max_retries=max_retries,
    )
    client = RefreshingSoundCloudAPIClient(config, provider)
    monitor = SoundCloudMonitor(config, client=client)
    return ManagedMonitorRuntime(monitor, provider)
