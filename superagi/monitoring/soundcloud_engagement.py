"""SoundCloud engagement monitoring for XuniHub.

This module only reads engagement through SoundCloud's documented HTTP API.
It never creates plays, follows, likes, comments, reposts, or other engagement.

Official API references:
- https://developers.soundcloud.com/docs/api/explorer/open-api
- https://github.com/soundcloud/api/blob/master/openapi/api.yaml
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests


API_BASE_URL = "https://api.soundcloud.com"
DEFAULT_PROFILE_URL = "https://soundcloud.com/almightysonoxo/tracks"
DEFAULT_POLL_SECONDS = 60


class SoundCloudMonitorError(RuntimeError):
    """Base monitoring error."""


class SoundCloudAuthenticationError(SoundCloudMonitorError):
    """Raised when SoundCloud rejects the configured credential."""


class SoundCloudRateLimitError(SoundCloudMonitorError):
    """Raised after bounded rate-limit retries are exhausted."""


@dataclass(frozen=True)
class MonitorConfig:
    access_token: str
    profile_url: str = DEFAULT_PROFILE_URL
    user_urn: Optional[str] = None
    poll_seconds: int = DEFAULT_POLL_SECONDS
    database_path: str = "workspace/xunihub_soundcloud_engagement.sqlite3"
    request_timeout_seconds: int = 20
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> Optional["MonitorConfig"]:
        token = os.getenv("SOUNDCLOUD_ACCESS_TOKEN", "").strip()
        if not token:
            return None
        poll_seconds = max(15, int(os.getenv("SOUNDCLOUD_POLL_SECONDS", "60")))
        return cls(
            access_token=token,
            profile_url=os.getenv("SOUNDCLOUD_PROFILE_URL", DEFAULT_PROFILE_URL).strip(),
            user_urn=os.getenv("SOUNDCLOUD_USER_URN") or None,
            poll_seconds=poll_seconds,
            database_path=os.getenv(
                "SOUNDCLOUD_MONITOR_DB",
                "workspace/xunihub_soundcloud_engagement.sqlite3",
            ),
            request_timeout_seconds=max(
                5, int(os.getenv("SOUNDCLOUD_REQUEST_TIMEOUT_SECONDS", "20"))
            ),
            max_retries=max(0, int(os.getenv("SOUNDCLOUD_MAX_RETRIES", "3"))),
        )


@dataclass
class TrackMetrics:
    track_urn: str
    title: str
    permalink_url: Optional[str]
    playback_count: Optional[int]
    favoritings_count: Optional[int]
    comment_count: Optional[int]
    reposts_count: Optional[int]
    reveal_stats: Optional[bool]


@dataclass
class EngagementSnapshot:
    captured_at: str
    user_urn: str
    username: str
    followers_count: Optional[int]
    followings_count: Optional[int]
    track_count: Optional[int]
    profile_reposts_count: Optional[int]
    public_favorites_count: Optional[int]
    visible_tracks: int
    stats_hidden_tracks: int
    total_plays: int
    total_likes: int
    total_comments: int
    total_track_reposts: int
    tracks: List[TrackMetrics]


class SoundCloudAPIClient:
    """Minimal documented SoundCloud API client with bounded backoff."""

    def __init__(self, config: MonitorConfig, session: Optional[requests.Session] = None):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"OAuth {config.access_token}",
                "Accept": "application/json",
                "User-Agent": "XuniHub-SoundCloud-Monitor/1.0",
            }
        )

    def _request_json(
        self,
        url_or_path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = (
            url_or_path
            if url_or_path.startswith("https://")
            else f"{API_BASE_URL}{url_or_path}"
        )
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.config.request_timeout_seconds,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    raise SoundCloudMonitorError(f"SoundCloud request failed: {exc}") from exc
                time.sleep(min(2 ** attempt, 8))
                continue

            if response.status_code in (401, 403):
                raise SoundCloudAuthenticationError(
                    f"SoundCloud authentication failed with HTTP {response.status_code}"
                )

            if response.status_code == 429:
                if attempt >= self.config.max_retries:
                    raise SoundCloudRateLimitError(
                        "SoundCloud rate limit persisted after bounded retries"
                    )
                retry_after = response.headers.get("Retry-After")
                try:
                    wait_seconds = float(retry_after) if retry_after else 2 ** attempt
                except (TypeError, ValueError):
                    wait_seconds = 2 ** attempt
                time.sleep(max(1.0, min(wait_seconds, 120.0)))
                continue

            if 500 <= response.status_code < 600:
                if attempt >= self.config.max_retries:
                    raise SoundCloudMonitorError(
                        f"SoundCloud upstream error HTTP {response.status_code}"
                    )
                time.sleep(min(2 ** attempt, 8))
                continue

            if not response.ok:
                body = response.text[:500]
                raise SoundCloudMonitorError(
                    f"SoundCloud API returned HTTP {response.status_code}: {body}"
                )

            try:
                return response.json()
            except ValueError as exc:
                raise SoundCloudMonitorError(
                    "SoundCloud returned a non-JSON response for an API resource"
                ) from exc

        if last_error:
            raise SoundCloudMonitorError(str(last_error))
        raise SoundCloudMonitorError("SoundCloud request failed")

    def resolve_profile(self) -> Dict[str, Any]:
        """Resolve the configured SoundCloud URL into its API resource."""
        resource = self._request_json("/resolve", params={"url": self.config.profile_url})
        if not isinstance(resource, dict):
            raise SoundCloudMonitorError("Resolved SoundCloud profile was not an object")
        return resource

    def get_user(self, user_urn: str) -> Dict[str, Any]:
        resource = self._request_json(f"/users/{user_urn}")
        if not isinstance(resource, dict):
            raise SoundCloudMonitorError("SoundCloud user response was not an object")
        return resource

    def get_user_tracks(self, user_urn: str) -> List[Dict[str, Any]]:
        """Read all available user tracks by following documented next_href pages."""
        tracks: List[Dict[str, Any]] = []
        next_url: Optional[str] = f"{API_BASE_URL}/users/{user_urn}/tracks"
        params: Optional[Dict[str, Any]] = {
            "limit": 50,
            "linked_partitioning": "true",
        }
        seen_urls = set()

        while next_url:
            if next_url in seen_urls:
                raise SoundCloudMonitorError("SoundCloud pagination loop detected")
            seen_urls.add(next_url)
            payload = self._request_json(next_url, params=params)
            params = None

            if isinstance(payload, list):
                tracks.extend(item for item in payload if isinstance(item, dict))
                break

            if not isinstance(payload, dict):
                raise SoundCloudMonitorError("Unexpected SoundCloud track-list response")

            collection = payload.get("collection") or []
            if not isinstance(collection, list):
                raise SoundCloudMonitorError("Invalid SoundCloud collection shape")
            tracks.extend(item for item in collection if isinstance(item, dict))
            candidate = payload.get("next_href")
            next_url = candidate if isinstance(candidate, str) and candidate else None

        return tracks


class SnapshotStore:
    """SQLite-backed immutable snapshots and delta reporting."""

    def __init__(self, path: str):
        self.path = path
        parent = Path(path).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profile_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    user_urn TEXT NOT NULL,
                    username TEXT NOT NULL,
                    followers_count INTEGER,
                    followings_count INTEGER,
                    track_count INTEGER,
                    profile_reposts_count INTEGER,
                    public_favorites_count INTEGER,
                    visible_tracks INTEGER NOT NULL,
                    stats_hidden_tracks INTEGER NOT NULL,
                    total_plays INTEGER NOT NULL,
                    total_likes INTEGER NOT NULL,
                    total_comments INTEGER NOT NULL,
                    total_track_reposts INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_profile_snapshots_captured_at
                    ON profile_snapshots(captured_at DESC);

                CREATE TABLE IF NOT EXISTS track_snapshots (
                    snapshot_id INTEGER NOT NULL,
                    track_urn TEXT NOT NULL,
                    title TEXT NOT NULL,
                    permalink_url TEXT,
                    playback_count INTEGER,
                    favoritings_count INTEGER,
                    comment_count INTEGER,
                    reposts_count INTEGER,
                    reveal_stats INTEGER,
                    PRIMARY KEY(snapshot_id, track_urn),
                    FOREIGN KEY(snapshot_id) REFERENCES profile_snapshots(id)
                );
                """
            )

    def save(self, snapshot: EngagementSnapshot) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO profile_snapshots (
                    captured_at, user_urn, username, followers_count,
                    followings_count, track_count, profile_reposts_count,
                    public_favorites_count, visible_tracks, stats_hidden_tracks,
                    total_plays, total_likes, total_comments, total_track_reposts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.captured_at,
                    snapshot.user_urn,
                    snapshot.username,
                    snapshot.followers_count,
                    snapshot.followings_count,
                    snapshot.track_count,
                    snapshot.profile_reposts_count,
                    snapshot.public_favorites_count,
                    snapshot.visible_tracks,
                    snapshot.stats_hidden_tracks,
                    snapshot.total_plays,
                    snapshot.total_likes,
                    snapshot.total_comments,
                    snapshot.total_track_reposts,
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            conn.executemany(
                """
                INSERT INTO track_snapshots (
                    snapshot_id, track_urn, title, permalink_url,
                    playback_count, favoritings_count, comment_count,
                    reposts_count, reveal_stats
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        track.track_urn,
                        track.title,
                        track.permalink_url,
                        track.playback_count,
                        track.favoritings_count,
                        track.comment_count,
                        track.reposts_count,
                        None if track.reveal_stats is None else int(track.reveal_stats),
                    )
                    for track in snapshot.tracks
                ],
            )
            return snapshot_id

    def latest(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profile_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            tracks = conn.execute(
                "SELECT * FROM track_snapshots WHERE snapshot_id = ? ORDER BY title",
                (row["id"],),
            ).fetchall()
            data["tracks"] = [dict(item) for item in tracks]
            return data

    def history(self, limit: int = 60) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1440))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM profile_snapshots ORDER BY id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def delta(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            snapshots = conn.execute(
                "SELECT * FROM profile_snapshots ORDER BY id DESC LIMIT 2"
            ).fetchall()
            if len(snapshots) < 2:
                return None

            current, previous = snapshots[0], snapshots[1]
            current_tracks = {
                row["track_urn"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM track_snapshots WHERE snapshot_id = ?",
                    (current["id"],),
                ).fetchall()
            }
            previous_tracks = {
                row["track_urn"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM track_snapshots WHERE snapshot_id = ?",
                    (previous["id"],),
                ).fetchall()
            }

        profile_metrics = (
            "followers_count",
            "followings_count",
            "track_count",
            "profile_reposts_count",
            "public_favorites_count",
            "total_plays",
            "total_likes",
            "total_comments",
            "total_track_reposts",
        )
        changes: Dict[str, Optional[int]] = {}
        for metric in profile_metrics:
            current_value = current[metric]
            previous_value = previous[metric]
            changes[metric] = (
                None
                if current_value is None or previous_value is None
                else int(current_value) - int(previous_value)
            )

        track_changes = []
        for urn, track in current_tracks.items():
            old = previous_tracks.get(urn)
            if old is None:
                track_changes.append(
                    {
                        "track_urn": urn,
                        "title": track["title"],
                        "new_track": True,
                    }
                )
                continue
            item: Dict[str, Any] = {
                "track_urn": urn,
                "title": track["title"],
                "new_track": False,
            }
            changed = False
            for metric in (
                "playback_count",
                "favoritings_count",
                "comment_count",
                "reposts_count",
            ):
                now = track.get(metric)
                before = old.get(metric)
                delta_value = None if now is None or before is None else now - before
                item[metric] = delta_value
                if delta_value not in (None, 0):
                    changed = True
            if changed:
                track_changes.append(item)

        track_changes.sort(
            key=lambda item: sum(
                abs(item.get(metric) or 0)
                for metric in (
                    "playback_count",
                    "favoritings_count",
                    "comment_count",
                    "reposts_count",
                )
            ),
            reverse=True,
        )

        return {
            "from": previous["captured_at"],
            "to": current["captured_at"],
            "changes": changes,
            "track_changes": track_changes,
            "coverage_changed": (
                current["visible_tracks"] != previous["visible_tracks"]
                or current["stats_hidden_tracks"] != previous["stats_hidden_tracks"]
            ),
        }


class SoundCloudMonitor:
    def __init__(
        self,
        config: MonitorConfig,
        client: Optional[SoundCloudAPIClient] = None,
        store: Optional[SnapshotStore] = None,
    ):
        self.config = config
        self.client = client or SoundCloudAPIClient(config)
        self.store = store or SnapshotStore(config.database_path)
        self._resolved_user_urn = config.user_urn

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _resolve_user_urn(self) -> str:
        if self._resolved_user_urn:
            return self._resolved_user_urn
        resource = self.client.resolve_profile()
        urn = resource.get("urn")
        if not isinstance(urn, str) or not urn.startswith("soundcloud:users:"):
            user = resource.get("user") if isinstance(resource.get("user"), dict) else None
            urn = user.get("urn") if user else None
        if not isinstance(urn, str) or not urn.startswith("soundcloud:users:"):
            raise SoundCloudMonitorError(
                "Configured profile URL did not resolve to a SoundCloud user. "
                "Set SOUNDCLOUD_USER_URN explicitly if needed."
            )
        self._resolved_user_urn = urn
        return urn

    def collect(self) -> EngagementSnapshot:
        user_urn = self._resolve_user_urn()
        user = self.client.get_user(user_urn)
        raw_tracks = self.client.get_user_tracks(user_urn)

        tracks: List[TrackMetrics] = []
        total_plays = 0
        total_likes = 0
        total_comments = 0
        total_reposts = 0
        hidden_stats = 0

        for raw in raw_tracks:
            urn = raw.get("urn")
            if not isinstance(urn, str):
                continue
            reveal_stats = raw.get("reveal_stats")
            if reveal_stats is False:
                hidden_stats += 1
            playback_count = self._optional_int(raw.get("playback_count"))
            favoritings_count = self._optional_int(raw.get("favoritings_count"))
            comment_count = self._optional_int(raw.get("comment_count"))
            reposts_count = self._optional_int(raw.get("reposts_count"))
            if playback_count is not None:
                total_plays += playback_count
            if favoritings_count is not None:
                total_likes += favoritings_count
            if comment_count is not None:
                total_comments += comment_count
            if reposts_count is not None:
                total_reposts += reposts_count
            tracks.append(
                TrackMetrics(
                    track_urn=urn,
                    title=str(raw.get("title") or urn),
                    permalink_url=raw.get("permalink_url"),
                    playback_count=playback_count,
                    favoritings_count=favoritings_count,
                    comment_count=comment_count,
                    reposts_count=reposts_count,
                    reveal_stats=reveal_stats if isinstance(reveal_stats, bool) else None,
                )
            )

        snapshot = EngagementSnapshot(
            captured_at=datetime.now(timezone.utc).isoformat(),
            user_urn=user_urn,
            username=str(user.get("username") or user_urn),
            followers_count=self._optional_int(user.get("followers_count")),
            followings_count=self._optional_int(user.get("followings_count")),
            track_count=self._optional_int(user.get("track_count")),
            profile_reposts_count=self._optional_int(user.get("reposts_count")),
            public_favorites_count=self._optional_int(user.get("public_favorites_count")),
            visible_tracks=len(tracks),
            stats_hidden_tracks=hidden_stats,
            total_plays=total_plays,
            total_likes=total_likes,
            total_comments=total_comments,
            total_track_reposts=total_reposts,
            tracks=tracks,
        )
        self.store.save(snapshot)
        return snapshot


class MonitorRuntime:
    """Owns the 60-second polling lifecycle and exposes truthful health state."""

    def __init__(self, monitor: Optional[SoundCloudMonitor]):
        self.monitor = monitor
        self.status = "UNCONFIGURED" if monitor is None else "STOPPED"
        self.last_error: Optional[str] = None
        self.last_success_at: Optional[str] = None
        self.last_started_at: Optional[str] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def store(self) -> Optional[SnapshotStore]:
        return self.monitor.store if self.monitor else None

    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self.status,
                "profile_url": self.monitor.config.profile_url if self.monitor else DEFAULT_PROFILE_URL,
                "poll_seconds": self.monitor.config.poll_seconds if self.monitor else DEFAULT_POLL_SECONDS,
                "last_success_at": self.last_success_at,
                "last_started_at": self.last_started_at,
                "last_error": self.last_error,
                "thread_alive": bool(self._thread and self._thread.is_alive()),
                "data_source": "SoundCloud official API",
                "artificial_engagement": False,
            }

    def start(self) -> None:
        if self.monitor is None:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.last_started_at = datetime.now(timezone.utc).isoformat()
        self._thread = threading.Thread(
            target=self._run,
            name="xunihub-soundcloud-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        with self._lock:
            self.status = "STOPPED" if self.monitor else "UNCONFIGURED"

    def poll_once(self) -> Optional[EngagementSnapshot]:
        if self.monitor is None:
            return None
        with self._lock:
            self.status = "CONNECTING"
            self.last_error = None
        try:
            snapshot = self.monitor.collect()
        except SoundCloudAuthenticationError as exc:
            with self._lock:
                self.status = "ERROR"
                self.last_error = str(exc)
            raise
        except Exception as exc:
            with self._lock:
                self.status = "DEGRADED"
                self.last_error = str(exc)
            raise
        else:
            with self._lock:
                self.status = "CONNECTED"
                self.last_success_at = snapshot.captured_at
                self.last_error = None
            return snapshot

    def _run(self) -> None:
        assert self.monitor is not None
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.poll_once()
            except Exception:
                # Health state already records the truthful error. The monitor remains
                # alive so transient upstream failures can recover automatically.
                pass
            elapsed = time.monotonic() - started
            wait_for = max(1.0, self.monitor.config.poll_seconds - elapsed)
            self._stop_event.wait(wait_for)


def runtime_from_env() -> MonitorRuntime:
    config = MonitorConfig.from_env()
    if config is None:
        return MonitorRuntime(None)
    return MonitorRuntime(SoundCloudMonitor(config))


def snapshot_to_dict(snapshot: EngagementSnapshot) -> Dict[str, Any]:
    return asdict(snapshot)
