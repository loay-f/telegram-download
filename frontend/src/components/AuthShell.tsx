import type { ReactNode } from "react";

export function AuthShell({ subtitle, children }: { subtitle: ReactNode; children: ReactNode }) {
  return (
    <main className="min-h-screen px-4 py-12 sm:py-20">
      <div className="mx-auto max-w-md">
        <h1 className="font-display text-5xl leading-tight text-binding-dark">مكتبة تيليغرام</h1>
        <p className="mt-3 text-lg text-ink-soft">{subtitle}</p>
        <div className="mt-8 rounded-xl border border-line bg-white p-6">{children}</div>
      </div>
    </main>
  );
}
