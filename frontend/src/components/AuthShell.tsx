import type { ReactNode } from "react";
import { AppMark } from "./Layout";

export function AuthShell({ subtitle, children }: { subtitle: ReactNode; children: ReactNode }) {
  return (
    <main className="min-h-screen px-4 py-10 sm:py-16">
      <div className="mx-auto max-w-md">
        <div className="flex flex-col items-center text-center">
          <AppMark className="size-24" />
          <h1 className="mt-5 text-3xl font-semibold leading-tight">مكتبة تيليغرام</h1>
          <p className="mt-2 text-lg text-ink-soft">{subtitle}</p>
        </div>
        <div className="mt-8 rounded-2xl border border-line bg-white p-6 shadow-xs">{children}</div>
      </div>
    </main>
  );
}
