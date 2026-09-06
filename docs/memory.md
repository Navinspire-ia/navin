# Memory in Navin

Navin keeps long-term context for your projects so agents stay useful across sessions - without turning memory into a messy dump of notes.

Everything below happens inside the **Navin desktop app** (Windows, macOS, or Linux). You open Navin, work in chat and Settings - you do not need a terminal.

## What memory is for

Good memory notices what is worth keeping, lets go of noise, and turns lived experience into something calm and useful.

Navin splits memory into layers:

- the **live conversation** in the current chat
- a **history archive** of compressed past turns
- durable files such as **SOUL**, **USER**, and **MEMORY** for stable facts and style
- optional **version history** when Dream updates those durable files

That keeps the moment light, and the long term reflective.

## How it works in the app

### Short-term consolidation

When a conversation grows large, Navin summarizes older turns into the project history archive. You keep chatting; Navin manages context so the window does not explode.

### Dream

**Dream** is the slower layer. On a schedule (and when you ask for it), Navin reads recent history plus your durable memory files, then updates those files carefully - small honest edits, not a full rewrite.

You can guide Dream from chat with actions such as:

| In chat | What it does |
|---------|--------------|
| `/dream` | Run Dream now |
| `/dream-log` | Show the latest Dream memory change |
| `/dream-restore` | Browse or restore a previous memory version |
| `/dream-prompt` | Inspect or create a Dream guidance note for this project |

These are composer actions inside Navin, not system shell commands.

## Where memory lives in a project

When you open a project in Navin, memory files sit with that project on disk, for example:

```text
your-project/
├── SOUL.md              # How the agent should sound
├── USER.md              # Stable facts about you
├── prompts/
│   └── dream.md         # Optional guidance for Dream
└── memory/
    ├── MEMORY.md        # Durable project facts and decisions
    ├── history.jsonl    # Compressed history summaries
    └── …                # Navin position markers and optional version history
```

- **SOUL.md** - voice and communication style  
- **USER.md** - who you are and what you prefer  
- **MEMORY.md** - what remains true about the work  
- **history** - what happened along the way  

Navin tracks its own read/write positions in that folder so consolidation and Dream stay in sync. You normally never edit those markers by hand.

## Guiding Dream from the UI

Most people leave Dream alone. If one project needs a different memory style:

1. In chat, run `/dream-prompt init` (creates an editable guide for this project).
2. Open `prompts/dream.md` from the project tree in Navin and edit it in plain Markdown.
3. Leave it empty or delete it to return to Navin’s defaults.

Each project has its own guide. Changing one project does not affect others.

## Settings

Dream timing and related options live under agent defaults in **Settings** (Dream interval and advanced options). Prefer the Settings panels in the app; you do not need to hand-edit JSON for everyday use.

In practice:

- set how often Dream runs
- leave the model as the main agent unless you have a dedicated Dream model later
- keep durable files readable - they are meant for you and for the agent

## Versioned memory

After Dream changes long-term files, Navin can keep a history of those edits so you can inspect what changed and restore a previous state from the Dream log / restore actions in chat.

## In daily use

- chats stay fast without infinite context
- durable facts get clearer over time instead of noisier
- you can inspect and restore memory when needed

Memory should feel like continuity - not a dump. That is what this design protects.
