import httpx
import pytest
import respx

from memescreen.providers.base import ProviderError, request_json


@respx.mock
def test_success_returns_parsed_json():
    respx.get("https://x.test/ok").mock(
        return_value=httpx.Response(200, json={"hello": "world"})
    )
    assert request_json("x", "GET", "https://x.test/ok") == {"hello": "world"}


@respx.mock
def test_4xx_raises_provider_error_with_status_in_reason():
    route = respx.get("https://x.test/nope").mock(return_value=httpx.Response(404))
    with pytest.raises(ProviderError) as ei:
        request_json("x", "GET", "https://x.test/nope")
    assert ei.value.source == "x"
    assert ei.value.reason.startswith("404")
    assert route.call_count == 1


@respx.mock
def test_retries_once_on_500_then_succeeds():
    route = respx.get("https://x.test/flaky").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": True})]
    )
    assert request_json("x", "GET", "https://x.test/flaky") == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_gives_up_after_retries_on_persistent_500():
    route = respx.get("https://x.test/down").mock(return_value=httpx.Response(500))
    with pytest.raises(ProviderError) as ei:
        request_json("x", "GET", "https://x.test/down")
    assert ei.value.reason.startswith("500")
    assert route.call_count == 2


@respx.mock
def test_non_json_body_raises_provider_error():
    respx.get("https://x.test/html").mock(
        return_value=httpx.Response(200, text="<html>nope</html>")
    )
    with pytest.raises(ProviderError):
        request_json("x", "GET", "https://x.test/html")


@respx.mock
def test_timeout_raises_provider_error():
    respx.get("https://x.test/slow").mock(side_effect=httpx.TimeoutException("slow"))
    with pytest.raises(ProviderError) as ei:
        request_json("x", "GET", "https://x.test/slow")
    assert "timeout" in ei.value.reason.lower()
