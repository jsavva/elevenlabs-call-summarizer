---
name: elevenlabs-call-summarizer
description: >
  Use this skill whenever the user wants to place an AI phone call via ElevenLabs
  Conversational AI and receive a Telegram notification with a summary of the call.
  Triggers on any request involving: making a phone call with ElevenLabs, calling
  a number and getting a summary, "call and notify", "call then telegram me",
  outbound AI calls with follow-up, or any workflow combining ElevenLabs calling
  with Telegram messaging. Even if the user only says something like "call this
  number and let me know how it went", use this skill.
---

# ElevenLabs Call + Telegram Summary Skill

This skill orchestrates a three-step workflow:

1. **Initiate** an outbound AI phone call via ElevenLabs Conversational AI + Twilio
2. **Poll** the ElevenLabs API until the call completes
3. **Send** a Telegram message to the user with a transcript or summary

---

## Prerequisites

The following environment variables must be set before running:

| Variable | Description |
|---|---|
| `ELEVENLABS_API_KEY` | Your ElevenLabs API key |
| `ELEVENLABS_AGENT_ID` | The Conversational AI agent to use |
| `ELEVENLABS_PHONE_NUMBER_ID` | The Twilio phone number registered in ElevenLabs |
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather for your Telegram bot |
| `TELEGRAM_CHAT_ID` | Your Telegram chat/user ID (get it from @userinfobot) |

If any are missing, the script will report exactly which ones to add.

---

## Running the skill

Use the script at `scripts/call_and_notify.py`:

```bash
python scripts/call_and_notify.py <phone_number> [first_message] [context]
```

| Argument | Required | Description |
|---|---|---|
| `phone_number` | ✅ | E.164 format — e.g. `+14155551234`. International numbers supported. |
| `first_message` | ✗ | Custom opening line for the AI agent on this call |
| `context` | ✗ | Passed as `call_context` dynamic variable to the agent |

**Example:**
```bash
python scripts/call_and_notify.py +14155551234 \
  "Hi, I'm calling to confirm your appointment tomorrow at 2pm." \
  "appointment_confirmation"
```

The script prints live progress to stdout and exits 0 on success, 1 on failure.

---

## What happens step by step

### 1 — Call initiation
Calls `POST /v1/convai/twilio/outbound-call`. Returns a `conversation_id` which is
used for all subsequent polling.

### 2 — Polling
Calls `GET /v1/convai/conversations/{conversation_id}` every **15 seconds**.
Stops when `status` is one of: `done`, `failed`, `error`.
Times out after **30 minutes** (adjust `MAX_WAIT_SECONDS` in the script if needed).

### 3 — Summary extraction
Checks `metadata.summary` in the conversation object first.
Falls back to reconstructing the dialogue from the `transcript` array.
Transcripts over 3000 characters are truncated with a note.

### 4 — Telegram notification
Sends a Markdown-formatted message to the configured chat:
```
📞 Call Summary
To: +14155551234
Status: done
Conversation ID: abc123

Agent: Hello, I'm calling to confirm…
User: Yes, that works for me.
…
```

---

## Adjusting behaviour

| What to change | Where |
|---|---|
| Poll frequency | `POLL_INTERVAL` constant in the script |
| Max wait time | `MAX_WAIT_SECONDS` constant |
| Phone number format validation | The `re.match` pattern in `initiate_call()` |
| Telegram message format | `build_summary()` / the `message` string in `main()` |
| Add an LLM summariser | Replace the body of `build_summary()` with a call to the Anthropic API |

---

## Troubleshooting

**"Missing env vars"** — Add the listed variables to your `.env` file and re-run.

**"Invalid E.164 phone number"** — Include the country code with `+`, e.g. `+1` for US, `+44` for UK.

**Conversation stuck polling** — Check the ElevenLabs dashboard to confirm the call was
connected. Twilio errors (wrong number, carrier rejection) will surface as a `failed`
status which will stop polling.

**Telegram send failed** — Verify `TELEGRAM_BOT_TOKEN` is correct and that you've
sent at least one message to the bot first (required before a bot can message you).
