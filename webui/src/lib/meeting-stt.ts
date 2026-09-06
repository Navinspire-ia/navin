/** User-facing STT errors and speaker-label helpers for the Meeting desk. */

export function meetingSttErrorDetail(err: unknown): string {
  return (err instanceof Error ? err.message : String(err ?? "")).trim();
}

export function describeMeetingSttError(
  err: unknown,
  tx: (key: string, fallback: string) => string,
): string {
  const detail = meetingSttErrorDetail(err).toLowerCase();
  if (detail === "disabled") {
    return tx(
      "meeting.errors.sttDisabled",
      "Transcription is turned off. Enable it under Settings -> Voice.",
    );
  }
  if (detail === "not_configured") {
    return tx(
      "meeting.errors.sttNotConfigured",
      "The transcription provider has no credentials yet. Open Settings -> Voice.",
    );
  }
  if (detail === "duration") {
    return tx(
      "meeting.errors.tooLong",
      "Audio segment longer than the configured limit.",
    );
  }
  if (detail === "size") {
    return tx(
      "meeting.errors.tooLarge",
      "Audio payload larger than the upload limit.",
    );
  }
  if (detail === "mime" || detail === "decode") {
    return tx(
      "meeting.errors.badFormat",
      "This audio format is not accepted by the provider.",
    );
  }
  if (detail === "empty") {
    return tx(
      "meeting.errors.empty",
      "No speech was detected in this clip. Keep talking, or check the microphone.",
    );
  }
  if (detail.includes("timed out") || detail === "timeout") {
    return tx(
      "meeting.errors.timeout",
      "Transcription timed out. The next clip will be sent again automatically.",
    );
  }
  if (detail === "missing_audio" || detail === "invalid_request") {
    return tx("meeting.errors.transcribeFailed", "Transcription failed.");
  }
  return (
    meetingSttErrorDetail(err)
    || tx("meeting.errors.transcribeFailed", "Transcription failed.")
  );
}

/** True when most lines already look like `Speaker 1: ...` or `Name: ...`. */
export function transcriptHasSpeakerLabels(text: string): boolean {
  const lines = text
    .split(/\n+/)
    // A capture timecode like [03:15] may precede the label; it is not one.
    .map((line) => line.trim().replace(/^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*/, ""))
    .filter(Boolean);
  if (lines.length < 2) return false;
  const labelled = lines.filter((line) => /^[^:]{1,40}:\s+\S/.test(line));
  return labelled.length >= 2 && labelled.length / lines.length >= 0.5;
}
