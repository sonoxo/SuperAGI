from fastapi.testclient import TestClient
from server import app

client = TestClient(app)


def test_health():
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['ok'] is True


def test_provider_manifest():
    response = client.get('/providers')
    assert response.status_code == 200
    body = response.json()
    assert body['copyright_us']['mode'] == 'official_handoff'
    assert 'mlc' in body and 'soundexchange' in body


def test_unknown_provider_fails_closed():
    assert client.get('/providers/not-real').status_code == 404


def test_receipt_format_does_not_fake_provider_verification():
    response = client.post('/receipts/validate', json={
        'provider': 'copyright_us',
        'reference': 'CASE-12345',
        'status': 'submitted'
    })
    assert response.status_code == 200
    assert response.json()['verified_by_provider'] is False
