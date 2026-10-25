import { useState, type FormEvent } from "react";
import { ApiError, post } from "../lib/api";
import type { AccountStatus } from "../lib/account";
import { Alert, Button, Field, Spinner } from "../components/ui";
import { AuthShell } from "../components/AuthShell";

/** First-run Telegram login: phone → code → (two-step password). */
export function TelegramSetup({ status, onChange }: { status: AccountStatus; onChange: (s: AccountStatus) => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(path: string, body: unknown) {
    setBusy(true);
    setError(null);
    try {
      const s = await post<AccountStatus>(path, body);
      setValue("");
      onChange(s);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "حدث خطأ غير متوقع.");
    } finally {
      setBusy(false);
    }
  }

  const steps: Record<string, { subtitle: string; content: React.ReactNode }> = {
    unconfigured: {
      subtitle: "خطوة واحدة قبل البدء.",
      content: (
        <Alert>
          لم تُضبط بيانات تطبيق تيليغرام. احصل على <bdi dir="ltr">API ID</bdi> و{" "}
          <bdi dir="ltr">API hash</bdi> من موقع <bdi dir="ltr">my.telegram.org</bdi>، واكتبهما في ملف{" "}
          <bdi dir="ltr">.env</bdi> ثم أعد تشغيل البرنامج. التفاصيل في ملف التعليمات (README).
        </Alert>
      ),
    },
    connecting: {
      subtitle: "جارٍ الاتصال بتيليغرام…",
      content: (
        <div className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" /> لحظات من فضلك.
        </div>
      ),
    },
    offline: {
      subtitle: "لا يوجد اتصال بتيليغرام الآن.",
      content: (
        <div className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" /> تحقق من اتصال الإنترنت. سيحاول البرنامج مرة أخرى تلقائياً.
        </div>
      ),
    },
    logged_out: {
      subtitle: "اربط حسابك في تيليغرام ليتمكن البرنامج من تنزيل الكتب من القنوات التي أنت عضو فيها.",
      content: (
        <form
          className="space-y-5"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            run("/api/telegram/send-code", { phone: value });
          }}
        >
          <Field
            label="رقم الهاتف"
            hint="مع رمز الدولة، مثل ‎+966512345678"
            type="tel"
            dir="ltr"
            autoComplete="tel"
            autoFocus
            required
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="text-start"
          />
          {error && <Alert>{error}</Alert>}
          <Button type="submit" busy={busy} className="w-full text-lg">
            أرسل رمز الدخول
          </Button>
        </form>
      ),
    },
    code_sent: {
      subtitle: "أرسل تيليغرام رمزاً إلى حسابك. افتح تطبيق تيليغرام على هاتفك وانسخ الرمز.",
      content: (
        <form
          className="space-y-5"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            run("/api/telegram/code", { code: value });
          }}
        >
          <Field
            label="رمز الدخول"
            hint={<>أُرسل إلى <bdi dir="ltr">{status.phone}</bdi></>}
            inputMode="numeric"
            autoComplete="one-time-code"
            dir="ltr"
            autoFocus
            required
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="text-center tracking-[0.4em] text-2xl"
          />
          {error && <Alert>{error}</Alert>}
          <Button type="submit" busy={busy} className="w-full text-lg">
            تأكيد الرمز
          </Button>
          <Button type="button" variant="quiet" className="w-full" onClick={() => run("/api/telegram/cancel", {})}>
            تغيير رقم الهاتف
          </Button>
        </form>
      ),
    },
    password_needed: {
      subtitle: "حسابك محمي بالتحقق بخطوتين. اكتب كلمة المرور التي اخترتها في تيليغرام.",
      content: (
        <form
          className="space-y-5"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            run("/api/telegram/password", { password: value });
          }}
        >
          <Field
            label="كلمة مرور التحقق بخطوتين"
            type="password"
            autoComplete="current-password"
            autoFocus
            required
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          {error && <Alert>{error}</Alert>}
          <Button type="submit" busy={busy} className="w-full text-lg">
            دخول
          </Button>
          <Button type="button" variant="quiet" className="w-full" onClick={() => run("/api/telegram/cancel", {})}>
            البدء من جديد
          </Button>
        </form>
      ),
    },
  };

  const step = steps[status.state] ?? steps.connecting;
  return <AuthShell subtitle={step.subtitle}>{step.content}</AuthShell>;
}
