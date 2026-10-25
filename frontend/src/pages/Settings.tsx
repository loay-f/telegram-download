import { useState } from "react";
import { ApiError, post } from "../lib/api";
import type { AccountStatus } from "../lib/account";
import { useAccount } from "../lib/context";
import { Alert, Button, Panel } from "../components/ui";

export function SettingsPage() {
  const { account, setAccount } = useAccount();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function logout() {
    setBusy(true);
    setError(null);
    try {
      setAccount(await post<AccountStatus>("/api/telegram/logout"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر تسجيل الخروج.");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="page-title">الإعدادات</h1>
      <Panel title="حساب تيليغرام">
        <p>
          متصل باسم <strong>{account.me?.name}</strong>
          {account.me?.phone && (
            <>
              {" "}— <bdi dir="ltr">+{account.me.phone}</bdi>
            </>
          )}
        </p>
        <div className="mt-4">
          {!confirming ? (
            <Button variant="danger" onClick={() => setConfirming(true)}>
              تسجيل الخروج من تيليغرام
            </Button>
          ) : (
            <div className="space-y-3">
              <Alert>
                سيتوقف التنزيل ويُحذف ملف الجلسة. الكتب التي نُزّلت تبقى محفوظة. ستحتاج إلى رمز جديد من
                تيليغرام للدخول مرة أخرى.
              </Alert>
              <div className="flex gap-3">
                <Button variant="danger" busy={busy} onClick={logout}>
                  نعم، سجّل الخروج
                </Button>
                <Button variant="secondary" onClick={() => setConfirming(false)}>
                  تراجع
                </Button>
              </div>
            </div>
          )}
          {error && <div className="mt-3"><Alert>{error}</Alert></div>}
        </div>
      </Panel>
    </div>
  );
}
