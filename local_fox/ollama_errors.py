"""
What went wrong with Ollama, in words the user can act on. Ollama's own messages are short and technical ("cudaMalloc
failed: out of memory", "llama runner process has terminated: exit status 2"), so the feed shows an explanation
with what to try first, and Ollama's message after it, as it came, for the details.
"""
import httpx

from .ollama_server import is_local

# Ollama's log, where a crashed runner tells why
OLLAMA_LOG = r"%LOCALAPPDATA%\Ollama\server.log (Windows) or ~/.ollama/logs/server.log"

_MEMORY = ("out of memory", "requires more system memory", "insufficient memory", "not enough memory", "failed to allocate",
           "unable to allocate", "cudamalloc", "alloc_buffer", "memory allocation", "oom")

def explain(message: str, model: str, num_ctx: int) -> str:
    """
    Returns an explanation of an error message of Ollama, with Ollama's message at its end, or the message as it is
    if it is not one Local Fox knows.
    """
    text = message.lower()
    context = f"{num_ctx:,}".replace(",", " ")
    if any(word in text for word in _MEMORY):
        explanation = (f"{model} ran out of memory with a context of {context} tokens. The model and its context must fit "
                       "in the memory of the GPU and the computer. Try a smaller context (click it in the top bar, or /context), "
                       "close other programs that use the GPU, or a smaller model.")
    elif "does not support tools" in text:
        explanation = f"{model} cannot call tools, which Local Fox needs. Choose another model in the top bar."
    elif "does not support thinking" in text:
        explanation = (f"{model} cannot think, but the setting think asks for it. Set think to empty (automatic) in the "
                       "settings (/settings).")
    elif "not found" in text and ("model" in text or "pull" in text):
        explanation = f"Ollama does not have {model}. Install it with 'ollama pull {model}', or choose another model in the top bar."
    elif "timed out waiting for llama runner" in text or "timed out waiting for" in text:
        explanation = (f"{model} took too long to load. A large model can take minutes the first time, and longer if part "
                       "of it does not fit in the GPU. Ask again; if it keeps happening, try a smaller context or model.")
    elif "runner process has terminated" in text or "runner process no longer running" in text or "exit status" in text:
        explanation = (f"The process that runs {model} stopped. This is often a lack of memory: try a smaller context or "
                       f"close other programs that use the GPU. Ollama's log tells why: {OLLAMA_LOG}.")
    else:
        return f"Ollama: {message}"
    return f"{explanation}\n\nOllama's message: {message}"

def explain_connection(error: Exception, host: str, sent: bool = True) -> str:
    """
    Returns an explanation of a failed connection to Ollama: not running, stopped in the middle of a reply, or too slow.
    sent is False for Local Fox's own check at the start of a conversation, which the user did not send: they are
    not told to send again what they have not sent yet.
    """
    if isinstance(error, (httpx.RemoteProtocolError, httpx.ReadError, ConnectionResetError, ConnectionAbortedError)):
        explanation = ("Ollama closed the connection in the middle of the reply. It may have crashed, often for lack of "
                       f"memory, or been restarted. Ask again; Ollama's log tells why: {OLLAMA_LOG}.")
    # The ollama library raises Python's own ConnectionError in place of httpx's ConnectError
    elif isinstance(error, (httpx.ConnectError, ConnectionError)):
        if is_local(host):
            start = ("Start the Ollama app, or run 'ollama serve' in a terminal (in the window, the \"Start Ollama server\" "
                     "button below the file list does that)")
        else:
            start = "Start Ollama on that computer"
        explanation = (f"Ollama is not running at {host}, or not reachable. {start}"
                       + (", and send your message again. " if sent else ". ")
                       + "If Ollama runs at another address, set ollama_host in the settings.")
    elif isinstance(error, httpx.TimeoutException):
        explanation = "Ollama did not answer in time. It may be busy loading the model" + (
            "; ask again in a moment." if sent else ".")
    else:
        explanation = f"The connection to Ollama at {host} failed."
    return f"{explanation}\n\nThe error: {type(error).__name__}: {error}"
