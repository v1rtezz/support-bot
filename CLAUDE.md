# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram support bot that bridges user DMs with a support group chat. Users send messages to the bot privately, the bot forwards them to a support supergroup (each user gets a dedicated forum topic), and operators reply in the topic. Written in Russian.

## Running

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Requires `.env` with: `TELEGRAM_TOKEN`, `SUPPORT_CHAT_ID` (supergroup with forum topics enabled).

## Architecture

Single-file bot (`bot.py`, ~870 lines) using `python-telegram-bot` v20.8 (async). SQLite database (`support_bot.db`) created at startup.

**Key concepts:**
- **Per-user topics**: Each user gets a dedicated forum topic in the support supergroup. Topic names show status emoji (🟢 open / 🔴 closed). Closed tickets reuse existing topics on reopen.
- **Message mapping**: `messages_mapping` table links user messages to support chat messages for reply routing. Indexed by `support_message_id`.
- **Topic-based routing**: Operator messages route to users via reply mapping OR by `message_thread_id` → ticket lookup (so messages without explicit reply also forward).
- **Entity preservation**: `_shift_entities()` shifts message entities (including `CUSTOM_EMOJI`) when prepending headers. `copy_message()` handles all media types with proper entity forwarding.

**DB tables**: `messages_mapping`, `tickets`, `blocked_users`

**Compatibility**: Uses `from __future__ import annotations` for Python 3.9 support. Uses `zoneinfo.ZoneInfo` (stdlib) for Moscow timezone.

## Bot Commands

- User-facing: `/start`, `/help`
- Operator (support chat): `/open_tickets`, `/close` (reply), `/reopen` (reply), `/ticket` (reply)
