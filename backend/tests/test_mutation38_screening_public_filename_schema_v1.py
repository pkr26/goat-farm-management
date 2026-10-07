"""Generated clients receive the release's public upload filename constraint."""

from httpx import AsyncClient


async def test_public_openapi_upload_filename_preserves_client_minimum(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    upload = response.json()["components"]["schemas"]["ScreeningUploadIn"]
    filename = upload["properties"]["file_name"]
    assert filename["type"] == "string"
    assert filename["minLength"] == 5
    assert filename["maxLength"] == 255
