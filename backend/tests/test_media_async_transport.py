import asyncio
import threading
from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.services import media_storage


async def _fake_read(_file):
    return b"raw-image"


def _fake_sanitize(_content, _content_type):
    return b"sanitized-image", "image/png", ".png"


def _settings(*, concurrency: int = 2, storage: str = "s3"):
    return SimpleNamespace(
        media_storage=storage,
        media_public_base_url="https://cdn.flashin.test",
        media_local_dir="media",
        media_io_max_concurrency=concurrency,
        s3_bucket="flashin-media",
        s3_region="auto",
        s3_endpoint_url="https://storage.flashin.test",
        s3_access_key_id="access",
        s3_secret_access_key="secret",
        s3_connect_timeout_seconds=5,
        s3_read_timeout_seconds=20,
        s3_max_attempts=3,
    )


class _Upload:
    filename = "upload.png"
    content_type = "image/png"


def _patch_payload(monkeypatch, *, concurrency: int = 2):
    monkeypatch.setattr(media_storage, "get_settings", lambda: _settings(concurrency=concurrency))
    monkeypatch.setattr(media_storage, "_read_limited", _fake_read)
    monkeypatch.setattr(media_storage, "_sanitize_image", _fake_sanitize)


async def _wait_thread_event(event: threading.Event, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not event.is_set():
        if loop.time() >= deadline:
            raise AssertionError("provider worker did not start")
        await asyncio.sleep(0.005)


def test_slow_s3_upload_does_not_block_event_loop(monkeypatch):
    _patch_payload(monkeypatch, concurrency=2)
    started = threading.Event()
    release = threading.Event()
    watchdog = threading.Timer(1.0, release.set)
    watchdog.daemon = True

    class _SlowS3:
        def put_object(self, **_kwargs):
            started.set()
            release.wait(timeout=2.0)
            return {"ETag": '"slow"'}

    monkeypatch.setattr(media_storage, "_s3_client", lambda: _SlowS3())

    async def scenario():
        watchdog.start()
        task = asyncio.create_task(media_storage.save_media(_Upload()))
        try:
            await _wait_thread_event(started)
            assert release.is_set() is False
            ticks = 0
            deadline = asyncio.get_running_loop().time() + 0.08
            while asyncio.get_running_loop().time() < deadline:
                ticks += 1
                await asyncio.sleep(0.005)
            assert ticks >= 5
            assert task.done() is False
            assert release.is_set() is False
            release.set()
            result = await asyncio.wait_for(task, timeout=1.0)
            assert result["storage_key"].endswith(".png")
        finally:
            release.set()
            watchdog.cancel()

    asyncio.run(scenario())


def test_provider_exception_propagates_from_offloaded_s3_call(monkeypatch):
    _patch_payload(monkeypatch, concurrency=2)

    class _FailingS3:
        def put_object(self, **_kwargs):
            raise TimeoutError("slow provider")

    monkeypatch.setattr(media_storage, "_s3_client", lambda: _FailingS3())

    async def scenario():
        with pytest.raises(media_storage.MediaStorageWriteError) as exc_info:
            await media_storage.save_media(_Upload())
        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert exc_info.value.storage_key.endswith(".png")

    asyncio.run(scenario())


def test_s3_transport_concurrency_is_hard_bounded(monkeypatch):
    _patch_payload(monkeypatch, concurrency=2)
    release = threading.Event()
    started = threading.Event()
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0, "started": 0}

    class _BoundedS3:
        def put_object(self, **_kwargs):
            with lock:
                state["active"] += 1
                state["started"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                if state["started"] >= 2:
                    started.set()
            try:
                release.wait(timeout=2.0)
                return {"ETag": '"bounded"'}
            finally:
                with lock:
                    state["active"] -= 1

    provider = _BoundedS3()
    monkeypatch.setattr(media_storage, "_s3_client", lambda: provider)

    async def scenario():
        tasks = [asyncio.create_task(media_storage.save_media(_Upload())) for _ in range(5)]
        try:
            await _wait_thread_event(started)
            await asyncio.sleep(0.05)
            with lock:
                assert state["max_active"] == 2
                assert state["started"] == 2
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)
            with lock:
                assert state["started"] == 5
                assert state["max_active"] == 2
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()

    asyncio.run(scenario())


def test_cancellation_keeps_capacity_reserved_until_provider_call_finishes(monkeypatch):
    _patch_payload(monkeypatch, concurrency=1)
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()
    lock = threading.Lock()
    calls = {"count": 0}

    class _BlockingS3:
        def put_object(self, **_kwargs):
            with lock:
                calls["count"] += 1
                call_number = calls["count"]
            if call_number == 1:
                first_started.set()
                first_release.wait(timeout=2.0)
            else:
                second_started.set()
            return {"ETag": f'"{call_number}"'}

    provider = _BlockingS3()
    monkeypatch.setattr(media_storage, "_s3_client", lambda: provider)

    async def scenario():
        first = asyncio.create_task(media_storage.save_media(_Upload()))
        await _wait_thread_event(first_started)
        first.cancel()
        with pytest.raises(media_storage.MediaStorageCancelled) as exc_info:
            await first
        assert exc_info.value.storage_key.endswith(".png")

        second = asyncio.create_task(media_storage.save_media(_Upload()))
        await asyncio.sleep(0.06)
        assert second_started.is_set() is False
        with lock:
            assert calls["count"] == 1

        first_release.set()
        await _wait_thread_event(second_started)
        await asyncio.wait_for(second, timeout=1.0)
        with lock:
            assert calls["count"] == 2

    try:
        asyncio.run(scenario())
    finally:
        first_release.set()


def test_cancellation_while_waiting_for_capacity_never_enters_provider(monkeypatch):
    _patch_payload(monkeypatch, concurrency=1)
    first_started = threading.Event()
    first_release = threading.Event()
    lock = threading.Lock()
    calls = {"count": 0}

    class _BlockingS3:
        def put_object(self, **_kwargs):
            with lock:
                calls["count"] += 1
                call_number = calls["count"]
            if call_number == 1:
                first_started.set()
                first_release.wait(timeout=2.0)
            return {"ETag": f'"{call_number}"'}

    provider = _BlockingS3()
    monkeypatch.setattr(media_storage, "_s3_client", lambda: provider)

    async def scenario():
        first = asyncio.create_task(media_storage.save_media(_Upload()))
        await _wait_thread_event(first_started)

        queued = asyncio.create_task(media_storage.save_media(_Upload()))
        await asyncio.sleep(0.03)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await queued
        assert isinstance(exc_info.value, media_storage.MediaStorageCancelled) is False
        with lock:
            assert calls["count"] == 1

        first_release.set()
        await asyncio.wait_for(first, timeout=1.0)

    try:
        asyncio.run(scenario())
    finally:
        first_release.set()


def test_media_io_concurrency_configuration_is_bounded():
    with pytest.raises(ValueError, match="MEDIA_IO_MAX_CONCURRENCY must be between 1 and 8"):
        Settings(
            telegram_bot_token="test-token",
            jwt_secret="test-secret",
            media_io_max_concurrency=9,
        )
