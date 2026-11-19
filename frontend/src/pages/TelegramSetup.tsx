import { useEffect, useState, type FormEvent } from "react";
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
      subtitle: "اربط حسابك في تيليغرام ليتمكن البرنامج من تنزيل الكتب والملفات من القنوات التي أنت عضو فيها.",
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
          {(error || status.login_error) && <Alert>{error ?? status.login_error}</Alert>}
          <Button type="submit" busy={busy} className="w-full text-lg">
            أرسل رمز الدخول
          </Button>
          <QrOption busy={busy} onStart={() => run("/api/telegram/qr", {})} />
        </form>
      ),
    },
    code_sent: {
      subtitle: "اكتب رمز الدخول الذي أرسله تيليغرام.",
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
          <CodeHint status={status} />
          {error && <Alert>{error}</Alert>}
          <Button type="submit" busy={busy} className="w-full text-lg">
            تأكيد الرمز
          </Button>
          <ResendButton status={status} busy={busy} onResend={() => run("/api/telegram/resend-code", {})} />
          <QrOption busy={busy} onStart={() => run("/api/telegram/qr", {})} prompt="لم يصلك الرمز؟" />
          <Button type="button" variant="quiet" className="w-full" onClick={() => run("/api/telegram/cancel", {})}>
            تغيير رقم الهاتف
          </Button>
        </form>
      ),
    },
    qr_waiting: {
      subtitle: "امسح هذا الرمز بتطبيق تيليغرام على هاتفك.",
      content: (
        <div className="space-y-5">
          <ol className="list-decimal ps-5 space-y-1.5">
            <li>افتح تيليغرام على هاتفك.</li>
            <li>
              اذهب إلى <strong>الإعدادات</strong> ← <strong>الأجهزة</strong> ← <strong>ربط جهاز سطح المكتب</strong>.
            </li>
            <li>وجّه الكاميرا إلى الرمز.</li>
          </ol>
          {status.qr && (
            <div
              role="img"
              aria-label="رمز QR لتسجيل الدخول"
              className="mx-auto w-fit rounded-lg border border-line bg-white p-2 [&_svg]:block"
              dangerouslySetInnerHTML={{ __html: status.qr.svg }}
            />
          )}
          <p className="text-sm text-ink-soft text-center">يتجدد الرمز تلقائياً كل نصف دقيقة تقريباً.</p>
          {error && <Alert>{error}</Alert>}
          <Button type="button" variant="quiet" className="w-full" onClick={() => run("/api/telegram/cancel", {})}>
            الدخول برقم الهاتف بدلاً من ذلك
          </Button>
        </div>
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

const VIA_NAME: Record<string, string> = {
  app: "داخل تطبيق تيليغرام",
  sms: "برسالة SMS",
  call: "بمكالمة هاتفية",
  email: "إلى البريد الإلكتروني",
  fragment: "عبر Fragment",
};

/** Tells the user exactly where to look for the code. */
function CodeHint({ status }: { status: AccountStatus }) {
  const c = status.code;
  if (!c) return null;
  const text: Record<string, React.ReactNode> = {
    app: (
      <>
        الرمز <strong>ليس رسالة SMS</strong>. افتح تطبيق تيليغرام على هاتفك أو حاسوبك، وابحث في قائمة المحادثات عن
        محادثة باسم <strong>Telegram</strong> عليها علامة توثيق زرقاء؛ ستجد الرمز في آخر رسالة فيها.
      </>
    ),
    sms: <>أُرسل الرمز برسالة SMS إلى رقمك. قد يستغرق وصولها دقيقة أو دقيقتين.</>,
    call: <>سيتصل بك تيليغرام ويقرأ لك الرمز، أو يكون الرمز آخر أرقام الرقم المتصل.</>,
    email: (
      <>
        أُرسل الرمز إلى بريدك الإلكتروني <bdi dir="ltr">{c.email_pattern}</bdi>. تحقق أيضاً من مجلد الرسائل غير
        المرغوبة.
      </>
    ),
    fragment: (
      <>
        رقمك من Fragment، فالرمز يصل هناك:{" "}
        <a href={c.fragment_url ?? "https://fragment.com"} target="_blank" rel="noopener noreferrer" className="underline">
          افتح Fragment
        </a>
        .
      </>
    ),
    setup_email: (
      <>
        يطلب تيليغرام ربط بريد إلكتروني بحسابك قبل الدخول من برنامج جديد. افتح تطبيق تيليغرام على هاتفك:
        الإعدادات ← الخصوصية والأمان ← بريد تسجيل الدخول، أضف بريدك، ثم ابدأ من جديد هنا.
      </>
    ),
    other: <>أرسل تيليغرام الرمز بطريقة غير معتادة. افحص تطبيق تيليغرام ورسائل SMS.</>,
  };
  return <Alert kind="info">{text[c.via] ?? text.other}</Alert>;
}

function ResendButton({ status, busy, onResend }: { status: AccountStatus; busy: boolean; onResend: () => void }) {
  const c = status.code;
  const [wait, setWait] = useState(c?.resend_in ?? 0);
  useEffect(() => setWait(c?.resend_in ?? 0), [c?.resend_in, c?.via]);
  useEffect(() => {
    if (wait <= 0) return;
    const t = setTimeout(() => setWait(wait - 1), 1000);
    return () => clearTimeout(t);
  }, [wait]);
  if (!c?.next_via) return null;
  const how = VIA_NAME[c.next_via] ?? "بطريقة أخرى";
  return (
    <Button type="button" variant="secondary" className="w-full" disabled={busy || wait > 0} onClick={onResend}>
      {wait > 0 ? `يمكنك طلب الرمز ${how} بعد ${wait.toLocaleString("ar-EG")} ثانية` : `لم يصلني الرمز — أرسله ${how}`}
    </Button>
  );
}

function QrOption({ busy, onStart, prompt }: { busy: boolean; onStart: () => void; prompt?: string }) {
  return (
    <div className="border-t border-line pt-4 text-center">
      {prompt && <p className="mb-2 text-ink-soft">{prompt}</p>}
      <Button type="button" variant="secondary" className="w-full" disabled={busy} onClick={onStart}>
        ادخل بمسح رمز QR من هاتفك
      </Button>
    </div>
  );
}
