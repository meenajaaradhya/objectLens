import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.gettempdir(), "objectlens-test.sqlite3"))

from fastapi.testclient import TestClient

from app import ai
from app.auth import normalize_phone
from app.config import get_settings, settings
from app.db import connection
from app.main import app
from app.telegram_bot import start as telegram_start
from app.ai import identify_image
from app.logging_utils import SecretRedactingFormatter


class PhoneValidationTests(unittest.TestCase):
    def test_normalizes_international_phone(self):
        self.assertEqual(normalize_phone("+1 415 555 2671"), "+14155552671")

    def test_rejects_phone_without_country_code(self):
        with self.assertRaises(ValueError):
            normalize_phone("4155552671")


class SettingsTests(unittest.TestCase):
    def test_reads_credentials_from_toml_settings(self):
        with patch(
            "app.config._SECRETS",
            {
                "telegram": {"bot_token": "toml-token", "bot_username": "@ObjectLensBot"},
                "ai": {
                    "provider_order": ["gemini", "groq"],
                    "gemini_api_key": "gemini-key",
                    "gemini_models": ["gemini-3.7-flash", "gemini-3.8-flash"],
                },
            },
        ), patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "",
                "TELEGRAM_BOT_USERNAME": "",
                "AI_PROVIDER_ORDER": "",
                "GEMINI_API_KEY": "",
                "GEMINI_MODELS": "",
                "GEMINI_MODEL": "",
            },
        ):
            config = get_settings()
        self.assertEqual(config.bot_token, "toml-token")
        self.assertEqual(config.bot_username, "ObjectLensBot")
        self.assertEqual(config.ai_provider_order, ("gemini", "groq"))
        self.assertEqual(config.gemini_api_key, "gemini-key")
        self.assertEqual(config.gemini_models, ("gemini-3.7-flash", "gemini-3.8-flash"))

    def test_legacy_single_gemini_model_setting_is_supported(self):
        with patch(
            "app.config._SECRETS",
            {"ai": {"gemini_model": "gemini-3.7-flash"}},
        ), patch.dict(
            os.environ,
            {
                "GEMINI_MODELS": "",
                "GEMINI_MODEL": "",
            },
        ):
            config = get_settings()
        self.assertEqual(config.gemini_models, ("gemini-3.7-flash",))


class SecretLoggingTests(unittest.TestCase):
    def test_redacts_bot_token_from_log_output(self):
        formatter = SecretRedactingFormatter("test-secret", logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            "httpx",
            logging.INFO,
            __file__,
            1,
            "Request to https://api.telegram.org/bottest-secret/getMe",
            (),
            None,
        )
        formatted = formatter.format(record)
        self.assertNotIn("test-secret", formatted)
        self.assertIn("bot[REDACTED]", formatted)


class IdentificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_identification_response(self):
        with patch("app.ai._with_failover", return_value='{"object_name":"Bicycle","introduction":"A bicycle uses pedals to turn its wheels.","aspects":["mechanical","materials"]}'):
            result = await identify_image(b"image", "image/jpeg")
        self.assertEqual(result["object_name"], "Bicycle")
        self.assertEqual(result["aspects"], ["mechanical", "materials"])

    async def test_reports_invalid_identification_shape(self):
        with patch("app.ai._with_failover", return_value='{"object_name":"Bicycle"}'):
            with self.assertRaises(RuntimeError):
                await identify_image(b"image", "image/jpeg")


class ProviderFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_tries_next_configured_provider_after_failure(self):
        with patch(
            "app.ai._available_providers",
            return_value=[
                ("gemini", "key-a", "gemini-3.7-flash"),
                ("gemini", "key-a", "gemini-3.8-flash"),
                ("groq", "key-b", "qwen/qwen3.8-27b"),
            ],
        ):
            with patch(
                "app.ai._generate",
                new=AsyncMock(side_effect=[RuntimeError("busy"), "answer"]),
            ) as generate:
                self.assertEqual(await ai.generate_text("system", "prompt"), "answer")
        self.assertEqual(generate.await_count, 2)
        self.assertEqual(generate.await_args_list[0].args[:3], ("gemini", "key-a", "gemini-3.7-flash"))
        self.assertEqual(generate.await_args_list[1].args[:3], ("gemini", "key-a", "gemini-3.8-flash"))

    async def test_user_facing_error_does_not_expose_provider_details(self):
        with patch(
            "app.ai._available_providers",
            return_value=[("gemini", "key-a", "retired-model"), ("groq", "key-b", "qwen/qwen3.8-27b")],
        ), patch(
            "app.ai._generate",
            new=AsyncMock(side_effect=[RuntimeError("404 private provider details"), RuntimeError("429 account billing details")]),
        ):
            with self.assertRaises(ai.AIUnavailableError) as raised:
                await ai.generate_text("system", "prompt")
        self.assertNotIn("retired-model", str(raised.exception))
        self.assertNotIn("private provider details", str(raised.exception))
        self.assertNotIn("billing details", str(raised.exception))
        self.assertIn("try again shortly", str(raised.exception))


class AccountLinkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        test_settings = replace(
            settings,
            database_path=os.path.join(self.temp_dir.name, "test.sqlite3"),
            bot_token="test-token",
            bot_username="ObjectLensBot",
        )
        self.settings_patchers = [
            patch(f"{module}.settings", test_settings)
            for module in ("app.db", "app.auth", "app.main")
        ]
        for patcher in self.settings_patchers:
            patcher.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for patcher in self.settings_patchers:
            patcher.stop()
        self.temp_dir.cleanup()

    def test_telegram_link_is_deferred_until_the_conversation_ends(self):
        response = self.client.post(
            "/api/auth/register",
            json={"username": "Alex", "phone": "+1 415 555 2671"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertNotIn("telegram_link", data)
        profile = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {data['session_token']}"},
        )
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json(), {"username": "Alex"})
        with connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

        headers = {"Authorization": f"Bearer {data['session_token']}"}
        conversation = self.client.post("/api/conversations", headers=headers)
        conversation_id = conversation.json()["conversation_id"]
        with patch(
            "app.main.identify_image",
            new=AsyncMock(
                return_value={
                    "object_name": "Bicycle",
                    "introduction": "A bicycle moves when its pedals turn the wheels.",
                    "aspects": ["mechanical", "materials"],
                }
            ),
        ) as identify:
            photo = self.client.post(
                f"/api/conversations/{conversation_id}/photo",
                headers=headers,
                files={"photo": ("bike.png", b"sample image", "image/png")},
            )
        self.assertEqual(photo.status_code, 200)
        identify.assert_awaited_once_with(b"sample image", "image/png")
        with patch("app.main.generate_text", new=AsyncMock(return_value="The chain transfers pedal force to the rear wheel.")):
            message = self.client.post(
                f"/api/conversations/{conversation_id}/messages",
                headers=headers,
                json={"message": "How does the chain work?"},
            )
        self.assertEqual(message.status_code, 200)
        self.assertIn("transfers pedal force", message.json()["answer"])

        with patch(
            "app.main.create_summary",
            new=AsyncMock(return_value="Bicycle: pedals turn the wheels."),
        ):
            ended = self.client.post(
                f"/api/conversations/{conversation_id}/end",
                headers=headers,
            )
        self.assertEqual(ended.status_code, 200)
        ended_data = ended.json()
        self.assertFalse(ended_data["sent"])
        self.assertIn("https://t.me/ObjectLensBot?start=", ended_data["telegram_link"])
        message = AsyncMock()
        telegram_user = SimpleNamespace(id=12345)
        update = SimpleNamespace(message=message, effective_user=telegram_user)
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(args=[ended_data["link_code"]], bot=bot)
        asyncio.run(telegram_start(update, context))
        bot.send_message.assert_awaited_once_with(
            chat_id=12345,
            text="Bicycle: pedals turn the wheels.",
        )
        message.reply_text.assert_awaited_once_with(
            "You're linked, Alex! Your ObjectLens conversation summary is above."
        )
        self.assertEqual(
            self.client.get(f"/api/auth/link-status/{ended_data['link_code']}").json(),
            {"linked": True},
        )
        self.assertEqual(
            self.client.post(
                f"/api/conversations/{conversation_id}/messages",
                headers=headers,
                json={"message": "Anything else?"},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post("/api/auth/logout", headers=headers).json(),
            {"logged_out": True},
        )
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
