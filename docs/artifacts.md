# Artifacts / Canvas

Navin can present concrete results beside the chat: HTML previews, Markdown, Mermaid diagrams, and files. Artifacts persist locally under your Navin data directory for that chat.

## How artifacts appear

1. The agent presents an artifact as part of its work, or
2. Navin detects fenced HTML / Mermaid blocks in an assistant reply and opens them in the canvas.

The desktop UI opens a resizable **Artifact canvas** next to the thread. HTML renders in a sandboxed iframe.

## Using the canvas

- Select artifacts from the thread or canvas list when more than one exists.
- Resize the panel by dragging the separator.
- Keep working in chat; follow-ups can update the same artifact when the agent presents a new version.

Types include `html`, `markdown`, `mermaid`, and `file` (and related preview kinds).

## Related

- Image generation media (chat media, not the artifact canvas): [Image generation](./image-generation.md)
- Mobile PWA can view artifacts read-only: [Mobile usage PWA](./mobile-usage-app.md)
