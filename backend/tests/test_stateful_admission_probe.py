import asyncio

from scripts.stateful_admission_probe import (
    _PROBE_TTL_MS,
    _redis_capability_result,
    _redis_report,
)


class FakeRedis:
    def __init__(self, *, ping=True, raw=None, error=None):
        self._ping = ping
        self._raw = raw if raw is not None else [b"1720000000", b"123456", 1, _PROBE_TTL_MS - 1]
        self._error = error
        self.deleted = []
        self.closed = False
        self.eval_calls = []

    async def ping(self):
        if self._error:
            raise self._error
        return self._ping

    async def eval(self, script, key_count, key, member, ttl_ms):
        self.eval_calls.append((script, key_count, key, member, ttl_ms))
        if self._error:
            raise self._error
        return self._raw

    async def delete(self, key):
        self.deleted.append(key)
        return 1

    async def aclose(self):
        self.closed = True


def test_redis_capability_result_requires_time_eval_sorted_set_and_ttl():
    assert _redis_capability_result([b"1720000000", b"123456", 1, 9999]) == {
        "server_time": True,
        "lua_eval": True,
        "sorted_set": True,
        "ttl": True,
    }
    assert _redis_capability_result([b"0", b"0", 0, -1]) == {
        "server_time": False,
        "lua_eval": True,
        "sorted_set": False,
        "ttl": False,
    }
    assert _redis_capability_result([b"bad"]) == {
        "server_time": False,
        "lua_eval": False,
        "sorted_set": False,
        "ttl": False,
    }


def test_redis_report_proves_algorithm_compatibility_and_redacts_ephemeral_key():
    client = FakeRedis()
    report = asyncio.run(_redis_report("rediss://user:secret@example.invalid:6379", client=client))

    assert report["reachable"] is True
    assert report["compatible"] is True
    assert report["scheme"] == "rediss"
    assert report["transport_scheme"] == "rediss"
    assert all(report["capabilities"].values())
    assert client.eval_calls
    assert "TIME" in client.eval_calls[0][0]
    assert "ZADD" in client.eval_calls[0][0]
    assert "PEXPIRE" in client.eval_calls[0][0]
    assert client.deleted == [client.eval_calls[0][2]]
    assert client.closed is True
    rendered = repr(report)
    assert "secret" not in rendered
    assert "example.invalid" not in rendered
    assert client.eval_calls[0][2] not in rendered


def test_redis_report_fails_closed_when_required_primitive_is_missing():
    client = FakeRedis(raw=[b"1720000000", b"123456", 0, -1])
    report = asyncio.run(_redis_report("valkeys://example.invalid:6379", client=client))

    assert report["reachable"] is True
    assert report["compatible"] is False
    assert report["error_code"] == "rate_limit_store_incompatible"
    assert report["transport_scheme"] == "rediss"
    assert client.closed is True


def test_redis_report_redacts_transport_exceptions_and_still_cleans_up():
    client = FakeRedis(error=RuntimeError("credential-bearing-provider-detail"))
    report = asyncio.run(_redis_report("redis://example.invalid:6379", client=client))

    assert report["reachable"] is False
    assert report["compatible"] is False
    assert report["error_code"] == "rate_limit_store_probe_failed"
    assert "credential-bearing-provider-detail" not in repr(report)
    assert client.closed is True
