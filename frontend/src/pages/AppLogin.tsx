import { useState, type FormEvent } from "react";
import { ApiError, post } from "../lib/api";
import { Alert, Button, Field } from "../components/ui";
import { AuthShell } from "../components/AuthShell";

export function AppLogin({ passwordConfigured, onDone }: { passwordConfigured: boolean; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await post("/api/auth/login", { password });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "حدث خطأ غير متوقع.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell subtitle="اكتب كلمة مرور البرنامج للدخول.">
      {!passwordConfigured ? (
        <Alert>
          لم تُضبط كلمة مرور البرنامج بعد. افتح ملف <bdi dir="ltr">.env</bdi> واكتب كلمة مرور في
          السطر <bdi dir="ltr">APP_PASSWORD=</bdi> ثم أعد تشغيل البرنامج.
        </Alert>
      ) : (
        <form onSubmit={submit} className="space-y-5">
          <Field
            label="كلمة المرور"
            type="password"
            autoComplete="current-password"
            autoFocus
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && <Alert>{error}</Alert>}
          <Button type="submit" busy={busy} className="w-full text-lg">
            دخول
          </Button>
        </form>
      )}
    </AuthShell>
  );
}
