"""
Tests of the explanations of Ollama's errors: each known failure gets what to try, with Ollama's own message after it.
"""
import httpx

from local_fox.ollama_errors import explain, explain_connection

def test_known_errors_are_explained():
    cases = {
        "model requires more system memory (12.3 GiB) than is available (8.0 GiB)": "ran out of memory with a context of 32 000 tokens",
        "llama runner process has terminated: cudaMalloc failed: out of memory": "ran out of memory",
        "registry.ollama.ai/library/llama2 does not support tools": "cannot call tools",
        'model "flint:40b" not found, try pulling it first': "ollama pull flint:40b",
        "llama runner process has terminated: exit status 2": "The process that runs flint:40b stopped",
        "timed out waiting for llama runner to start - progress 0.00": "took too long to load",
        "flint:40b does not support thinking": "cannot think",
    }
    for message, expected in cases.items():
        text = explain(message, "flint:40b", 32000)
        assert expected in text and text.endswith(f"Ollama's message: {message}"), message

def test_unknown_errors_are_shown_as_they_are():
    assert explain("something new", "m", 4096) == "Ollama: something new"

def test_connection_errors():
    assert "not running" in explain_connection(httpx.ConnectError("refused"), "http://127.0.0.1:11434")
    assert "closed the connection in the middle of the reply" in explain_connection(httpx.RemoteProtocolError("peer closed"), "h")
