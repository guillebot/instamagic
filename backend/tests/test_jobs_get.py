"""GET /api/jobs/{job_id} for background job polling."""

from tests.conftest import login


def test_get_job_by_id(client, app_env) -> None:
    login(client)
    created = client.post(
        "/api/libraries",
        json={
            "name": "Jobs",
            "root_path": str(app_env["library"]),
            "confirmed": True,
            "statement": "I confirm that every person depicted in this library is a consenting adult.",
        },
    )
    library_id = created.json()["id"]
    listed = client.get("/api/jobs", params={"library_id": library_id}).json()
    if not listed:
        scan = client.post(f"/api/libraries/{library_id}/scan")
        assert scan.status_code == 202
        listed = client.get("/api/jobs", params={"library_id": library_id}).json()
    assert listed, "expected at least one job after scan"
    job_id = listed[0]["id"]
    one = client.get(f"/api/jobs/{job_id}")
    assert one.status_code == 200
    assert one.json()["id"] == job_id
    assert client.get("/api/jobs/00000000-0000-0000-0000-000000000000").status_code == 404
