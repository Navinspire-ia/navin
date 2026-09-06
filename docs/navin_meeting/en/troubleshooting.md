# Meeting troubleshooting

Symptoms you can hit on the Meeting desk (`#/meeting`), what usually causes them, and the fix.

## Capture

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Record and Import are greyed out | Transcription disabled, or provider without credentials | Click the amber status pill, or open Settings -> Voice |
| "Microphone permission denied or unavailable" | The OS or the app never got microphone access | Grant it in the system privacy settings, then reopen the desk |
| "This browser cannot record microphone audio" | Surface without `MediaRecorder` | Import a file recorded elsewhere instead |
| Recording runs but the transcript stays empty | Chunks are rejected by the provider | Check the error banner, then see the transcription rows below |
| The timer runs after the meeting ended | Recording was never stopped | Press the stop button; leaving the view also releases the microphone |

## Transcription

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "Audio segment longer than the configured limit" | A single segment exceeds the provider duration cap | Lower the cap in Settings -> Voice so the desk cuts smaller segments, or import instead of recording |
| "Audio payload larger than the upload limit" | Long file, or decoding failed so it was sent whole | Re-encode the file to a common format, then import again |
| "This audio format is not accepted by the provider" | Container the provider refuses | Convert to WAV or MP3, or pick a provider that accepts `webm/opus` |
| "Transcription is turned off" | Feature disabled | Settings -> Voice, enable transcription |
| "The transcription provider has no credentials yet" | Provider selected but not configured | Settings -> Voice, add the key or connect the provider |
| Import stops halfway with a partial transcript | One segment failed and the run stopped | The transcript keeps what succeeded; import again for the rest |
| Words are right but the speakers are not | No diarization at capture time | Run **Identify speakers**, then correct the list in the `Notes` tab |

AssemblyAI speaker labels are provider-native acoustic diarization. With other providers, **Identify speakers** is an explicit LLM text fallback and must not be treated as acoustic identity evidence.

## Meeting bot and desktop

The bot supports guest join flows for Zoom, Google Meet and Microsoft Teams. A host may still need to admit it. Account-only meetings cannot be joined as a guest. The gateway persists status and audio segments; after a gateway restart, an active browser session is reported as interrupted rather than falsely live. Start the bot again to reconnect.

Packaged Windows, macOS and Linux apps need a usable Chromium installation and OS microphone permission. Invite links are not written to bot state files. Never put account passwords or provider keys in a meeting URL.

## Actions and exports

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "Add a transcript or notes before running an action" | Meeting has neither transcript nor notes | Capture, import, or type notes; actions never invent content |
| An action seems to do nothing | Full width mode was hiding the chat | The desk leaves full width by itself; check the chat composer on the right |
| No Listen button | No TTS provider, or plan below Pro | Settings -> Voice, plus a Pro, Ultra, or Team plan |
| "Voice output needs a Pro, Ultra, or Team plan" | TTS refused by the plan | Same as above |
| Markdown export is nearly empty | Everything was captured in another meeting | Check the selected meeting in the left list |

## Calendar

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "No events found in this ICS file" | Empty export, or a file that is not ICS | Re-export from the calendar, unzip if needed |
| No events listed after an import | They all start beyond 72 hours | Normal: the list only shows the next 72 hours |
| No Join button on an event | No conference link in the ICS | Add the link to the event location or description, then re-import |
| No start notification | Notifications refused for the app | Allow notifications, then reimport so the event is announced again |
| Auto-join does nothing | Tab opening blocked because the app was not focused | Keep Navin in the foreground, or click Join yourself |
| "Could not open the link" | Neither the system browser nor a tab could be opened | Copy the URL shown in the message |

## Related docs

- [Overview](./README.md)
- [Transcription](./transcription.md)
- [Calendar](./calendar.md)
