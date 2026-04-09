import json
import os
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi


def load_env_file(path):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value


def load_env_from_search_path(start_dir):
    current_dir = os.path.abspath(start_dir)

    while True:
        env_path = os.path.join(current_dir, ".env")
        load_env_file(env_path)

        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            break

        current_dir = parent_dir


class OpenRouterClient:
    def __init__(self, api_key=None, model=None, site_url=None, app_title=None):
        module_dir = os.path.dirname(os.path.abspath(__file__))
        load_env_from_search_path(module_dir)

        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.model = model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        self.site_url = site_url or os.environ.get("OPENROUTER_HTTP_REFERER", "http://localhost")
        self.app_title = app_title or os.environ.get("OPENROUTER_APP_TITLE", "EduBot")

        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is not set")

    def _build_ssl_context(self):
        # Use certifi's CA bundle to avoid platform-specific trust store issues.
        verify_ssl = os.environ.get("OPENROUTER_SSL_VERIFY", "true").strip().lower()
        if verify_ssl in {"0", "false", "no", "off"}:
            return ssl._create_unverified_context()

        return ssl.create_default_context(cafile=certifi.where())

    def __call__(self, prompt):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }

        request = Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": self.site_url,
                "X-Title": self.app_title,
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=60, context=self._build_ssl_context()) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenRouter request failed with HTTP {error.code}: {details}") from error
        except URLError as error:
            raise RuntimeError(f"OpenRouter request failed: {error.reason}") from error

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("OpenRouter response did not include any choices")

        message = choices[0].get("message", {})
        content = message.get("content")
        if not content:
            raise RuntimeError("OpenRouter response did not include message content")

        return content.strip()