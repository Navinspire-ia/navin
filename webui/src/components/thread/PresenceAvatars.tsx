import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import type { PresenceMember } from "@/lib/types";

function initials(name: string): string {
  const parts = name.trim().split(/[\s._@-]+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  }
  return (parts[0] || "?").slice(0, 2).toUpperCase();
}

function roleTone(role: string): string {
  if (role === "viewer") return "bg-muted text-muted-foreground";
  if (role === "admin") return "bg-primary/15 text-primary";
  return "bg-accent text-foreground";
}

interface PresenceAvatarsProps {
  members: PresenceMember[];
  className?: string;
  maxVisible?: number;
}

/**
 * One entry per person, keeping the first seen.
 *
 * Someone with the thread open in two tabs is announced once per connection,
 * and showing them twice is both wrong and a duplicate React key.
 */
function unique(members: PresenceMember[]): PresenceMember[] {
  const seen = new Set<string>();
  return members.filter((member) => {
    if (seen.has(member.member_id)) return false;
    seen.add(member.member_id);
    return true;
  });
}

/** Compact avatar stack for collaborators currently in the thread. */
export function PresenceAvatars({
  members,
  className,
  maxVisible = 5,
}: PresenceAvatarsProps) {
  const { t } = useTranslation();
  const people = unique(members);
  if (!people.length) return null;

  const visible = people.slice(0, maxVisible);
  const overflow = people.length - visible.length;

  return (
    <div
      className={cn("flex items-center", className)}
      title={t("thread.presence.tooltip", {
        defaultValue: "{{count}} online",
        count: people.length,
      })}
      aria-label={t("thread.presence.tooltip", {
        defaultValue: "{{count}} online",
        count: people.length,
      })}
    >
      <div className="flex -space-x-1.5">
        {visible.map((member) => (
          <span
            key={member.member_id}
            className={cn(
              "grid h-6 w-6 place-items-center rounded-full border border-background text-[10px] font-semibold",
              roleTone(member.role),
            )}
            title={`${member.display_name} (${member.role})`}
          >
            {initials(member.display_name)}
          </span>
        ))}
        {overflow > 0 ? (
          <span className="grid h-6 w-6 place-items-center rounded-full border border-background bg-muted text-[10px] font-semibold text-muted-foreground">
            +{overflow}
          </span>
        ) : null}
      </div>
    </div>
  );
}
