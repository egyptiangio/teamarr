"""Ollama API client for AI-powered stream parsing."""

import logging
from dataclasses import dataclass
import httpx
import json

logger = logging.getLogger(__name__)


@dataclass
class OllamaConfig:
    """Configuration for Ollama client."""
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    timeout: float = 60.0


class OllamaClient:
    """HTTP client for Ollama API."""

    def __init__(self, config: OllamaConfig | None = None):
        self.config = config or OllamaConfig()
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                timeout=self.config.timeout,
            )
        return self._client

    def generate(
        self,
        prompt: str,
        *,
        json_format: bool = True,
        temperature: float = 0.1,
    ) -> dict | str | None:
        """Generate a response from Ollama.

        Args:
            prompt: The prompt to send
            json_format: If True, request JSON output and parse it
            temperature: Sampling temperature (lower = more deterministic)

        Returns:
            Parsed JSON dict if json_format=True, raw string otherwise, None on error
        """
        try:
            payload = {
                "model": self.config.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                },
            }

            if json_format:
                payload["format"] = "json"

            response = self.client.post("/api/generate", json=payload)
            response.raise_for_status()

            data = response.json()
            raw_response = data.get("response", "")

            if json_format:
                try:
                    return json.loads(raw_response)
                except json.JSONDecodeError as e:
                    logger.warning("[AI] Failed to parse JSON response: %s", e)
                    logger.debug("[AI] Raw response: %s", raw_response[:500])
                    return None

            return raw_response

        except httpx.TimeoutException:
            logger.warning("[AI] Request timed out")
            return None
        except httpx.HTTPError as e:
            logger.warning("[AI] HTTP error: %s", e)
            return None
        except Exception as e:
            logger.exception("[AI] Unexpected error: %s", e)
            return None

    def is_available(self) -> bool:
        """Check if Ollama is reachable and model is loaded."""
        try:
            response = self.client.get("/api/tags")
            if response.status_code != 200:
                return False

            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]

            # Check if our model is available (handle tag variations)
            model_base = self.config.model.split(":")[0]
            return any(model_base in m for m in models)

        except Exception:
            return False

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
