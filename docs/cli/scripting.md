# Scripting

## Triggers

In `navin-cli`, `/trigger nightly`. Then from cron:

```bash
navin trigger nightly "summarize new issues"
0 7 * * * navin trigger nightly "morning digest"
```

The bound session receives the payload while Navin is running.

## Local API

```bash
navin plugins enable api
navin serve -H 127.0.0.1 -p 8000
```

POST `/v1/chat/completions`. Set `api.api_key` if you bind beyond loopback.

## Gateway

```bash
navin gateway --background
navin gateway status
navin gateway stop
```

## `navin python`

Packaged builds should call `navin python` instead of system `python`.

```bash
navin python -m pytest
```
