# Meeting transcription

How the Meeting desk (`#/meeting`) turns audio into text with the speech-to-text provider you configured in Settings -> Voice. Nothing is sent to a Navin transcription service of its own: the desk uses the same STT route as the rest of the product.

## Provider status

The toolbar pill and the footer under the transcript show the three values that matter: provider, model, and the maximum segment length the desk will send.

| Pill | Meaning | What to do |
| --- | --- | --- |
| Green with a provider name | Transcription enabled and credentials present | Record or import |
| Amber, clickable | Disabled, or provider without credentials | Click it to open Settings -> Voice |

Record and Import are disabled while the pill is amber. That is deliberate: a meeting that fails silently after twenty minutes is worse than a button that refuses to start.

## Live recording

Recording streams the microphone in chunks so the transcript grows during the call instead of arriving at the end.

- Chunk length is 25 seconds, reduced automatically when your provider caps duration lower than that.
- Each chunk is transcribed on its own and appended to the transcript.
- The timer in the toolbar shows elapsed time. Stopping releases the microphone immediately.
- The browser container is chosen from what your platform supports, in order: `audio/webm;codecs=opus`, `audio/webm`, `audio/mp4`, `audio/ogg;codecs=opus`.

## Importing a file

An hour-long recording exceeds both the duration cap and the upload cap of most providers. The desk therefore prepares the file locally before any upload:

1. Decode the file in the browser.
2. Downmix to mono and resample to 16 kHz, which is what speech models expect and which cuts the payload size.
3. Cut it into segments below the configured duration cap.
4. Send segments one by one, showing `Transcribing 4/24` while it works.
5. Append each returned segment to the transcript in order.

If decoding fails, for instance on an exotic container, the file is sent as is and the provider decides.

## Formats and conversion

Some providers reject the browser `webm/opus` container. For those, the audio is converted to WAV in the browser before upload. Xiaomi MiMo is the current example. You do not have to configure anything: the conversion follows the active provider.

## Accuracy

| Lever | Effect |
| --- | --- |
| A stronger STT model in Settings -> Voice | Best single improvement, especially on accents and jargon |
| A close microphone, one speaker per device | Removes most of the errors diarization cannot fix |
| The **High-accuracy pass** action | Rereads the transcript, repairs obvious ASR errors and punctuation, keeps the meaning |
| The **Identify speakers** action | Labels turns as Speaker 1, Speaker 2, or explicit names when they are said out loud |

## Speakers

Speech-to-text returns words, not who said them. **Identify speakers**, in the `Transcript` and `Notes` tabs, runs a labelling pass over the whole transcript and rewrites it as one turn per line:

```text
Speaker 1: On se cale sur vendredi pour la livraison.
Aymen Ghadghadi: D'accord, je prepare la recette d'ici jeudi.
```

How it behaves:

- A long transcript is cut into chunks and labelled chunk by chunk. Each chunk is told which labels the previous ones used, so the same person keeps the same label from the first minute to the last.
- A real name is adopted only when the transcript states it: someone introduces themselves, signs off, or is addressed by name. Otherwise the label stays `Speaker 1`, `Speaker 2`. The pass never guesses a name from the topic or the role.
- The spoken words are preserved. The pass adds labels and line breaks, nothing else.
- Very long transcripts are labelled up to a chunk limit; anything beyond it is left as captured and the desk says so.

Rename a speaker in the `Notes` tab and every turn of that person is rewritten, so the reading view, the report, and all exports agree. The transcript stays editable: switch to **Edit raw text** whenever you want to fix a label by hand.

## Reading a summary aloud

The **Listen** button (when offered on the report) reads the summary, or the notes, or the transcript, whichever exists first. It opens a speak-only voice session, so it never touches the microphone. It needs a Pro, Ultra, or Team plan plus a configured TTS provider; without them the button is not displayed at all.

## Related docs

- [Overview](./README.md)
- [Quickstart](./quickstart.md)
- [Troubleshooting](./troubleshooting.md)
