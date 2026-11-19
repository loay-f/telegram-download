import { useState } from "react";
import { Link } from "react-router-dom";
import { post } from "../lib/api";
import { NOUNS, bytes, count, duration, num, speed } from "../lib/format";
import { engineSentence } from "../lib/labels";
import { useLive } from "../lib/live";
import { Alert, Button, ProgressBar, Spinner } from "../components/ui";

export function Dashboard() {
  const { status, connected } = useLive();
  const [busy, setBusy] = useState(false);

  if (!status) {
    return (
      <div className="flex items-center gap-3 text-ink-soft py-10">
        <Spinner className="size-5" /> جارٍ تحميل الحالة…
      </div>
    );
  }

  const { engine, stats, scan } = status;
  const sentence = engineSentence(status);
  const stopped = engine.paused || engine.state === "disk_full";
  const attention = ["flood_wait", "disk_full", "offline", "waiting_telegram"].includes(engine.state);
  const limited = engine.speed_limit_kbps > 0;

  async function toggle() {
    setBusy(true);
    try {
      await post(stopped ? "/api/engine/resume" : "/api/engine/pause");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-10">
      {/* The one thing the user comes here to learn: is it working? */}
      <section aria-live="polite" className="pt-2">
        <h1
          className={`text-3xl sm:text-4xl font-semibold leading-tight ${
            attention ? "text-warn" : "text-accent-strong"
          }`}
        >
          {sentence.title}
        </h1>
        <p className="mt-3 text-lg text-ink-soft max-w-2xl">{sentence.detail}</p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Button onClick={toggle} busy={busy} variant={stopped ? "primary" : "secondary"} className="text-lg px-6 min-h-12">
            {stopped ? "استئناف التنزيل" : "إيقاف التنزيل مؤقتاً"}
          </Button>
          {limited && (
            <span className="text-sm text-ink-soft">السرعة محددة بـ{speed(engine.speed_limit_kbps * 1024)}</span>
          )}
          {!connected && <span className="text-sm text-ink-soft">انقطع التحديث المباشر، يُعاد الاتصال…</span>}
        </div>
      </section>

      <section aria-label="أرقام المكتبة" className="grid grid-cols-2 lg:grid-cols-4 gap-px overflow-hidden rounded-xl border border-line bg-line">
        <Figure label="ملف في المكتبة" value={num(stats.files)} />
        <Figure label="الحجم الكلي" value={bytes(stats.library_bytes)} />
        <Figure
          label="نسخة مكررة لم تُحفظ مرتين"
          value={num(stats.duplicates)}
          note={stats.space_saved_bytes > 0 ? `وفّرت ${bytes(stats.space_saved_bytes)}` : undefined}
          warn
        />
        <Figure
          label="في الانتظار"
          value={num(stats.status_counts.pending + stats.status_counts.downloading)}
          note={stats.remaining_bytes > 0 ? `${bytes(stats.remaining_bytes)} متبقية` : undefined}
        />
      </section>

      {stats.status_counts.available > 0 && (
        <p className="-mt-6">
          <Link to="/library?view=available" className="text-accent underline underline-offset-4">
            {count(stats.status_counts.available, NOUNS.file)} متاحة للتنزيل في المكتبة
          </Link>{" "}
          <span className="text-ink-soft">— وجدها البرنامج في المحادثات المتابَعة، وتنتظر اختيارك.</span>
        </p>
      )}

      {(stats.bandwidth_saved_bytes > 0 || stats.repeat_posts > 0) && (
        <p className="text-ink-soft -mt-6">
          {stats.repeat_posts > 0 && <>نُشرت ملفات في أكثر من مكان ({count(stats.repeat_posts, NOUNS.time)} إضافية)، ونُزّل كل منها مرة واحدة فقط. </>}
          {stats.bandwidth_saved_bytes > 0 && <>تجنّب البرنامج تنزيل {bytes(stats.bandwidth_saved_bytes)} لأنها مكررة.</>}
        </p>
      )}

      <section>
        <h2 className="text-xl font-semibold mb-4">التنزيلات الحالية</h2>
        {engine.active.length === 0 ? (
          <p className="text-ink-soft">
            لا يوجد تنزيل الآن.{" "}
            {stats.status_counts.failed > 0 && (
              <Link to="/queue?tab=failed" className="text-accent underline underline-offset-4">
                {count(stats.status_counts.failed, NOUNS.file)} لم ينجح تنزيلها
              </Link>
            )}
          </p>
        ) : (
          <ul className="divide-y divide-line rounded-xl border border-line bg-white">
            {engine.active.map((a) => {
              const left = a.speed > 0 ? (a.size - a.done) / a.speed : null;
              return (
                <li key={a.id} className="px-5 py-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                    <bdi className="font-semibold break-all">{a.name}</bdi>
                    <span className="text-sm text-ink-soft whitespace-nowrap">
                      {bytes(a.done)} من {bytes(a.size)}
                      {a.speed > 0 && <> · {speed(a.speed)}</>}
                      {left !== null && left > 5 && <> · باقٍ {duration(left)}</>}
                    </span>
                  </div>
                  <div className="mt-3">
                    <ProgressBar value={a.done} max={a.size} />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {scan.current && (
        <section className="rounded-xl border border-line bg-white px-5 py-4 flex items-center gap-4">
          <Spinner className="size-5 text-accent" />
          <p>
            يفحص الآن «<bdi>{scan.current.title}</bdi>»: {count(scan.current.messages, NOUNS.message)}،
            وجد {count(scan.current.new_documents, NOUNS.file)} جديدة.
            {scan.current.flood_wait_seconds > 0 && (
              <> تيليغرام طلب الانتظار {duration(scan.current.flood_wait_seconds)}.</>
            )}
            {scan.queued > 0 && <span className="text-ink-soft"> ({count(scan.queued, NOUNS.chat)} تنتظر الفحص)</span>}
          </p>
        </section>
      )}

      {engine.state === "disk_full" && <Alert>{engine.disk_full}</Alert>}
    </div>
  );
}

function Figure({ label, value, note, warn }: { label: string; value: string; note?: string; warn?: boolean }) {
  return (
    <div className="bg-white px-5 py-5">
      <div className={`text-3xl font-semibold ${warn ? "text-warn" : "text-ink"}`}>{value}</div>
      <div className="mt-1 text-ink-soft">{label}</div>
      {note && <div className={`mt-1 text-sm ${warn ? "text-warn" : "text-ink-soft"}`}>{note}</div>}
    </div>
  );
}
