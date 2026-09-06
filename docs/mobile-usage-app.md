# Mobile usage

Use Navin from a phone or tablet as a **chat companion** while your desktop Navin app is running on your computer. This is not the Dev workbench **Mobile** preview that builds Expo / React Native / Flutter apps.

| | Mobile chat (this page) | Dev Mobile preview |
|---|---|---|
| Purpose | Chat with your Navin agent on the go | Build and preview mobile apps inside Code |
| Where | Browser or home-screen shortcut to your running Navin | Workbench **Mobile** tab in the desktop app |
| Docs | This page | [Mobile Agent](./mobile.md) |

Licensing is the same as desktop: free with your own API keys, or a paid plan for managed models.

## What you need

1. **Navin desktop** installed and open on Windows, macOS, or Linux ([download](https://navin.live/download)).
2. Your computer and phone on the **same trusted Wi‑Fi** (or another network you control).
3. In Navin desktop: **Settings** so the app can accept connections from your phone on the local network (enable network access for the chat interface and keep a strong access secret - Navin guides you in Settings).

Do not expose Navin to the public internet without a deliberate security setup.

## Connect from your phone

1. Start Navin on your computer and leave it running.
2. In Settings, note the address Navin shows for local / LAN access (your computer’s local address on Wi‑Fi).
3. On the phone, open that address in Chrome (Android) or Safari (iOS).
4. Sign in or paste the access secret if Navin asks for it.
5. Optionally use **Add to Home Screen** / **Install app** so Navin opens like a normal app icon.

Chat still needs your desktop Navin online. Offline, you may reopen the shell, but the agent needs the running app on the computer.

## What works well on mobile

- **Chat** - same threads and composer as desktop; the sidebar becomes a sheet on small screens
- **Voice** - mic / STT when a transcription provider is configured in Settings
- **Account** - connect your navin.live account from Settings when you use managed plans
- **Artifacts** - read-only preview when the agent shares a document or page

Full Dev tooling (terminals, device preview, heavy workbench) stays on the desktop app.

## What this is not

- Not a separate store app in this phase - it is the Navin chat UI opened from the phone
- Not a replacement for [Mobile Agent](./mobile.md) when you develop mobile products inside Navin Code

## Related

- Desktop install: [Quick start](./quick-start.md)
- Dev mobile preview: [Mobile Agent](./mobile.md)
- Privacy-minded setup: [Secure a local AI agent](./guides/secure-local-ai-agent.md)
