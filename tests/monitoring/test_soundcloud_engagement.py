from pathlib import Path

from superagi.monitoring.soundcloud_engagement import (
    MonitorConfig,
    SnapshotStore,
    SoundCloudMonitor,
    runtime_from_env,
)


class FakeSoundCloudClient:
    def __init__(self):
        self.tick = 0

    def resolve_profile(self):
        return {"urn": "soundcloud:users:42"}

    def get_user(self, user_urn):
        assert user_urn == "soundcloud:users:42"
        return {
            "urn": user_urn,
            "username": "Almighty Sonoxo",
            "followers_count": 100 + self.tick,
            "followings_count": 20,
            "track_count": 1,
            "reposts_count": 3,
            "public_favorites_count": 7,
        }

    def get_user_tracks(self, user_urn):
        assert user_urn == "soundcloud:users:42"
        tracks = [
            {
                "urn": "soundcloud:tracks:9",
                "title": "Track 9",
                "permalink_url": "https://soundcloud.com/example/track-9",
                "playback_count": 1000 + (10 * self.tick),
                "favoritings_count": 50 + self.tick,
                "comment_count": 5,
                "reposts_count": 8 + self.tick,
                "reveal_stats": True,
            }
        ]
        self.tick += 1
        return tracks


def test_snapshots_and_deltas(tmp_path: Path):
    db_path = tmp_path / "monitor.sqlite3"
    config = MonitorConfig(
        access_token="test-token",
        user_urn="soundcloud:users:42",
        database_path=str(db_path),
    )
    client = FakeSoundCloudClient()
    store = SnapshotStore(str(db_path))
    monitor = SoundCloudMonitor(config, client=client, store=store)

    first = monitor.collect()
    second = monitor.collect()

    assert first.total_plays == 1000
    assert second.total_plays == 1010
    assert second.followers_count == 101

    delta = store.delta()
    assert delta is not None
    assert delta["changes"]["followers_count"] == 1
    assert delta["changes"]["total_plays"] == 10
    assert delta["changes"]["total_likes"] == 1
    assert delta["changes"]["total_track_reposts"] == 1
    assert delta["track_changes"][0]["playback_count"] == 10


def test_missing_values_are_not_fabricated(tmp_path: Path):
    class HiddenStatsClient(FakeSoundCloudClient):
        def get_user_tracks(self, user_urn):
            return [
                {
                    "urn": "soundcloud:tracks:hidden",
                    "title": "Hidden",
                    "playback_count": None,
                    "favoritings_count": None,
                    "comment_count": None,
                    "reposts_count": None,
                    "reveal_stats": False,
                }
            ]

    db_path = tmp_path / "hidden.sqlite3"
    config = MonitorConfig(
        access_token="test-token",
        user_urn="soundcloud:users:42",
        database_path=str(db_path),
    )
    snapshot = SoundCloudMonitor(
        config,
        client=HiddenStatsClient(),
        store=SnapshotStore(str(db_path)),
    ).collect()

    assert snapshot.stats_hidden_tracks == 1
    assert snapshot.tracks[0].playback_count is None
    assert snapshot.tracks[0].favoritings_count is None


def test_runtime_is_truthfully_unconfigured_without_access_token(monkeypatch):
    monkeypatch.delenv("SOUNDCLOUD_ACCESS_TOKEN", raising=False)
    runtime = runtime_from_env()
    assert runtime.health()["status"] == "UNCONFIGURED"
    assert runtime.health()["thread_alive"] is False
    assert runtime.store is None
