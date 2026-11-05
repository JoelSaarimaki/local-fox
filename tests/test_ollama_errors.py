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

def test_the_ollama_library_s_connection_error_says_how_to_start_ollama():
    # The ollama library raises Python's own ConnectionError for a refused connection, not httpx's ConnectError,
    # so it got only "The connection to Ollama failed."
    error = ConnectionError("Failed to connect to Ollama. Please check that Ollama is downloaded, running and accessible.")
    text = explain_connection(error, "http://127.0.0.1:11434")
    assert "not running" in text and "'ollama serve'" in text and '"Start Ollama server"' in text
    remote = explain_connection(error, "http://192.168.1.20:11434")
    assert "Start Ollama on that computer" in remote and "ollama serve" not in remote.split("The error:")[0]
    assert "middle of the reply" in explain_connection(ConnectionResetError("reset"), "http://127.0.0.1:11434")
    assert "send your message again" in text

def test_the_check_at_the_start_does_not_ask_to_send_again():
    # The model's check at the start of a conversation fails before the user has sent anything
    error = ConnectionError("Failed to connect to Ollama")
    text = explain_connection(error, "http://127.0.0.1:11434", sent=False)
    assert "not running" in text and "'ollama serve'" in text
    assert "send your message again" not in text and "ask again" not in text
    assert "ask again" not in explain_connection(httpx.ReadTimeout("slow"), "h", sent=False)

def test_a_session_started_without_ollama_does_not_ask_to_send_again(tmp_path):
    import asyncio

    from local_fox import config
    from local_fox.session import Session
    (tmp_path / ".local-fox").mkdir()
    session = Session(dict(config.DEFAULTS), tmp_path, log_dir=tmp_path / "logs")

    class Down:
        async def show(self, model):
            raise ConnectionError("Failed to connect to Ollama")
    session.client = Down()
    error = asyncio.run(session.load_model_info())
    assert "not running" in error and "send your message again" not in error
