"""
Pytest unit and integration tests for solution.py.

Run with:
  pytest -v test_upload.py

Tests require reachable PostgreSQL and Redis instances. Set DATABASE_URL,
REDIS_URL and CHUNK_STORAGE to isolated test resources before running.
"""

import asyncio
import hashlib
import shutil
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

import solution


@pytest_asyncio.fixture
async def client_and_namespace(tmp_path, monkeypatch):
    """Use a temporary filesystem directory and unique test IDs."""
    temp_dir = tmp_path / "chunks"
    final_dir = tmp_path / "completed"
    temp_dir.mkdir()
    final_dir.mkdir()

    monkeypatch.setattr(solution, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(solution, "FINAL_DIR", final_dir)

    async with solution.engine.begin() as connection:
        await connection.run_sync(solution.Base.metadata.create_all)

    namespace = f"test_{uuid.uuid4().hex}"

    transport = httpx.ASGITransport(app=solution.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client, namespace, temp_dir, final_dir

    # Remove Redis keys belonging to this test only.
    keys = []
    async for key in solution.redis.scan_iter(match=f"upload:{namespace}*"):
        keys.append(key)
    async for key in solution.redis.scan_iter(match=f"upload-lock:{namespace}*"):
        keys.append(key)
    if keys:
        await solution.redis.delete(*keys)

    # Remove database rows belonging to this test.
    async with solution.Session() as db:
        record = await db.get(solution.CompletedUpload, namespace)
        if record is not None:
            await db.delete(record)
            await db.commit()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def post_chunk(client, file_id, index, total, content, checksum=None):
    return await client.post(
        "/api/chunks/upload",
        data={
            "file_id": file_id,
            "chunk_index": str(index),
            "total_chunks": str(total),
            "checksum": checksum or digest(content),
        },
        files={"file": ("chunk.bin", content, "application/octet-stream")},
    )


def test_save_chunk_deduplicates_content(tmp_path, monkeypatch):
    monkeypatch.setattr(solution, "TEMP_DIR", tmp_path)
    content = b"same bytes"
    checksum = digest(content)

    first = solution.save_chunk(content, checksum)
    second = solution.save_chunk(content, checksum)

    assert first == second
    assert first.read_bytes() == content
    assert len(list(tmp_path.iterdir())) == 1


def test_save_chunk_rejects_wrong_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(solution, "TEMP_DIR", tmp_path)
    with pytest.raises(ValueError):
        solution.save_chunk(b"content", "0" * 64)
    assert list(tmp_path.iterdir()) == []


def test_assemble_file_preserves_order(tmp_path, monkeypatch):
    chunk_dir = tmp_path / "chunks"
    final_dir = tmp_path / "completed"
    chunk_dir.mkdir()
    final_dir.mkdir()

    monkeypatch.setattr(solution, "TEMP_DIR", chunk_dir)
    monkeypatch.setattr(solution, "FINAL_DIR", final_dir)

    parts = [b"alpha-", b"beta-", b"gamma"]
    hashes = []
    for part in parts:
        checksum = digest(part)
        (chunk_dir / checksum).write_bytes(part)
        hashes.append(checksum)

    output = solution.assemble_file("unit_file", hashes)
    assert output.read_bytes() == b"alpha-beta-gamma"


def test_assemble_file_fails_for_missing_chunk(tmp_path, monkeypatch):
    chunk_dir = tmp_path / "chunks"
    final_dir = tmp_path / "completed"
    chunk_dir.mkdir()
    final_dir.mkdir()

    monkeypatch.setattr(solution, "TEMP_DIR", chunk_dir)
    monkeypatch.setattr(solution, "FINAL_DIR", final_dir)

    with pytest.raises(FileNotFoundError):
        solution.assemble_file("missing_file", ["a" * 64])


@pytest.mark.asyncio
async def test_checksum_mismatch_rejected(client_and_namespace):
    client, namespace, temp_dir, _ = client_and_namespace
    file_id = namespace + "_bad_checksum"

    response = await post_chunk(
        client,
        file_id,
        0,
        1,
        b"actual payload",
        checksum=digest(b"different payload"),
    )

    assert response.status_code == 400
    assert "checksum mismatch" in response.json()["detail"].lower()
    assert list(temp_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_single_chunk_upload_persists_file_and_database_record(
    client_and_namespace,
):
    client, namespace, _, _ = client_and_namespace
    file_id = namespace + "_single"
    payload = b"one complete file"

    response = await post_chunk(client, file_id, 0, 1, payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert Path(body["storage_path"]).read_bytes() == payload

    async with solution.Session() as db:
        record = await db.get(solution.CompletedUpload, file_id)

    assert record is not None
    assert record.total_chunks == 1


@pytest.mark.asyncio
async def test_out_of_order_chunks_are_assembled_in_index_order(
    client_and_namespace,
):
    client, namespace, _, _ = client_and_namespace
    file_id = namespace + "_out_of_order"
    parts = [b"first-", b"second-", b"third"]

    response = await post_chunk(client, file_id, 2, 3, parts[2])
    assert response.status_code == 200
    assert response.json()["status"] == "uploading"

    response = await post_chunk(client, file_id, 0, 3, parts[0])
    assert response.status_code == 200
    assert response.json()["status"] == "uploading"

    response = await post_chunk(client, file_id, 1, 3, parts[1])
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert Path(response.json()["storage_path"]).read_bytes() == b"".join(parts)


@pytest.mark.asyncio
async def test_concurrent_chunk_uploads_complete_correctly(
    client_and_namespace,
):
    client, namespace, _, _ = client_and_namespace
    file_id = namespace + "_concurrent"
    parts = [f"part-{i}-".encode() for i in range(8)]

    responses = await asyncio.gather(
        *[
            post_chunk(client, file_id, i, len(parts), part)
            for i, part in enumerate(parts)
        ]
    )

    assert all(response.status_code == 200 for response in responses)
    completed = [
        response.json()
        for response in responses
        if response.json()["status"] == "completed"
    ]
    assert completed

    final_path = Path(completed[-1]["storage_path"])
    assert final_path.read_bytes() == b"".join(parts)

    async with solution.Session() as db:
        record = await db.get(solution.CompletedUpload, file_id)
    assert record is not None
    assert record.total_chunks == len(parts)


@pytest.mark.asyncio
async def test_identical_duplicate_chunk_is_idempotent(client_and_namespace):
    client, namespace, _, _ = client_and_namespace
    file_id = namespace + "_duplicate"
    payload = b"repeat me"

    first = await post_chunk(client, file_id, 0, 2, payload)
    second = await post_chunk(client, file_id, 0, 2, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["received_chunks"] == 1
    assert second.json()["received_chunks"] == 1


@pytest.mark.asyncio
async def test_conflicting_duplicate_chunk_is_rejected(client_and_namespace):
    client, namespace, _, _ = client_and_namespace
    file_id = namespace + "_conflict"

    first = await post_chunk(client, file_id, 0, 2, b"version one")
    second = await post_chunk(client, file_id, 0, 2, b"version two")

    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_total_chunk_count_cannot_change(client_and_namespace):
    client, namespace, _, _ = client_and_namespace
    file_id = namespace + "_manifest"

    first = await post_chunk(client, file_id, 0, 2, b"part one")
    second = await post_chunk(client, file_id, 1, 3, b"part two")

    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_invalid_chunk_index_rejected(client_and_namespace):
    client, namespace, _, _ = client_and_namespace
    response = await post_chunk(
        client,
        namespace + "_invalid_index",
        2,
        2,
        b"invalid",
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_upload_not_assembled_until_all_chunks_arrive(
    client_and_namespace,
):
    client, namespace, _, final_dir = client_and_namespace
    file_id = namespace + "_incomplete"

    response = await post_chunk(client, file_id, 0, 3, b"first")
    assert response.status_code == 200
    assert response.json()["status"] == "uploading"
    assert not (final_dir / f"{file_id}.complete").exists()


@pytest.mark.asyncio
async def test_status_endpoint_reports_progress(client_and_namespace):
    client, namespace, _, _ = client_and_namespace
    file_id = namespace + "_status"

    upload = await post_chunk(client, file_id, 0, 2, b"first")
    assert upload.status_code == 200

    response = await client.get(f"/api/chunks/{file_id}/status")
    assert response.status_code == 200
    assert response.json()["received_chunks"] == 1
    assert response.json()["missing_chunks"] == 1
