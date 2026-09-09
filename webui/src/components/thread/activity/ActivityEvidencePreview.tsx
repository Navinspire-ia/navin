// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { AttachmentTile } from "@/components/AttachmentTile";
import { cn } from "@/lib/utils";
import type { ActivityEvidence } from "@/lib/activity-timeline";

interface ActivityEvidencePreviewProps {
  evidence: ActivityEvidence[];
  className?: string;
}

export function ActivityEvidencePreview({ evidence, className }: ActivityEvidencePreviewProps) {
  if (evidence.length === 0) return null;
  return (
    <div
      data-testid="activity-evidence-preview"
      className={cn(
        "flex max-w-full flex-wrap items-start gap-2 pt-0.5",
        "motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-top-1 motion-safe:duration-200",
        className,
      )}
    >
      {evidence.slice(0, 4).map((item) => (
        <AttachmentTile
          key={item.id}
          attachment={item.attachment}
          variant="compact"
          className={cn(
            item.attachment.kind === "image" ||
              item.attachment.kind === "video" ||
              item.attachment.kind === "audio"
              ? "max-w-[min(100%,20rem)]"
              : "max-w-[14rem]",
          )}
        />
      ))}
    </div>
  );
}
