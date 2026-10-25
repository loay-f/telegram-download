import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "danger" | "quiet";

const variants: Record<Variant, string> = {
  primary: "bg-binding text-white hover:bg-binding-dark disabled:bg-binding/50",
  secondary:
    "bg-white text-ink border border-line hover:border-binding hover:text-binding disabled:text-ink-soft/60",
  danger: "bg-white text-danger border border-danger/40 hover:bg-danger-tint",
  quiet: "text-binding hover:bg-binding-tint",
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
        className={`w-full rounded-lg border border-line bg-white px-3 min-h-12 text-lg focus:border-binding focus:outline-none focus:ring-2 focus:ring-binding/25 ${rest.className ?? ""}`}
      />
      {hint && <span className="block text-sm text-ink-soft mt-1.5">{hint}</span>}
    </label>
  );
}

export function Alert({ kind = "danger", children }: { kind?: "danger" | "info" | "gilt"; children: ReactNode }) {
  const styles = {
    danger: "bg-danger-tint text-danger border-danger/30",
    info: "bg-binding-tint text-binding-dark border-binding/25",
    gilt: "bg-gilt-tint text-[#6f4f12] border-gilt/30",
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
    <section className={`rounded-xl border border-line bg-white ${className}`}>
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
        checked ? "bg-binding" : "bg-line"
      }`}
    >
      <span
        className={`absolute size-6 rounded-full bg-white transition-all ${checked ? "start-7" : "start-1"}`}
      />
    </button>
  );
}

export function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div
      className="h-2.5 w-full overflow-hidden rounded-full bg-binding-tint"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div className="h-full rounded-full bg-binding transition-[width] duration-700" style={{ width: `${pct}%` }} />
    </div>
  );
}
