import { cn } from "@/lib/utils";

/**
 * A teammate's bot face. Each `avatar` key is a distinct robot head (visor,
 * antenna, cyclops...) tinted with the teammate's color, so Mark and Salma are
 * told apart at a glance in the team view, the agent cards and Settings.
 */
export const TEAM_AVATAR_KEYS = ["visor", "round", "antenna", "square", "cyclops", "ears", "dome", "crest"] as const;

export function TeamBotAvatar({
  avatar = "round",
  color = "#6b7280",
  size = 28,
  active = false,
  title,
  className,
}: {
  avatar?: string;
  color?: string;
  size?: number;
  /** Working right now: a soft pulse ring. */
  active?: boolean;
  title?: string;
  className?: string;
}) {
  const tint = /^#[0-9a-fA-F]{6}$/.test(color) ? color : "#6b7280";
  return (
    <span
      className={cn("relative inline-flex shrink-0 items-center justify-center rounded-full", className)}
      style={{ width: size, height: size, backgroundColor: `${tint}1f` }}
      title={title}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      aria-label={title}
    >
      {active ? (
        <span
          className="absolute inset-0 rounded-full motion-safe:animate-ping"
          style={{ boxShadow: `0 0 0 1.5px ${tint}66` }}
        />
      ) : null}
      <svg viewBox="0 0 32 32" width={size * 0.78} height={size * 0.78} fill="none">
        <BotHead avatar={avatar} tint={tint} />
      </svg>
    </span>
  );
}

function BotHead({ avatar, tint }: { avatar: string; tint: string }) {
  const face = "#ffffff";
  const ink = "#0f172a";
  switch (avatar) {
    case "navin":
      return (
        <>
          <circle cx="16" cy="16" r="11" fill={tint} />
          <path d="M11 20.5V11.5l10 9v-9" stroke={face} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        </>
      );
    case "user":
      return (
        <>
          <circle cx="16" cy="12" r="5" fill={tint} />
          <path d="M6.5 26c1.4-5 5-7.5 9.5-7.5s8.1 2.5 9.5 7.5" fill={tint} />
        </>
      );
    case "visor":
      return (
        <>
          <line x1="16" y1="3.5" x2="16" y2="7" stroke={tint} strokeWidth="1.6" strokeLinecap="round" />
          <rect x="5" y="7" width="22" height="19" rx="6" fill={tint} />
          <rect x="8" y="12" width="16" height="6" rx="3" fill={ink} />
          <rect x="10" y="14" width="5" height="2" rx="1" fill="#67e8f9" />
          <rect x="17" y="14" width="5" height="2" rx="1" fill="#67e8f9" />
          <rect x="12" y="21" width="8" height="1.6" rx=".8" fill={face} opacity=".8" />
        </>
      );
    case "antenna":
      return (
        <>
          <line x1="10" y1="4" x2="12" y2="9" stroke={tint} strokeWidth="1.6" strokeLinecap="round" />
          <line x1="22" y1="4" x2="20" y2="9" stroke={tint} strokeWidth="1.6" strokeLinecap="round" />
          <circle cx="10" cy="3.8" r="1.6" fill={tint} />
          <circle cx="22" cy="3.8" r="1.6" fill={tint} />
          <rect x="6" y="9" width="20" height="17" rx="5" fill={tint} />
          <circle cx="12" cy="16" r="2.6" fill={face} />
          <circle cx="20" cy="16" r="2.6" fill={face} />
          <circle cx="12" cy="16" r="1.2" fill={ink} />
          <circle cx="20" cy="16" r="1.2" fill={ink} />
          <path d="M12.5 21.5h7" stroke={face} strokeWidth="1.6" strokeLinecap="round" />
        </>
      );
    case "square":
      return (
        <>
          <rect x="3" y="13" width="3" height="6" rx="1.2" fill={tint} />
          <rect x="26" y="13" width="3" height="6" rx="1.2" fill={tint} />
          <rect x="6" y="6" width="20" height="21" rx="2.5" fill={tint} />
          <rect x="10" y="12" width="4" height="4" rx=".6" fill={face} />
          <rect x="18" y="12" width="4" height="4" rx=".6" fill={face} />
          <path d="M10.5 21h11" stroke={ink} strokeWidth="1.8" strokeLinecap="round" strokeDasharray="1.8 1.6" />
        </>
      );
    case "cyclops":
      return (
        <>
          <line x1="16" y1="3" x2="16" y2="7" stroke={tint} strokeWidth="1.6" strokeLinecap="round" />
          <circle cx="16" cy="3" r="1.5" fill={tint} />
          <rect x="5" y="7" width="22" height="19" rx="9.5" fill={tint} />
          <circle cx="16" cy="15.5" r="5" fill={face} />
          <circle cx="16" cy="15.5" r="2.4" fill={ink} />
          <circle cx="17" cy="14.5" r=".8" fill={face} />
          <path d="M12.5 22.5h7" stroke={face} strokeWidth="1.5" strokeLinecap="round" opacity=".85" />
        </>
      );
    case "ears":
      return (
        <>
          <path d="M7 10 5 4l6 4" fill={tint} />
          <path d="M25 10l2-6-6 4" fill={tint} />
          <rect x="5" y="8" width="22" height="18" rx="7" fill={tint} />
          <path d="M10 15.5q2-2.4 4 0" stroke={face} strokeWidth="1.8" strokeLinecap="round" />
          <path d="M18 15.5q2-2.4 4 0" stroke={face} strokeWidth="1.8" strokeLinecap="round" />
          <path d="M13 20.5q3 2.2 6 0" stroke={face} strokeWidth="1.6" strokeLinecap="round" />
        </>
      );
    case "dome":
      return (
        <>
          <path d="M6 18a10 10 0 0 1 20 0v5a3 3 0 0 1-3 3H9a3 3 0 0 1-3-3z" fill={tint} />
          <path d="M9 13a8 8 0 0 1 14 0" stroke={face} strokeWidth="1.2" opacity=".5" />
          <circle cx="12" cy="19" r="2" fill={face} />
          <circle cx="20" cy="19" r="2" fill={face} />
          <rect x="13.5" y="23" width="5" height="1.4" rx=".7" fill={ink} opacity=".6" />
        </>
      );
    case "crest":
      return (
        <>
          <path d="M11 8l2-4 3 3 3-3 2 4z" fill={tint} />
          <rect x="5" y="8" width="22" height="18" rx="6" fill={tint} />
          <path d="M9.5 15h5M17.5 15h5" stroke={face} strokeWidth="2.2" strokeLinecap="round" />
          <rect x="11" y="20" width="10" height="2.4" rx="1.2" fill={face} opacity=".85" />
        </>
      );
    case "round":
    default:
      return (
        <>
          <line x1="16" y1="3.5" x2="16" y2="7" stroke={tint} strokeWidth="1.6" strokeLinecap="round" />
          <circle cx="16" cy="3.5" r="1.5" fill={tint} />
          <circle cx="16" cy="17" r="10.5" fill={tint} />
          <circle cx="12.2" cy="16" r="2.2" fill={face} />
          <circle cx="19.8" cy="16" r="2.2" fill={face} />
          <path d="M12.5 21q3.5 2.6 7 0" stroke={face} strokeWidth="1.6" strokeLinecap="round" />
        </>
      );
  }
}
