import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, onUnauthenticated } from "./lib/api";
import type { AccountStatus } from "./lib/account";
import { AppLogin } from "./pages/AppLogin";
import { TelegramSetup } from "./pages/TelegramSetup";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import { SettingsPage } from "./pages/Settings";
import { AccountContext } from "./lib/context";

type AuthStatus = { authenticated: boolean; password_configured: boolean };

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [account, setAccount] = useState<AccountStatus | null>(null);

  const loadAuth = useCallback(async () => {
    try {
      setAuth(await api<AuthStatus>("/api/auth/status"));
    } catch {
      setTimeout(loadAuth, 3000);
    }
  }, []);

  const loadAccount = useCallback(async () => {
    try {
      setAccount(await api<AccountStatus>("/api/telegram/status"));
    } catch {
      /* handled by onUnauthenticated or retried by the poll below */
    }
  }, []);

  useEffect(() => {
    loadAuth();
    return onUnauthenticated(() => {
      setAccount(null);
      setAuth((a) => (a ? { ...a, authenticated: false } : a));
    });
  }, [loadAuth]);

  useEffect(() => {
    if (auth?.authenticated) loadAccount();
  }, [auth?.authenticated, loadAccount]);

  // While Telegram isn't ready (connecting / offline), keep checking.
  useEffect(() => {
    if (!auth?.authenticated || account?.state === "ready") return;
    const t = setInterval(loadAccount, 3000);
    return () => clearInterval(t);
  }, [auth?.authenticated, account?.state, loadAccount]);

  if (!auth || (auth.authenticated && !account)) {
    return (
      <div className="min-h-screen grid place-items-center text-binding">
        <Spinner className="size-8" />
      </div>
    );
  }
  if (!auth.authenticated) {
    return <AppLogin passwordConfigured={auth.password_configured} onDone={loadAuth} />;
  }
  if (account!.state !== "ready") {
    return <TelegramSetup status={account!} onChange={setAccount} />;
  }

  return (
    <AccountContext.Provider value={{ account: account!, setAccount, reloadAccount: loadAccount }}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout footer={<>متصل باسم {account!.me?.name}</>} />}>
            <Route index element={<Placeholder title="الرئيسية" />} />
            <Route path="chats" element={<Placeholder title="المحادثات" />} />
            <Route path="library" element={<Placeholder title="المكتبة" />} />
            <Route path="queue" element={<Placeholder title="قائمة الانتظار والأخطاء" />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AccountContext.Provider>
  );
}

function Placeholder({ title }: { title: string }) {
  return <h1 className="page-title">{title}</h1>;
}
