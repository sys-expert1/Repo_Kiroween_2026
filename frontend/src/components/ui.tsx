import { AlertTriangle, Info, Loader2, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export type Tone = "ok" | "agent" | "warn" | "bad" | "info" | "muted";

export const TONE_TEXT: Record<Tone, string> = {
  ok: "text-phosphor", agent: "text-haunt-soft", warn: "text-pumpkin", bad: "text-crimson",
  info: "text-ice", muted: "text-ink-3",
};
export const TONE_BORDER: Record<Tone, string> = {
  ok: "border-phosphor/50", agent: "border-haunt/60", warn: "border-pumpkin/60",
  bad: "border-crimson/60", info: "border-ice/50", muted: "border-edge",
};
const TONE_BG: Record<Tone, string> = {
  ok: "bg-phosphor-muted/60", agent: "bg-haunt/10", warn: "bg-pumpkin/10", bad: "bg-crimson/10",
  info: "bg-ice/10", muted: "bg-raised",
};

/** Status is never colour alone: every badge carries an icon and a text label. */
export function Badge({ tone, icon: Icon, children }: { tone: Tone; icon?: LucideIcon; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium ${TONE_TEXT[tone]} ${TONE_BORDER[tone]} ${TONE_BG[tone]}`}>
      {Icon && <Icon size={13} aria-hidden="true" />}
      {children}
    </span>
  );
}

export function Panel({ title, icon: Icon, actions, children, className = "" }: {
  title?: string; icon?: LucideIcon; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-lg border border-edge bg-panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-2 border-b border-edge px-4 py-2.5">
          <h2 className="flex items-center gap-2 font-heading text-[15px] font-semibold text-ink">
            {Icon && <Icon size={16} className="text-ink-2" aria-hidden="true" />}
            {title}
          </h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function EmptyState({ icon: Icon = Info, title, children }: {
  icon?: LucideIcon; title: string; children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-edge px-6 py-10 text-center">
      <Icon size={28} className="text-ink-3" aria-hidden="true" />
      <p className="font-heading text-base text-ink">{title}</p>
      {children && <div className="max-w-md text-sm text-ink-2">{children}</div>}
    </div>
  );
}

export function Notice({ tone, title, children, role }: {
  tone: Tone; title: string; children?: ReactNode; role?: "alert" | "status";
}) {
  const Icon = tone === "bad" || tone === "warn" ? AlertTriangle : Info;
  return (
    <div role={role} className={`flex gap-3 rounded-lg border px-4 py-3 text-sm ${TONE_BORDER[tone]} ${TONE_BG[tone]}`}>
      <Icon size={18} className={`mt-0.5 shrink-0 ${TONE_TEXT[tone]}`} aria-hidden="true" />
      <div>
        <p className={`font-medium ${TONE_TEXT[tone]}`}>{title}</p>
        {children && <div className="mt-0.5 text-ink-2">{children}</div>}
      </div>
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-ink-2" role="status">
      <Loader2 size={16} className="animate-spin" aria-hidden="true" />
      {label}
    </span>
  );
}

export const button = {
  primary: "inline-flex items-center gap-2 rounded-md bg-phosphor px-3.5 py-2 text-sm font-semibold text-obsidian hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40",
  secondary: "inline-flex items-center gap-2 rounded-md border border-edge bg-raised px-3.5 py-2 text-sm font-medium text-ink hover:border-ink-3 disabled:cursor-not-allowed disabled:opacity-40",
  danger: "inline-flex items-center gap-2 rounded-md border border-crimson/60 bg-crimson/10 px-3.5 py-2 text-sm font-medium text-crimson hover:bg-crimson/20 disabled:cursor-not-allowed disabled:opacity-40",
  amber: "inline-flex items-center gap-2 rounded-md bg-pumpkin px-3.5 py-2 text-sm font-semibold text-obsidian hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40",
};
export const field = "w-full rounded-md border border-edge bg-raised px-3 py-2 text-sm text-ink placeholder:text-ink-3";
