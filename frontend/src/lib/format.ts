const nf = new Intl.NumberFormat("ar-EG");
const nf1 = new Intl.NumberFormat("ar-EG", { maximumFractionDigits: 1 });

export const num = (n: number) => nf.format(n);

const UNITS = ["بايت", "ك.ب", "م.ب", "ج.ب", "ت.ب"];

export function bytes(n: number | null | undefined): string {
  if (!n || n <= 0) return `${nf.format(0)} ${UNITS[0]}`;
  let i = 0;
  let v = n;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i++;
  }
  return `${i === 0 ? nf.format(v) : nf1.format(v)} ${UNITS[i]}`;
}

export function speed(bps: number): string {
  return `${bytes(bps)}/ث`;
}

/** "٣ دقائق", "ساعة", "٤٥ ثانية" — plain Arabic durations. */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return plural(s, "ثانية", "ثانيتان", "ثوانٍ", "ثانية");
  const m = Math.round(s / 60);
  if (m < 60) return plural(m, "دقيقة", "دقيقتان", "دقائق", "دقيقة");
  const h = Math.round(m / 60);
  if (h < 24) return plural(h, "ساعة", "ساعتان", "ساعات", "ساعة");
  return plural(Math.round(h / 24), "يوم", "يومان", "أيام", "يوماً");
}

function plural(n: number, one: string, two: string, few: string, many: string): string {
  if (n === 1) return one;
  if (n === 2) return two;
  if (n >= 3 && n <= 10) return `${nf.format(n)} ${few}`;
  return `${nf.format(n)} ${many}`;
}

/** Server timestamps are naive UTC ISO strings. */
export function parseUtc(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
}

const dateFmt = new Intl.DateTimeFormat("ar-EG", { dateStyle: "medium" });
const dateTimeFmt = new Intl.DateTimeFormat("ar-EG", { dateStyle: "medium", timeStyle: "short" });

export const date = (iso: string | null | undefined) => {
  const d = parseUtc(iso);
  return d ? dateFmt.format(d) : "—";
};

export const dateTime = (iso: string | null | undefined) => {
  const d = parseUtc(iso);
  return d ? dateTimeFmt.format(d) : "—";
};

export function ago(iso: string | null | undefined): string {
  const d = parseUtc(iso);
  if (!d) return "لم يُفحص بعد";
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "الآن";
  return `قبل ${duration(s)}`;
}
