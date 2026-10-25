import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, onUnauthenticated } from "./lib/api";
import type { AccountStatus } from "./lib/account";
import { AccountContext } from "./lib/context";
import { LiveContext, useLiveConnection } from "./lib/live";
import { AppLogin } from "./pages/AppLogin";
import { TelegramSetup } from "./pages/TelegramSetup";
import { Dashboard } from "./pages/Dashboard";
import { ChatsPage } from "./pages/Chats";
import { LibraryPage } from "./pages/Library";
import { QueuePage } from "./pages/Queue";
import { SettingsPage } from "./pages/Settings";
import { SearchPage } from "./pages/Search";
import { ReaderPage } from "./pages/Reader";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";

type AuthStatus = { authenticated: boolean; password_configured: boolean };

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [account, setAccount] = useState<AccountStatus | null>(null);
  const ready = auth?.authenticated === true && account?.state === "ready";
  const live = useLiveConnection(ready);

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

  // The live stream notices when the session is revoked elsewhere.
  const liveAccountState = live.status?.account.state;
  useEffect(() => {
    if (liveAccountState && liveAccountState !== "ready") loadAccount();
  }, [liveAccountState, loadAccount]);

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
      <LiveContext.Provider value={live}>
        <BrowserRouter>
          <Routes>
            <Route path="read/:id" element={<ReaderPage />} />
            <Route element={<Layout footer={<>متصل باسم <bdi>{account!.me?.name}</bdi></>} />}>
              <Route index element={<Dashboard />} />
              <Route path="chats" element={<ChatsPage />} />
              <Route path="library" element={<LibraryPage />} />
              <Route path="search" element={<SearchPage />} />
              <Route path="queue" element={<QueuePage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </LiveContext.Provider>
    </AccountContext.Provider>
  );
}
