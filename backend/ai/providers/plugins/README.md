# Adding your own LLM

Tradeo has two AI slots:

- **Local** — Ollama on your machine. Used by default, for everything.
- **Cloud** (optional) — one more model, used for the trade double-check, the
  research agent, and (if you choose) chat and analysis.

There are three ways to use your own model. Pick the first one that fits.

## 1. Any Ollama model — change one line

```bash
ollama pull llama3.1:8b
```

```ini
# backend/.env
OLLAMA_MODEL=llama3.1:8b
```

Restart the backend. Any model on [ollama.com/library](https://ollama.com/library)
or a GGUF from Hugging Face (`ollama pull hf.co/<user>/<repo>:<quant>`) works.
Also settable on **Connections → AI** without a restart.

## 2. Any OpenAI-compatible API — no code

Most hosted APIs and local servers speak the OpenAI chat-completions format.
Set `CLOUD_LLM_PROVIDER=custom` plus three keys:

```ini
CLOUD_LLM_PROVIDER=custom
CLOUD_BASE_URL=https://api.groq.com/openai/v1
CLOUD_API_KEY=your-key
CLOUD_MODEL=llama-3.3-70b-versatile
```

| Service | `CLOUD_BASE_URL` |
|---|---|
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` |
| Groq | `https://api.groq.com/openai/v1` |
| Mistral | `https://api.mistral.ai/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| Together AI | `https://api.together.xyz/v1` |
| Anthropic (OpenAI-compatible layer) | `https://api.anthropic.com/v1` |
| LM Studio (local) | `http://localhost:1234/v1` |
| llama.cpp server (local) | `http://localhost:8080/v1` |
| vLLM (local) | `http://localhost:8001/v1` — start it on a port other than 8000, which Tradeo uses |

Check each service's docs for its current model names. For local servers
that don't need a key, put any placeholder in `CLOUD_API_KEY` (e.g. `local`)
— Tradeo treats an empty key as "not configured".

OpenRouter (`CLOUD_LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY`) and
OpenAI (`openai`, `OPENAI_API_KEY`) have their own keys and are set up the
same way.

## 3. Anything else — a plugin (one Python file)

For an API with its own format:

```bash
cd backend/ai/providers/plugins
cp _example_provider.py mymodel.py     # names starting with "_" are ignored
```

Fill in the class (`name`, `is_available()`, `complete()`), then:

```ini
CLOUD_LLM_PROVIDER=mymodel     # your class's `name`
CLOUD_API_KEY=...              # read in your class as settings.cloud_api_key_custom
CLOUD_MODEL=...                # settings.cloud_model
CLOUD_BASE_URL=...             # settings.cloud_base_url
```

| Method | Required | Contract |
|---|---|---|
| `__init__(self, settings)` | yes | Receives Tradeo's settings. Don't make network calls here. |
| `model` (property) | yes | The model name, shown in the UI. |
| `is_available()` | yes | `True` when it can be used. Cheap, no network, never raises. |
| `complete(prompt, system, temperature, max_tokens, json_mode)` | yes | The full answer as a string. Raise `ProviderError` on failure — Tradeo then falls back to the local model. |
| `stream(...)` | no | Yield text chunks. Default: the whole answer at once. |
| `list_models()` | no | Model ids, for the Connections screen. |

The interface is in [`../base.py`](../base.py); the built-in
[`../openai_compat.py`](../openai_compat.py) is a complete example.

If the plugin fails to load, the backend log says why
(`LLM plugin mymodel.py failed to load: …`) and Tradeo carries on with the
built-in providers. The research agent needs the OpenAI format, so with a
plugin it stays on local Ollama.

## When is the cloud model used?

`AI_MODE` decides (default `local_only` — nothing leaves your machine):

| `AI_MODE` | Chat and analysis use |
|---|---|
| `local_only` | Ollama only. Add `VERIFY_WITH_CLOUD=true` to let the cloud model double-check trades. |
| `hybrid` | Cloud for nuanced tasks (chat, analysis), local for quick ones and voice |
| `local_first` / `cloud_first` | That tier first, the other as fallback |
| `cloud_only` | Your model for everything — use this to run on LM Studio, llama.cpp or vLLM instead of Ollama |

## Check it

```bash
curl -s localhost:8000/api/ai/status | python -m json.tool
```

`cloud.available` should be `true`, with your provider's name and model.
The **Connections** screen shows the same with a live health check.

## Rules for language models in Tradeo

A model can explain, summarise, read news and veto a trade. It never sets a
price, a stop, a target or a position size — those are computed in code. A
plugin that changes this won't be merged.
