import { NOUNS, count, digits, duration } from "./format";
import type { LiveStatus } from "./live";

export const STATUS_LABEL: Record<string, string> = {
  available: "متاح للتنزيل",
  pending: "في الانتظار",
  downloading: "يُنزَّل الآن",
  done: "تم التنزيل",
  failed: "فشل",
  skipped: "متخطى",
  duplicate: "نسخة مكررة",
};

/** Plain-Arabic reason for a document's last error or skip. */
export function reasonText(lastError: string | null): string | null {
  if (!lastError) return null;
  if (lastError === "filtered:ext") return "نوع الملف غير مسموح في الإعدادات";
  if (lastError === "filtered:size") return "حجم الملف أكبر من الحد المسموح";
  if (lastError === "deleted") return "حذفته من المكتبة";
  return lastError;
}

/** Headline + explanation for the engine state, in everyday Arabic. */
export function engineSentence(s: LiveStatus): { title: string; detail: string } {
  const e = s.engine;
  const waiting = s.stats.status_counts.pending;
  switch (e.state) {
    case "running":
      return {
        title: "يجري التنزيل الآن",
        detail: `${count(e.active.length, NOUNS.file)} قيد التنزيل.`,
      };
    case "paused":
      return { title: "التنزيل متوقف مؤقتاً", detail: "اضغط «استئناف التنزيل» للمتابعة من حيث توقف." };
    case "flood_wait":
      return {
        title: `تيليغرام طلب الانتظار ${duration(e.flood_wait_seconds)}`,
        detail: "هذا طبيعي عند كثرة الطلبات. سيستأنف التنزيل تلقائياً.",
      };
    case "disk_full":
      return { title: "مساحة التخزين ممتلئة", detail: e.disk_full ?? "أفرغ مساحة على القرص ثم استأنف التنزيل." };
    case "offline":
      return { title: "انقطع الاتصال بتيليغرام", detail: "سيحاول البرنامج مرة أخرى تلقائياً خلال لحظات." };
    case "scheduled":
      return {
        title: `التنزيل مجدول من ${digits(e.schedule.start)} إلى ${digits(e.schedule.end)}`,
        detail: "خارج هذه الساعات تنتظر الملفات دورها، ويمكنك تغيير الجدول من الإعدادات.",
      };
    case "waiting_telegram":
      return { title: "بانتظار الاتصال بتيليغرام", detail: "تحقق من اتصال الإنترنت." };
    default:
      return waiting > 0
        ? { title: "بانتظار دور الملفات", detail: "بعض الملفات ستُعاد محاولتها بعد قليل." }
        : {
            title: "لا شيء في قائمة التنزيل",
            detail: "اختر ما تريد تنزيله من «المكتبة» أو «البحث في تيليغرام».",
          };
  }
}
