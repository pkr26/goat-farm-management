"""JSON media type parameters remain compatible with password endpoints."""

import httpx


async def test_json_charset_parameter_is_accepted_by_registration_and_login(
    client: httpx.AsyncClient,
) -> None:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    credentials = {"email": "json-charset-owner@farm.in", "password": "ownerpass123"}
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("This contract requires the real application transport")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=True),
        base_url=client.base_url,
        event_hooks=client.event_hooks,
    ) as browser:
        registered = await browser.post("/api/auth/register", headers=headers, json=credentials)
        assert registered.status_code == 201, registered.text
        logged_in = await browser.post("/api/auth/login", headers=headers, json=credentials)
        assert logged_in.status_code == 200, logged_in.text
        assert logged_in.json()["user"]["email"] == credentials["email"]
