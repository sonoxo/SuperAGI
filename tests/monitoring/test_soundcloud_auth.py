from superagi.monitoring.soundcloud_runtime import (
    SoundCloudCredentialConfig,
    SoundCloudTokenProvider,
)


class FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class FakeTokenSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_client_credentials_token_is_cached():
    session = FakeTokenSession(
        [
            FakeResponse(
                200,
                {
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                },
            )
        ]
    )
    provider = SoundCloudTokenProvider(
        SoundCloudCredentialConfig(
            access_token=None,
            client_id="client-id",
            client_secret="client-secret",
        ),
        session=session,
    )

    assert provider.get_access_token() == "access-1"
    assert provider.get_access_token() == "access-1"
    assert len(session.calls) == 1
    _, kwargs = session.calls[0]
    assert kwargs["data"] == {"grant_type": "client_credentials"}
    assert kwargs["auth"] == ("client-id", "client-secret")


def test_refresh_token_rotates_in_memory():
    session = FakeTokenSession(
        [
            FakeResponse(
                200,
                {
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                },
            ),
            FakeResponse(
                200,
                {
                    "access_token": "access-2",
                    "refresh_token": "refresh-2",
                    "expires_in": 3600,
                },
            ),
        ]
    )
    provider = SoundCloudTokenProvider(
        SoundCloudCredentialConfig(
            access_token=None,
            client_id="client-id",
            client_secret="client-secret",
        ),
        session=session,
    )

    assert provider.get_access_token() == "access-1"
    assert provider.force_refresh() == "access-2"
    assert len(session.calls) == 2
    _, refresh_kwargs = session.calls[1]
    assert refresh_kwargs["data"]["grant_type"] == "refresh_token"
    assert refresh_kwargs["data"]["refresh_token"] == "refresh-1"
    assert refresh_kwargs["data"]["client_id"] == "client-id"
    assert refresh_kwargs["data"]["client_secret"] == "client-secret"
    assert "auth" not in refresh_kwargs


def test_static_token_does_not_require_client_secret():
    provider = SoundCloudTokenProvider(
        SoundCloudCredentialConfig(
            access_token="static-token",
            client_id=None,
            client_secret=None,
        ),
        session=FakeTokenSession([]),
    )
    assert provider.get_access_token() == "static-token"
    assert provider.can_refresh is False
    assert provider.auth_mode == "static_access_token"
