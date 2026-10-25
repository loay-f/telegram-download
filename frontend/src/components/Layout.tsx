import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/", label: "الرئيسية", end: true },
  { to: "/chats", label: "المحادثات" },
  { to: "/library", label: "المكتبة" },
  { to: "/queue", label: "قائمة الانتظار والأخطاء" },
  { to: "/settings", label: "الإعدادات" },
];

export function Layout({ footer }: { footer?: React.ReactNode }) {
  return (
    <div className="min-h-screen md:flex">
      <aside className="md:w-64 md:shrink-0 md:border-e md:border-line bg-white border-b border-line md:border-b-0">
        <div className="md:sticky md:top-0 md:h-screen flex flex-col">
          <div className="px-5 pt-4 md:px-6 md:pt-7 md:pb-6">
            <span className="font-display text-2xl md:text-3xl text-binding-dark leading-none">مكتبة تيليغرام</span>
          </div>
          <nav aria-label="الأقسام" className="flex md:flex-col gap-1 overflow-x-auto px-3 py-2 md:py-0">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  `whitespace-nowrap rounded-lg px-4 py-3 text-[1.05rem] transition-colors ${
                    isActive ? "bg-binding text-white font-semibold" : "text-ink hover:bg-binding-tint"
                  }`
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          {footer && <div className="hidden md:block mt-auto px-6 py-5 text-sm text-ink-soft">{footer}</div>}
        </div>
      </aside>
      <main className="flex-1 min-w-0 px-4 sm:px-8 py-8 max-w-6xl">
        <Outlet />
      </main>
    </div>
  );
}
