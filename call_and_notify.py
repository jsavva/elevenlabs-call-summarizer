#!/usr/bin/env python3
"""
Initiate an ElevenLabs outbound call, wait for it to finish,
then send a Telegram summary of the conversation.

Required environment variables:
  ELEVENLABS_API_KEY
  ELEVENLABS_AGENT_ID
  ELEVENLABS_PHONE_NUMBER_ID
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

Usage:
  python call_and_notify.py <phone_number> [first_message] [context]
"""

import sys
import os
import json
import re
import time
import urllib.request
import urllib.error

# ── Config ──────────────────────────────────────────────────────────────────
ELEVENLABS_API_KEY    = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID   = os.environ.get("ELEVENLABS_AGENT_ID", "")
PHONE_NUMBER_ID       = os.environ.get("ELEVENLABS_PHONE_NUMBER_ID", "")
TELEGRAM_BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID      = os.environ.get("TELEGRAM_CHAT_ID", "")

ELEVENLABS_BASE       = "https://api.elevenlabs.io/v1/convai"
TELEGRAM_BASE         = "https://api.telegram.org"

# How long to wait between status polls (seconds) and max total wait time
POLL_INTERVAL         = 15
MAX_WAIT_SECONDS      = 1800   # 30 minutes — adjust to suit expected call length


# ── Helpers ──────────────────────────────────────────────────────────────────

def _el_headers() -> dict:
    return {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}


def _post(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _get(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


# ── Step 1: Initiate call ────────────────────────────────────────────────────

def initiate_call(to_number: str, first_message: str = "", context: str = "") -> dict:
    """Returns {"conversation_id": "...", "call_sid": "..."} or raises."""
    if not re.match(r'^\+\d{7,15}$', to_number):
        raise ValueError(f"Invalid E.164 phone number: {to_number}")

    payload: dict = {
        "agent_id": ELEVENLABS_AGENT_ID,
        "agent_phone_number_id": PHONE_NUMBER_ID,
        "to_number": to_number,
    }

    client_data: dict = {}
    if first_message:
        client_data["conversation_config_override"] = {
            "agent": {"first_message": first_message}
        }
    if context:
        client_data["dynamic_variables"] = {"call_context": context}
    if client_data:
        payload["conversation_initiation_client_data"] = client_data

    result = _post(f"{ELEVENLABS_BASE}/twilio/outbound-call", payload, _el_headers())

    if not result.get("success", True) or "error" in result:
        raise RuntimeError(f"ElevenLabs call initiation failed: {result}")

    return {
        "conversation_id": result.get("conversation_id", ""),
        "call_sid":        result.get("callSid", ""),
    }


# ── Step 2: Poll until conversation is done ──────────────────────────────────

# Terminal statuses from ElevenLabs ConvAI
_DONE_STATUSES = {"done", "failed", "error"}


def poll_conversation(conversation_id: str) -> dict:
    """
    Polls GET /v1/convai/conversations/{id} until status is terminal.
    Returns the final conversation object.
    """
    url = f"{ELEVENLABS_BASE}/conversations/{conversation_id}"
    deadline = time.time() + MAX_WAIT_SECONDS

    while time.time() < deadline:
        convo = _get(url, _el_headers())
        status = convo.get("status", "").lower()
        print(f"  [poll] status={status}", flush=True)

        if status in _DONE_STATUSES:
            return convo

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        f"Conversation {conversation_id} did not finish within {MAX_WAIT_SECONDS}s"
    )


# ── Step 3: Extract summary / transcript ─────────────────────────────────────

def build_summary(convo: dict) -> str:
    """
    Pulls the best available summary text from the conversation object.
    Tries metadata summary first, then reconstructs from transcript turns.
    """
    # ElevenLabs may embed a summary in metadata
    metadata = convo.get("metadata", {})
    if isinstance(metadata, dict):
        summary = metadata.get("summary") or metadata.get("call_summary")
        if summary:
            return summary.strip()

    # Fall back: stitch transcript turns into a readable dialogue
    transcript = convo.get("transcript", [])
    if not transcript:
        return "(No transcript available)"

    lines = []
    for turn in transcript:
        role    = turn.get("role", "?").capitalize()
        message = turn.get("message", "").strip()
        if message:
            lines.append(f"{role}: {message}")

    if not lines:
        return "(Empty transcript)"

    # If the transcript is long, summarise it ourselves via the ElevenLabs
    # analysis endpoint (if available), otherwise truncate politely.
    full_text = "\n".join(lines)
    if len(full_text) <= 3000:
        return full_text

    # Truncate with a note — callers can swap in an LLM summariser here
    return full_text[:2900] + "\n\n[…transcript truncated — see full log for details]"


# ── Step 4: Send Telegram message ────────────────────────────────────────────

def send_telegram(text: str) -> None:
    """Sends a message to the configured Telegram chat."""
    url     = f"{TELEGRAM_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }
    result = _post(url, payload, {"Content-Type": "application/json"})
    if not result.get("ok"):
        raise RuntimeError(f"Telegram send failed: {result}")


# ── Validation ───────────────────────────────────────────────────────────────

def validate_env() -> list[str]:
    missing = []
    for var in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID",
                "ELEVENLABS_PHONE_NUMBER_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if not os.environ.get(var):
            missing.append(var)
    return missing


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    missing = validate_env()
    if missing:
        print(json.dumps({"error": f"Missing env vars: {', '.join(missing)}"}))
        sys.exit(1)

    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: call_and_notify.py <phone> [first_message] [context]"}))
        sys.exit(1)

    to_number     = sys.argv[1]
    first_message = sys.argv[2] if len(sys.argv) > 2 else ""
    context       = sys.argv[3] if len(sys.argv) > 3 else ""

    try:
        # 1. Kick off the call
        print(f"📞 Initiating call to {to_number}…", flush=True)
        call_info = initiate_call(to_number, first_message, context)
        conv_id   = call_info["conversation_id"]
        print(f"✅ Call started — conversation_id={conv_id}", flush=True)

        # 2. Poll until done
        print(f"⏳ Waiting for conversation to finish (polling every {POLL_INTERVAL}s)…", flush=True)
        convo = poll_conversation(conv_id)
        print("✅ Conversation complete.", flush=True)

        # 3. Build summary text
        summary = build_summary(convo)
        status  = convo.get("status", "unknown")

        message = (
            f"📞 *Call Summary*\n"
            f"To: `{to_number}`\n"
            f"Status: `{status}`\n"
            f"Conversation ID: `{conv_id}`\n\n"
            f"{summary}"
        )

        # 4. Send to Telegram
        print("📨 Sending Telegram notification…", flush=True)
        send_telegram(message)
        print("✅ Telegram message sent.", flush=True)

        print(json.dumps({"success": True, "conversation_id": conv_id, "status": status}))
        sys.exit(0)

    except (ValueError, RuntimeError, TimeoutError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        print(json.dumps({"error": f"HTTP {exc.code}: {body}"}))
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(json.dumps({"error": f"Network error: {exc.reason}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
