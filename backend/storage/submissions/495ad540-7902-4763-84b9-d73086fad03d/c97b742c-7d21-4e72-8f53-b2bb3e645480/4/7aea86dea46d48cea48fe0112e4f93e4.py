
# ============================================================
# UNIT AND INTEGRATION TESTS
# ============================================================

import pytest
import pytest_asyncio
import httpx
import tempfile
import shutil


@pytest_asyncio.fixture
async def test_environment(monkeypatch):
    """
    Prepare isolated storage and clean Redis/PostgreSQL state
    for each integration test.
    """

    test_storage = Path(tempfile.mkdtemp(prefix="chunk_test_"))
    test_temp = test_storage / "chunks"
    test_final = test_storage / "completed"

    test_temp.mkdir(parents=True, exist_ok=True)
    test_final.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "solution.TEMP_DIR",
        test_temp,
    )
    monkeypatch.setattr(
        "solution.FINAL_DIR",
        test_final,
    )

    # Ensure the database table exists.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    # Use a unique namespace for every test.
    prefix = f"test-{uuid.uuid4().hex}"
    original_redis = redis

    yield {
        "prefix": prefix,
        "temp_dir": test_temp,
        "final_dir": test_final,
    }

    # Remove test upload state and progress keys.
    keys = []
    async for key in original_redis.scan_iter(
        match=f"upload:{prefix}*"
    ):
        keys.append(key)

    async for key in original_redis.scan_iter(
        match=f"upload-lock:{prefix}*"
    ):
        keys.append(key)

    if keys:
        await original_redis.delete(*keys)

    # Remove test database records.
    async with Session() as db:
        await db.execute(
            CompletedUpload.__table__.delete().where(
                CompletedUpload.file_id.like(f"{prefix}%")
            )
        )
        await db.commit()

    shutil.rmtree(test_storage, ignore_errors=True)


@pytest_asyncio.fixture
async def test_client():
    """
    Create an asynchronous HTTP client for the FastAPI app.
    """

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client


def make_chunk(content: bytes):
    """
    Generate a chunk and its correct SHA-256 checksum.
    """

    checksum = hashlib.sha256(content).hexdigest()

    return {
        "file": ("chunk.bin", content, "application/octet-stream"),
        "checksum": checksum,
    }


# ---------------- UNIT TESTS ----------------

def test_save_chunk_deduplicates_identical_content(
    monkeypatch,
    tmp_path,
):
    """
    Identical content should use the same hash-based storage path.
    """

    monkeypatch.setattr(
        "solution.TEMP_DIR",
        tmp_path,
    )

    content = b"duplicate chunk data"
    checksum = hashlib.sha256(content).hexdigest()

    first_path = save_chunk(content, checksum)
    second_path = save_chunk(content, checksum)

    assert first_path == second_path
    assert first_path.read_bytes() == content
    assert len(list(tmp_path.iterdir())) == 1


def test_assemble_file_preserves_chunk_order(
    monkeypatch,
    tmp_path,
):
    """
    Assembly must follow the supplied hash order.
    """

    chunk_dir = tmp_path / "chunks"
    final_dir = tmp_path / "completed"

    chunk_dir.mkdir()
    final_dir.mkdir()

    monkeypatch.setattr("solution.TEMP_DIR", chunk_dir)
    monkeypatch.setattr("solution.FINAL_DIR", final_dir)

    chunks = [b"first-", b"second-", b"third"]
    hashes = []

    for content in chunks:
        checksum = hashlib.sha256(content).hexdigest()
        (chunk_dir / checksum).write_bytes(content)
        hashes.append(checksum)

    result = assemble_file("unit-test-file", hashes)

    assert result.read_bytes() == b"first-second-third"


def test_assemble_file_fails_when_chunk_is_missing(
    monkeypatch,
    tmp_path,
):
    """
    Assembly must fail rather than silently create an incomplete file.
    """

    chunk_dir = tmp_path / "chunks"
    final_dir = tmp_path / "completed"

    chunk_dir.mkdir()
    final_dir.mkdir()

    monkeypatch.setattr("solution.TEMP_DIR", chunk_dir)
    monkeypatch.setattr("solution.FINAL_DIR", final_dir)

    with pytest.raises(FileNotFoundError):
        assemble_file("missing-test", ["a" * 64])


# ---------------- INTEGRATION TEST HELPERS ----------------

async def send_chunk(
    client,
    file_id,
    index,
    total,
    content,
    checksum=None,
):
    actual_checksum = checksum or hashlib.sha256(
        content
    ).hexdigest()

    return await client.post(
        "/api/chunks/upload",
        data={
            "file_id": file_id,
            "chunk_index": str(index),
            "total_chunks": str(total),
            "checksum": actual_checksum,
        },
        files={
            "file": (
                "chunk.bin",
                content,
                "application/octet-stream",
            )
        },
    )


# ---------------- INTEGRATION TESTS ----------------

@pytest.mark.asyncio
async def test_valid_single_chunk_upload(
    test_environment,
    test_client,
):
    """
    A valid single-chunk upload should complete immediately.
    """

    file_id = test_environment["prefix"] + "-single"
    content = b"hello cloud storage"

    response = await send_chunk(
        test_client,
        file_id,
        0,
        1,
        content,
    )

    assert response.status_code == 200

    result = response.json()

    assert result["status"] == "completed"
    assert result["file_id"] == file_id

    final_path = Path(result["storage_path"])

    assert final_path.exists()
    assert final_path.read_bytes() == content

    async with Session() as db:
        record = await db.get(CompletedUpload, file_id)

    assert record is not None
    assert record.total_chunks == 1


@pytest.mark.asyncio
async def test_checksum_mismatch_is_rejected(
    test_environment,
    test_client,
):
    """
    Invalid chunk content must be rejected before storage.
    """

    file_id = test_environment["prefix"] + "-bad-hash"
    content = b"original content"

    wrong_checksum = hashlib.sha256(
        b"different content"
    ).hexdigest()

    response = await send_chunk(
        test_client,
        file_id,
        0,
        1,
        content,
        checksum=wrong_checksum,
    )

    assert response.status_code == 400
    assert "checksum mismatch" in response.json()["detail"].lower()

    # No chunk should have been persisted.
    assert list(
        test_environment["temp_dir"].iterdir()
    ) == []


@pytest.mark.asyncio
async def test_out_of_order_chunk_arrival(
    test_environment,
    test_client,
):
    """
    Chunks arriving in reverse order must be assembled correctly.
    """

    file_id = test_environment["prefix"] + "-out-of-order"

    chunks = [
        b"first-",
        b"second-",
        b"third",
    ]

    response = await send_chunk(
        test_client,
        file_id,
        2,
        3,
        chunks[2],
    )

    assert response.status_code == 200
    assert response.json()["status"] == "uploading"

    response = await send_chunk(
        test_client,
        file_id,
        1,
        3,
        chunks[1],
    )

    assert response.status_code == 200
    assert response.json()["status"] == "uploading"

    response = await send_chunk(
        test_client,
        file_id,
        0,
        3,
        chunks[0],
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    final_path = Path(response.json()["storage_path"])

    assert final_path.read_bytes() == b"first-second-third"


@pytest.mark.asyncio
async def test_concurrent_chunk_uploads(
    test_environment,
    test_client,
):
    """
    Multiple chunks uploaded concurrently must complete once
    all chunk indexes have been received.
    """

    file_id = test_environment["prefix"] + "-concurrent"

    chunks = [
        b"chunk-0-",
        b"chunk-1-",
        b"chunk-2-",
        b"chunk-3",
    ]

    responses = await asyncio.gather(
        *[
            send_chunk(
                test_client,
                file_id,
                index,
                len(chunks),
                content,
            )
            for index, content in enumerate(chunks)
        ]
    )

    assert all(
        response.status_code == 200
        for response in responses
    )

    completed = [
        response.json()
        for response in responses
        if response.json()["status"] == "completed"
    ]

    assert len(completed) >= 1

    final_path = Path(completed[-1]["storage_path"])

    assert final_path.read_bytes() == b"".join(chunks)

    async with Session() as db:
        record = await db.get(CompletedUpload, file_id)

    assert record is not None
    assert record.total_chunks == len(chunks)


@pytest.mark.asyncio
async def test_duplicate_chunk_is_idempotent(
    test_environment,
    test_client,
):
    """
    Uploading the same chunk index and content twice must not
    increase the received chunk count.
    """

    file_id = test_environment["prefix"] + "-duplicate"
    content = b"same chunk"

    first = await send_chunk(
        test_client,
        file_id,
        0,
        2,
        content,
    )

    second = await send_chunk(
        test_client,
        file_id,
        0,
        2,
        content,
    )

    assert first.status_code == 200
    assert second.status_code == 200

    assert first.json()["received_chunks"] == 1
    assert second.json()["received_chunks"] == 1


@pytest.mark.asyncio
async def test_conflicting_duplicate_chunk_is_rejected(
    test_environment,
    test_client,
):
    """
    A different payload cannot replace an already received index.
    """

    file_id = test_environment["prefix"] + "-conflict"

    first = await send_chunk(
        test_client,
        file_id,
        0,
        2,
        b"first version",
    )

    second = await send_chunk(
        test_client,
        file_id,
        0,
        2,
        b"different version",
    )

    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_invalid_chunk_index_is_rejected(
    test_environment,
    test_client,
):
    """
    Chunk indexes must be within the range 0 to total_chunks - 1.
    """

    file_id = test_environment["prefix"] + "-invalid-index"

    response = await send_chunk(
        test_client,
        file_id,
        3,
        3,
        b"invalid index",
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_file_is_not_assembled_until_all_chunks_arrive(
    test_environment,
    test_client,
):
    """
    Missing chunks must prevent premature final assembly.
    """

    file_id = test_environment["prefix"] + "-incomplete"

    response = await send_chunk(
        test_client,
        file_id,
        0,
        3,
        b"first",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "uploading"

    assert not (
        test_environment["final_dir"] /
        f"{file_id}.complete"
    ).exists()
