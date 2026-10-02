# Hermes Bots

A collection of bots for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Each bot lives in its own directory with its instructions, installation steps, supporting code, and tests.

## Available bots

| Bot | What it does | Installation |
| --- | --- | --- |
| [Hermes Bot Creator](bots/bot-creator/) | Creates, inspects, edits, duplicates, and manages native Hermes bots through conversation, with history and rollback. | [Setup guide](bots/bot-creator/README.md#installation) |

## Repository layout

```text
bots/
  bot-creator/
    README.md
    BOT_CREATOR_SOUL.md
    hermes_bot_creator.py
    server.py
    install.py
    requirements.txt
    tests/
```

New bots can be added under `bots/<bot-name>/`. Include an English README explaining what the bot does, its prerequisites, how to install it, and any limitations. Prompts may use the language appropriate for the bot.

## Configuration stays local

This repository contains reusable bot definitions and implementation code. Credentials, private profile exports, chat history, memories, and machine-specific configuration are not included. Installation reads the user's existing Hermes setup locally.

Bot Creator manages Hermes' native profiles rather than maintaining a separate bot registry. See its [documentation](bots/bot-creator/README.md) for behavior, validation, backups, and concurrency limitations.
