# ObjectLens

ObjectLens is a local-first photo exploration website with a Telegram account link. Upload one photo, learn what is in it, choose an AI-suggested aspect to explore, and ask follow-up questions about that same subject. End the conversation from the top-right button to have a plain-text summary delivered to your linked Telegram account.

## Requirements

- Python 3.11 or newer
- A Telegram bot created with [@BotFather](https://t.me/BotFather)
- At least one AI provider API key for image analysis, chat, and summaries

## Setup on Windows

Open PowerShell in this folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Edit `secrets.toml` in the project root:

1. Set `bot_token` and `bot_username` in `[telegram]` from BotFather (username without `@`).
2. Add a Gemini API key in `[ai]`. Groq is an optional fallback; providers without a key are skipped.
3. Keep `secrets.toml` private. It is ignored by Git. Environment variables and values in `.env` override values from `secrets.toml` when set.

Run the site and Telegram bot in two PowerShell terminals:

```powershell
# Terminal 1
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload
```

```powershell
# Terminal 2
.\.venv\Scripts\Activate.ps1
python -m app.telegram_bot
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Account linking and conversation flow

Your browser session stays signed in between visits until you select **Log out**. Logging out during an active conversation asks for confirmation; an unended conversation is not summarized.

1. Enter a name and a phone number in international format, such as `+14155552671`, to open the website chat.
2. Upload a JPEG, PNG, or WebP photo (up to 10 MB). The AI identifies it, provides a short introduction, and suggests relevant aspects to explore.
3. Choose a suggested aspect or type a follow-up. The current conversation remains visible in a scrollable chat pane, and replies are kept focused on the identified subject.
4. Select **End conversation** to generate a summary and open the one-time ObjectLens Telegram link.
5. In Telegram, tap **Start** on the one-time link. The bot links that Telegram account and sends the summary; no contact sharing or code entry is needed.

Photos are analyzed in memory and are not saved to disk. Conversation messages and account details are stored in the local SQLite database at `objectlens.sqlite3` by default.

## AI provider failover

The server tries Gemini models in the order listed in `[ai].gemini_models` (default: Gemini 3.7 Flash, then Gemini 3.8 Flash), followed by the configured provider fallback order (default: Groq). If a model or provider is busy, unavailable, misconfigured, or returns an error, the next model/provider is attempted automatically. The Groq fallback defaults to `qwen/qwen3.8-27b`, a vision-capable model. If all configured models fail, the website displays a short, user-friendly error. Model names can be changed in `secrets.toml`.

## Project layout

```text
app/
  ai.py             AI calls, image identification, and provider failover
  auth.py           Phone verification and browser-session authentication
  config.py         Environment-based settings
  db.py             Local SQLite schema and helpers
  main.py           FastAPI routes and website serving
  telegram_bot.py   Telegram polling and one-time account-link flow
  static/           Website HTML, CSS, and JavaScript
prompts.py          ObjectLens behavior and summary prompts
```

## Local development notes

- Keep `.env` private; it contains bot and AI credentials.
- Keep `secrets.toml` private; it contains bot and AI credentials and is ignored by Git.
- Telegram bot polling and the website must both be running for the full flow.
- The local site works over `http://127.0.0.1`; no public webhook or hosting is required.
- Clear local user and conversation data by stopping the app and removing `objectlens.sqlite3`.
