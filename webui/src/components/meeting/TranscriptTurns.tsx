// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Readable transcript view: one block per speech turn, with the speaker on
 * its own line (stable colour + initials avatar), the relative timecode, the
 * real clock time when the meeting start is known, and the speech below.
 */

import { useMemo } from "react";

import {
  timecodeToSeconds,
  type TranscriptTurn,
} from "@/components/meeting/meetingReport";
import { highlightParts } from "@/components/meeting/meetingPersistence";

/** Hues picked to stay readable on both light and dark themes. */
const SPEAKER_HUES = [211, 152, 268, 32, 340, 187, 84, 300];

function speakerColor(index: number): { text: string; bg: string } {
  const hue = SPEAKER_HUES[index % SPEAKER_HUES.length];
  return {
    text: `hsl(${hue} 72% 52%)`,
    bg: `hsl(${hue} 72% 52% / 0.14)`,
  };
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Wall-clock time of a turn ("17:52") from the meeting start + timecode. */
function clockTime(startedAt: string, time: string, locale: string): string | null {
  const startMs = Date.parse(startedAt);
  if (!Number.isFinite(startMs)) return null;
  const at = new Date(startMs + timecodeToSeconds(time) * 1000);
  return at.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

interface TranscriptTurnsProps {
  turns: TranscriptTurn[];
  /** Real meeting start (ISO) to turn [mm:ss] offsets into clock times. */
  startedAt?: string | null;
  /** Localized fallback label for turns with no identified speaker. */
  unknownSpeakerLabel: string;
  locale: string;
  query?: string;
  onSeek?: (seconds: number) => void;
}

export function TranscriptTurns({
  turns,
  startedAt,
  unknownSpeakerLabel,
  locale,
  query = "",
  onSeek,
}: TranscriptTurnsProps) {
  // Stable colour per speaker, in order of first appearance.
  const colorIndex = useMemo(() => {
    const map = new Map<string, number>();
    for (const turn of turns) {
      if (turn.speaker && !map.has(turn.speaker)) {
        map.set(turn.speaker, map.size);
      }
    }
    return map;
  }, [turns]);

  return (
    <div className="space-y-3">
      {turns.map((turn) => {
        const known = turn.speaker ? colorIndex.get(turn.speaker) : undefined;
        const color =
          known !== undefined
            ? speakerColor(known)
            : { text: "hsl(215 12% 58%)", bg: "hsl(215 12% 58% / 0.14)" };
        const wallClock =
          turn.time && startedAt ? clockTime(startedAt, turn.time, locale) : null;
        return (
          <div key={turn.id} className="flex items-start gap-2.5">
            <span
              aria-hidden
              className="mt-0.5 grid h-6 w-6 shrink-0 select-none place-items-center rounded-full text-[10px] font-bold leading-none"
              style={{ background: color.bg, color: color.text }}
            >
              {turn.speaker ? initials(turn.speaker) : "?"}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span
                  className="text-[12.5px] font-semibold tracking-[-0.01em]"
                  style={{ color: color.text }}
                >
                  {turn.speaker || unknownSpeakerLabel}
                </span>
                {turn.time ? (
                  <button
                    type="button"
                    onClick={() => onSeek?.(timecodeToSeconds(turn.time || "0:00"))}
                    disabled={!onSeek}
                    className="rounded-md bg-muted/70 px-1.5 py-px text-[10.5px] tabular-nums text-muted-foreground enabled:cursor-pointer enabled:hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                    aria-label={`Seek to ${turn.time}`}
                  >
                    {turn.time}
                  </button>
                ) : null}
                {wallClock ? (
                  <span className="text-[10.5px] tabular-nums text-muted-foreground/80">
                    {wallClock}
                  </span>
                ) : null}
              </div>
              <p className="mt-0.5 whitespace-pre-wrap text-[13px] leading-6 text-foreground/92">
                {highlightParts(turn.text, query).map((part, index) =>
                  part.match ? (
                    <mark
                      key={`${turn.id}-${index}`}
                      className="rounded-sm bg-yellow-300/70 text-inherit dark:bg-yellow-500/45"
                    >
                      {part.text}
                    </mark>
                  ) : (
                    <span key={`${turn.id}-${index}`}>{part.text}</span>
                  ),
                )}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
