"""AI provider clients for external API services.

Supports:
- Groq: Fast inference with free tier (Llama, Mixtral)
- Gemini: Google's AI with free tier (Gemini 1.5 Flash)
- OpenRouter: Aggregator with many models, some free
- OpenAI: ChatGPT API (paid)
- Anthropic: Claude API (paid)
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Rate limit retry settings
# Groq free tier: 30 req/min, so we need longer delays
MAX_RETRIES = 5
BASE_DELAY = 5.0  # seconds (Groq needs ~2s minimum between requests)
MAX_DELAY = 60.0  # seconds


class AIProviderClient(ABC):
    """Base class for AI provider clients."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        json_format: bool = True,
        temperature: float = 0.1,
    ) -> dict | str | None:
        """Generate a response from the AI provider."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is reachable and configured."""
        pass

    @abstractmethod
    def close(self):
        """Close the client."""
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@dataclass
class GroqConfig:
    """Configuration for Groq client."""

    api_key: str = ""
    model: str = "llama-3.1-8b-instant"
    timeout: float = 60.0


class GroqClient(AIProviderClient):
    """HTTP client for Groq API.

    Groq offers fast inference with a free tier.
    API is OpenAI-compatible.
    """

    BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self, config: GroqConfig | None = None):
        self.config = config or GroqConfig()
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.BASE_URL,
                timeout=self.config.timeout,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "python-httpx/0.27",
                },
            )
        return self._client

    def generate(
        self,
        prompt: str,
        *,
        json_format: bool = True,
        temperature: float = 0.1,
    ) -> dict | str | None:
        """Generate a response from Groq."""
        if not self.config.api_key:
            logger.warning("[Groq] No API key configured")
            return None

        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }

        if json_format:
            payload["response_format"] = {"type": "json_object"}

        # Retry loop for rate limits
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.client.post("/chat/completions", json=payload)

                # Handle rate limiting with retry
                if response.status_code == 429:
                    if attempt < MAX_RETRIES:
                        # Get retry-after header or use exponential backoff
                        retry_after = response.headers.get("retry-after")
                        logger.debug("[Groq] 429 response headers: %s", dict(response.headers))
                        logger.debug("[Groq] 429 response body: %s", response.text[:500])
                        if retry_after:
                            delay = min(float(retry_after), MAX_DELAY)
                        else:
                            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                        logger.info("[Groq] Rate limited, waiting %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                        time.sleep(delay)
                        continue
                    else:
                        logger.warning("[Groq] Rate limited after %d retries", MAX_RETRIES)
                        return None

                response.raise_for_status()

                data = response.json()
                raw_response = data["choices"][0]["message"]["content"]

                if json_format:
                    try:
                        return json.loads(raw_response)
                    except json.JSONDecodeError as e:
                        logger.warning("[Groq] Failed to parse JSON response: %s", e)
                        return None

                return raw_response

            except httpx.TimeoutException:
                logger.warning("[Groq] Request timed out")
                return None
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < MAX_RETRIES:
                    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    logger.info("[Groq] Rate limited, waiting %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                    time.sleep(delay)
                    continue
                logger.warning("[Groq] HTTP error: %s", e)
                return None
            except httpx.HTTPError as e:
                logger.warning("[Groq] HTTP error: %s", e)
                return None
            except Exception as e:
                logger.exception("[Groq] Unexpected error: %s", e)
                return None

        return None

    def is_available(self) -> bool:
        """Check if Groq is reachable and API key is valid."""
        if not self.config.api_key:
            logger.warning("[Groq] No API key configured")
            return False
        try:
            response = self.client.get("/models")
            if response.status_code != 200:
                logger.warning("[Groq] API returned status %d: %s", response.status_code, response.text[:200])
            return response.status_code == 200
        except Exception as e:
            logger.warning("[Groq] Connection failed: %s", e)
            return False

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


@dataclass
class GeminiConfig:
    """Configuration for Google Gemini client."""

    api_key: str = ""
    model: str = "gemini-2.0-flash"
    timeout: float = 60.0


class GeminiClient(AIProviderClient):
    """HTTP client for Google Gemini API.

    Gemini offers a free tier for the Flash model.
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, config: GeminiConfig | None = None):
        self.config = config or GeminiConfig()
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.BASE_URL,
                timeout=self.config.timeout,
                headers={
                    "User-Agent": "python-httpx/0.27",
                },
            )
        return self._client

    def generate(
        self,
        prompt: str,
        *,
        json_format: bool = True,
        temperature: float = 0.1,
    ) -> dict | str | None:
        """Generate a response from Gemini."""
        if not self.config.api_key:
            logger.warning("[Gemini] No API key configured")
            return None

        url = f"/models/{self.config.model}:generateContent?key={self.config.api_key}"

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
            },
        }

        if json_format:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        # Retry loop for rate limits
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.client.post(url, json=payload)

                # Handle rate limiting with retry
                if response.status_code == 429:
                    if attempt < MAX_RETRIES:
                        retry_after = response.headers.get("retry-after")
                        if retry_after:
                            delay = min(float(retry_after), MAX_DELAY)
                        else:
                            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                        logger.info("[Gemini] Rate limited, waiting %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                        time.sleep(delay)
                        continue
                    else:
                        logger.warning("[Gemini] Rate limited after %d retries", MAX_RETRIES)
                        return None

                response.raise_for_status()

                data = response.json()
                raw_response = data["candidates"][0]["content"]["parts"][0]["text"]

                if json_format:
                    try:
                        return json.loads(raw_response)
                    except json.JSONDecodeError as e:
                        logger.warning("[Gemini] Failed to parse JSON response: %s", e)
                        return None

                return raw_response

            except httpx.TimeoutException:
                logger.warning("[Gemini] Request timed out")
                return None
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < MAX_RETRIES:
                    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    logger.info("[Gemini] Rate limited, waiting %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                    time.sleep(delay)
                    continue
                logger.warning("[Gemini] HTTP error: %s", e)
                return None
            except httpx.HTTPError as e:
                logger.warning("[Gemini] HTTP error: %s", e)
                return None
            except (KeyError, IndexError) as e:
                logger.warning("[Gemini] Unexpected response format: %s", e)
                return None
            except Exception as e:
                logger.exception("[Gemini] Unexpected error: %s", e)
                return None

        return None

    def is_available(self) -> bool:
        """Check if Gemini is reachable and API key is valid."""
        if not self.config.api_key:
            logger.warning("[Gemini] No API key configured")
            return False
        try:
            url = f"/models?key={self.config.api_key}"
            response = self.client.get(url)
            if response.status_code != 200:
                logger.warning("[Gemini] API returned status %d: %s", response.status_code, response.text[:200])
            return response.status_code == 200
        except Exception as e:
            logger.warning("[Gemini] Connection failed: %s", e)
            return False

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


@dataclass
class OpenRouterConfig:
    """Configuration for OpenRouter client."""

    api_key: str = ""
    model: str = "meta-llama/llama-3.1-8b-instruct:free"
    timeout: float = 60.0
    site_url: str = ""
    app_name: str = "Teamarr"


class OpenRouterClient(AIProviderClient):
    """HTTP client for OpenRouter API.

    OpenRouter is an aggregator with access to many models.
    Some models have free tiers.
    API is OpenAI-compatible.
    """

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, config: OpenRouterConfig | None = None):
        self.config = config or OpenRouterConfig()
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "python-httpx/0.27",
            }
            if self.config.site_url:
                headers["HTTP-Referer"] = self.config.site_url
            if self.config.app_name:
                headers["X-Title"] = self.config.app_name

            self._client = httpx.Client(
                base_url=self.BASE_URL,
                timeout=self.config.timeout,
                headers=headers,
            )
        return self._client

    def generate(
        self,
        prompt: str,
        *,
        json_format: bool = True,
        temperature: float = 0.1,
    ) -> dict | str | None:
        """Generate a response from OpenRouter."""
        if not self.config.api_key:
            logger.warning("[OpenRouter] No API key configured")
            return None

        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }

        if json_format:
            # Add JSON instruction to prompt for models that don't support response_format
            payload["messages"][0]["content"] = (
                prompt + "\n\nRespond with valid JSON only, no markdown."
            )

        # Retry loop for rate limits
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.client.post("/chat/completions", json=payload)

                # Handle rate limiting with retry
                if response.status_code == 429:
                    if attempt < MAX_RETRIES:
                        retry_after = response.headers.get("retry-after")
                        if retry_after:
                            delay = min(float(retry_after), MAX_DELAY)
                        else:
                            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                        logger.info("[OpenRouter] Rate limited, waiting %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                        time.sleep(delay)
                        continue
                    else:
                        logger.warning("[OpenRouter] Rate limited after %d retries", MAX_RETRIES)
                        return None

                response.raise_for_status()

                data = response.json()
                raw_response = data["choices"][0]["message"]["content"]

                # Clean up response if wrapped in markdown code blocks
                if raw_response.startswith("```"):
                    lines = raw_response.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    raw_response = "\n".join(lines)

                if json_format:
                    try:
                        return json.loads(raw_response)
                    except json.JSONDecodeError as e:
                        logger.warning("[OpenRouter] Failed to parse JSON response: %s", e)
                        return None

                return raw_response

            except httpx.TimeoutException:
                logger.warning("[OpenRouter] Request timed out")
                return None
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < MAX_RETRIES:
                    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    logger.info("[OpenRouter] Rate limited, waiting %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                    time.sleep(delay)
                    continue
                logger.warning("[OpenRouter] HTTP error: %s", e)
                return None
            except httpx.HTTPError as e:
                logger.warning("[OpenRouter] HTTP error: %s", e)
                return None
            except Exception as e:
                logger.exception("[OpenRouter] Unexpected error: %s", e)
                return None

        return None

    def is_available(self) -> bool:
        """Check if OpenRouter is reachable and API key is valid."""
        if not self.config.api_key:
            logger.warning("[OpenRouter] No API key configured")
            return False
        try:
            response = self.client.get("/models")
            if response.status_code != 200:
                logger.warning("[OpenRouter] API returned status %d: %s", response.status_code, response.text[:200])
            return response.status_code == 200
        except Exception as e:
            logger.warning("[OpenRouter] Connection failed: %s", e)
            return False

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


@dataclass
class OpenAIConfig:
    """Configuration for OpenAI client."""

    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout: float = 60.0
    organization: str = ""


class OpenAIClient(AIProviderClient):
    """HTTP client for OpenAI API."""

    BASE_URL = "https://api.openai.com/v1"

    def __init__(self, config: OpenAIConfig | None = None):
        self.config = config or OpenAIConfig()
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "python-httpx/0.27",
            }
            if self.config.organization:
                headers["OpenAI-Organization"] = self.config.organization

            self._client = httpx.Client(
                base_url=self.BASE_URL,
                timeout=self.config.timeout,
                headers=headers,
            )
        return self._client

    def generate(
        self,
        prompt: str,
        *,
        json_format: bool = True,
        temperature: float = 0.1,
    ) -> dict | str | None:
        """Generate a response from OpenAI."""
        if not self.config.api_key:
            logger.warning("[OpenAI] No API key configured")
            return None

        try:
            payload = {
                "model": self.config.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
            }

            if json_format:
                payload["response_format"] = {"type": "json_object"}

            response = self.client.post("/chat/completions", json=payload)
            response.raise_for_status()

            data = response.json()
            raw_response = data["choices"][0]["message"]["content"]

            if json_format:
                try:
                    return json.loads(raw_response)
                except json.JSONDecodeError as e:
                    logger.warning("[OpenAI] Failed to parse JSON response: %s", e)
                    return None

            return raw_response

        except httpx.TimeoutException:
            logger.warning("[OpenAI] Request timed out")
            return None
        except httpx.HTTPError as e:
            logger.warning("[OpenAI] HTTP error: %s", e)
            return None
        except Exception as e:
            logger.exception("[OpenAI] Unexpected error: %s", e)
            return None

    def is_available(self) -> bool:
        """Check if OpenAI is reachable and API key is valid."""
        if not self.config.api_key:
            return False
        try:
            response = self.client.get("/models")
            return response.status_code == 200
        except Exception:
            return False

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


@dataclass
class AnthropicConfig:
    """Configuration for Anthropic client."""

    api_key: str = ""
    model: str = "claude-3-5-sonnet-20241022"
    timeout: float = 60.0


class AnthropicClient(AIProviderClient):
    """HTTP client for Anthropic API."""

    BASE_URL = "https://api.anthropic.com/v1"

    def __init__(self, config: AnthropicConfig | None = None):
        self.config = config or AnthropicConfig()
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.BASE_URL,
                timeout=self.config.timeout,
                headers={
                    "x-api-key": self.config.api_key,
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "User-Agent": "python-httpx/0.27",
                },
            )
        return self._client

    def generate(
        self,
        prompt: str,
        *,
        json_format: bool = True,
        temperature: float = 0.1,
    ) -> dict | str | None:
        """Generate a response from Anthropic."""
        if not self.config.api_key:
            logger.warning("[Anthropic] No API key configured")
            return None

        try:
            # Add JSON instruction for Anthropic
            if json_format:
                prompt = prompt + "\n\nRespond with valid JSON only."

            payload = {
                "model": self.config.model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
            }

            response = self.client.post("/messages", json=payload)
            response.raise_for_status()

            data = response.json()
            raw_response = data["content"][0]["text"]

            # Clean up response if wrapped in markdown code blocks
            if raw_response.startswith("```"):
                lines = raw_response.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw_response = "\n".join(lines)

            if json_format:
                try:
                    return json.loads(raw_response)
                except json.JSONDecodeError as e:
                    logger.warning("[Anthropic] Failed to parse JSON response: %s", e)
                    return None

            return raw_response

        except httpx.TimeoutException:
            logger.warning("[Anthropic] Request timed out")
            return None
        except httpx.HTTPError as e:
            logger.warning("[Anthropic] HTTP error: %s", e)
            return None
        except Exception as e:
            logger.exception("[Anthropic] Unexpected error: %s", e)
            return None

    def is_available(self) -> bool:
        """Check if Anthropic is reachable and API key is valid."""
        if not self.config.api_key:
            return False
        # Anthropic doesn't have a simple health check endpoint
        # Just verify API key format
        return len(self.config.api_key) > 0

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


def get_provider_client(
    provider: str,
    settings: dict,
) -> AIProviderClient | None:
    """Factory function to get the appropriate provider client.

    Args:
        provider: Provider name (ollama, openai, anthropic, groq, gemini, openrouter)
        settings: Provider-specific settings dict

    Returns:
        Configured provider client, or None if provider unknown
    """
    # Import here to avoid circular imports
    from teamarr.services.ai.client import OllamaClient, OllamaConfig

    if provider == "ollama":
        return OllamaClient(OllamaConfig(
            base_url=settings.get("url", "http://localhost:11434"),
            model=settings.get("model", "qwen2.5:7b"),
            timeout=settings.get("timeout", 180),
        ))
    elif provider == "groq":
        return GroqClient(GroqConfig(
            api_key=settings.get("api_key", ""),
            model=settings.get("model", "llama-3.1-8b-instant"),
            timeout=settings.get("timeout", 60),
        ))
    elif provider == "gemini":
        return GeminiClient(GeminiConfig(
            api_key=settings.get("api_key", ""),
            model=settings.get("model", "gemini-2.0-flash"),
            timeout=settings.get("timeout", 60),
        ))
    elif provider == "openrouter":
        return OpenRouterClient(OpenRouterConfig(
            api_key=settings.get("api_key", ""),
            model=settings.get("model", "meta-llama/llama-3.1-8b-instruct:free"),
            timeout=settings.get("timeout", 60),
            site_url=settings.get("site_url", ""),
            app_name=settings.get("app_name", "Teamarr"),
        ))
    elif provider == "openai":
        return OpenAIClient(OpenAIConfig(
            api_key=settings.get("api_key", ""),
            model=settings.get("model", "gpt-4o-mini"),
            timeout=settings.get("timeout", 60),
            organization=settings.get("organization", ""),
        ))
    elif provider == "anthropic":
        return AnthropicClient(AnthropicConfig(
            api_key=settings.get("api_key", ""),
            model=settings.get("model", "claude-3-5-sonnet-20241022"),
            timeout=settings.get("timeout", 60),
        ))
    else:
        logger.warning("[AI] Unknown provider: %s", provider)
        return None
