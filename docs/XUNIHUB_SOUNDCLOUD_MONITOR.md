# XuniHub SoundCloud Engagement Monitor

## Purpose

Collect genuine SoundCloud engagement snapshots every 60 seconds while XuniHub is running. The monitor is read-only. It does not create plays, follows, likes, comments, reposts, or any other engagement.

Primary profile target:

- `https://soundcloud.com/almightysonoxo/tracks`

## Data source

The monitor uses the official SoundCloud HTTP API and the documented fields exposed by SoundCloud. Missing or hidden statistics remain unavailable; XuniHub does not substitute mock values.

## Required environment

- `SOUNDCLOUD_ACCESS_TOKEN` — authorized SoundCloud API access token. Required for live monitoring.

Optional:

- `SOUNDCLOUD_PROFILE_URL` — defaults to the Almighty Sonoxo tracks URL above.
- `SOUNDCLOUD_USER_URN` — skips URL resolution when the canonical user URN is known.
- `SOUNDCLOUD_POLL_SECONDS` — defaults to `60`.
- `SOUNDCLOUD_MONITOR_DB` — defaults to `workspace/xunihub_soundcloud_engagement.sqlite3`.
- `SOUNDCLOUD_REQUEST_TIMEOUT_SECONDS` — defaults to `20`.
- `SOUNDCLOUD_MAX_RETRIES` — defaults to `3` with bounded exponential backoff for transient failures and HTTP 429.
- `XUNIHUB_MONITOR_API_KEY` — optional API key for the standalone monitor service.

Secrets must be injected by the deployment environment. Never commit access tokens.

## Integrated SuperAGI endpoints

These endpoints are available below the existing authenticated `/analytics` router:

- `GET /analytics/soundcloud/health`
- `GET /analytics/soundcloud/latest`
- `GET /analytics/soundcloud/delta`
- `GET /analytics/soundcloud/history?limit=60`
- `POST /analytics/soundcloud/poll-now`

The background worker starts with the backend when `SOUNDCLOUD_ACCESS_TOKEN` is present. If the token is absent, health reports `UNCONFIGURED` and no fake data is created.

## Standalone service

The same monitor can run independently:

`uvicorn superagi.monitoring.soundcloud_app:app --host 0.0.0.0 --port 8091`

Standalone endpoints:

- `GET /health`
- `GET /latest`
- `GET /delta`
- `GET /history?limit=60`
- `POST /poll-now`

## Snapshot metrics

Profile-level values:

- followers
- followings
- public track count
- profile repost count
- public favorites count

Track-level values when SoundCloud exposes them:

- play count
- favoritings/likes
- comments
- reposts
- `reveal_stats` state

The database also stores aggregate visible totals and per-track deltas between consecutive snapshots.

## Reporting cadence

XuniHub itself samples every 60 seconds while the process is alive. A UI can request `/delta` once per minute to display live changes. ChatGPT scheduled-task delivery is independent from the XuniHub collector and can be slower without reducing the stored minute-level history.

## Status contract

- `UNCONFIGURED` — no SoundCloud credential is available.
- `STOPPED` — configured but worker is not active.
- `CONNECTING` — a poll is in progress.
- `CONNECTED` — last official API poll succeeded.
- `DEGRADED` — a transient poll failed; the worker remains alive and will retry on the next interval.
- `ERROR` — authentication/authorization failed.

Only `CONNECTED` is a positive live-data signal.
