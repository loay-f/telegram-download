import type { ComponentType, ReactNode, SVGProps } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { BooksIcon, ChatsIcon, DownloadIcon, FolderIcon, HomeIcon, SearchIcon, SettingsIcon } from "./icons";

const NAV: { to: string; label: string; icon: ComponentType<SVGProps<SVGSVGElement>>; end?: boolean }[] = [
  { to: "/", label: "الرئيسية", icon: HomeIcon, end: true },
  { to: "/chats", label: "المحادثات", icon: ChatsIcon },
  { to: "/library", label: "المكتبة", icon: BooksIcon },
  { to: "/search", label: "البحث في تيليغرام", icon: SearchIcon },
  { to: "/queue", label: "قائمة الانتظار والأخطاء", icon: DownloadIcon },
  { to: "/import", label: "إضافة ملفات من جهازك", icon: FolderIcon },
  { to: "/settings", label: "الإعدادات", icon: SettingsIcon },
];

export function AppMark({ className = "size-10" }: { className?: string }) {
  return (
    <span aria-hidden className={`grid shrink-0 place-items-center rounded-full bg-accent text-white ${className}`}>
      <BooksIcon className="size-[58%]" />
    </span>
  );
}

export function Layout({ footer }: { footer?: ReactNode }) {
  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-line bg-white md:w-72 md:shrink-0 md:border-b-0 md:border-e">
        <div className="flex flex-col md:sticky md:top-0 md:h-screen">
          <div className="flex items-center gap-3 px-4 pt-3 md:px-5 md:pb-4 md:pt-5">
            <AppMark />
            <span className="text-xl font-semibold leading-none">مكتبة تيليغرام</span>
          </div>
          <nav aria-label="الأقسام" className="flex gap-1 overflow-x-auto px-2 py-2 md:flex-col md:px-3 md:py-0">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  `flex items-center gap-3 whitespace-nowrap rounded-xl px-3 py-2.5 text-[1.02rem] transition-colors md:px-4 md:py-3 ${
                    isActive ? "bg-accent font-semibold text-white" : "text-ink hover:bg-canvas"
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <n.icon className={`size-6 ${isActive ? "" : "text-ink-soft"}`} />
                    {n.label}
                  </>
                )}
              </NavLink>
            ))}
          </nav>
          {footer && (
            <div className="mt-auto hidden border-t border-line px-5 py-4 text-sm text-ink-soft md:block">{footer}</div>
          )}
        </div>
      </aside>
      <main className="min-w-0 max-w-6xl flex-1 px-4 py-6 sm:px-8 md:py-8">
        <Outlet />
      </main>
    </div>
  );
}
