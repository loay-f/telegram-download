import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "danger" | "quiet";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-strong disabled:bg-accent/50",
  secondary: "bg-accent-tint text-accent-strong hover:bg-accent/20 disabled:opacity-55",
  danger: "bg-danger-tint text-danger hover:bg-danger/20",
  quiet: "text-accent hover:bg-accent-tint disabled:opacity-55",
};

export function Button({
  variant = "primary",
  busy,
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 min-h-11 font-semibold transition-colors disabled:cursor-not-allowed ${variants[variant]} ${className}`}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}

/** A round button that shows only an icon; `label` names it. */
export function IconButton({
  label,
  busy,
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; busy?: boolean }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      disabled={rest.disabled || busy}
      className={`grid size-11 shrink-0 place-items-center rounded-full text-ink-soft transition-colors hover:bg-canvas hover:text-ink disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    >
      {busy ? <Spinner className="size-5" /> : children}
    </button>
  );
}

export function Spinner({ className = "size-4" }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block rounded-full border-2 border-current border-e-transparent animate-spin ${className}`}
    />
  );
}

export function Field({
  label,
  hint,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="block font-semibold mb-1.5">{label}</span>
      <input
        {...rest}
        className={`w-full rounded-lg border border-line bg-white px-3 min-h-12 text-lg focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25 ${rest.className ?? ""}`}
      />
      {hint && <span className="block text-sm text-ink-soft mt-1.5">{hint}</span>}
    </label>
  );
}

export function Alert({ kind = "danger", children }: { kind?: "danger" | "info" | "warn"; children: ReactNode }) {
  const styles = {
    danger: "bg-danger-tint text-danger border-danger/30",
    info: "bg-accent-tint text-accent-strong border-accent/25",
    warn: "bg-warn-tint text-[#6f4f12] border-warn/30",
  }[kind];
  return (
    <div role={kind === "danger" ? "alert" : "status"} className={`rounded-lg border px-4 py-3 ${styles}`}>
      {children}
    </div>
  );
}

export function Panel({ title, actions, children, className = "" }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-white shadow-xs ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4">
          {title && <h2 className="text-lg font-semibold">{title}</h2>}
          {actions}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Toggle({ checked, onChange, label, disabled }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-8 w-14 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
        checked ? "bg-accent" : "bg-[#b9c0c7]"
      }`}
    >
      <span
        className={`absolute size-6 rounded-full bg-white shadow-sm transition-all ${checked ? "start-7" : "start-1"}`}
      />
    </button>
  );
}

export function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div
      className="h-2.5 w-full overflow-hidden rounded-full bg-accent-tint"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div className="h-full rounded-full bg-accent transition-[width] duration-700" style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Telegram's way of telling chats apart at a glance: a coloured disc with
 * the first letters of the name. The colour follows the id, so it is stable. */
const AVATAR_COLORS = [
  "from-[#ff8a5c] to-[#e0523c]",
  "from-[#ffc46b] to-[#e8912d]",
  "from-[#b08cf0] to-[#7b5ad6]",
  "from-[#8fd66e] to-[#45a843]",
  "from-[#6fd7dc] to-[#2ea3b5]",
  "from-[#6cb8f2] to-[#2f86d6]",
  "from-[#f58cb9] to-[#d9548c]",
];

export function Avatar({ id, name, className = "size-12 text-lg" }: { id: number; name: string; className?: string }) {
  const words = name.replace(/[^\p{L}\p{N}\s]/gu, " ").trim().split(/\s+/).filter(Boolean);
  const initials = words.slice(0, 2).map((w) => [...w][0]).join("\u200c") || "؟";
  return (
    <span
      aria-hidden
      className={`grid shrink-0 select-none place-items-center rounded-full bg-linear-to-b font-semibold text-white ${
        AVATAR_COLORS[Math.abs(id) % AVATAR_COLORS.length]
      } ${className}`}
    >
      {initials.toUpperCase()}
    </span>
  );
}
