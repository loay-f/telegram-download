import { Fragment, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api, post } from "../lib/api";
import { bytes, clock, day, stem, time } from "../lib/format";
import { CHAT_TYPE_LABEL, STATUS_LABEL } from "../lib/labels";
import { useLive } from "../lib/live";
import { Alert, Button, Spinner } from "../components/ui";

interface Entity {
  type: string;
  /** In UTF-16 code units, which is how JavaScript indexes strings too. */
  offset: number;
  length: number;
  url: string | null;
}

/** Where a message's file stands in the library. */
interface FileState {
  ext: string | null;
  doc_id: number | null;
  status: string | null;
  has_file: boolean;
}

interface Media {
  kind: string;
  name: string | null;
  size: number | null;
  duration: number | null;
  width: number | null;
  height: number | null;
  /** A picture can be loaded from the message's `preview` address. */
  preview: boolean;
  file: FileState | null;
}

interface Message {
  id: number;
  date: string | null;
  text: string;
  entities: Entity[];
  out: boolean;
  sender: string | null;
  reply_to: number | null;
  /** null = not forwarded; "" = forwarded from a hidden origin. */
  forwarded_from: string | null;
  edited: boolean;
  service: string | null;
  media: Media | null;
}

interface ChatInfo {
  id: number;
  title: string | null;
  username: string | null;
  type: string;
}

interface Page {
  chat: ChatInfo;
  /** Oldest first. */
  messages: Message[];
  has_more: boolean;
}

const PAGE = 50;
const CHECK_EVERY_MS = 15_000;
/** Closer than this to the end counts as reading the newest messages. */
const NEAR_END_PX = 120;

const MEDIA_LABEL: Record<string, string> = {
  photo: "صورة",
  video: "فيديو",
  round: "رسالة فيديو",
  gif: "صورة متحركة",
  sticker: "ملصق",
  voice: "رسالة صوتية",
  audio: "ملف صوتي",
  file: "ملف",
  poll: "استطلاع",
  location: "موقع",
  contact: "جهة اتصال",
  other: "محتوى لا يُعرض هنا",
};

const SERVICE_LABEL: Record<string, string> = {
  join: "انضمام عضو",
  leave: "مغادرة عضو",
  pin: "تثبيت رسالة",
  title: "تغيير اسم المحادثة",
  photo: "تغيير صورة المحادثة",
  create: "إنشاء المحادثة",
  call: "مكالمة",
  other: "تحديث في المحادثة",
};

export function ChatViewPage() {
  const { id } = useParams();
  // Keyed: opening another chat starts from a clean state.
  return <ChatView key={id} chatId={Number(id)} />;
}

function ChatView({ chatId }: { chatId: number }) {
  const [chat, setChat] = useState<ChatInfo | null>(null);
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [hasOlder, setHasOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [unseen, setUnseen] = useState(0); // new messages below while reading older ones
  const [flash, setFlash] = useState<number | null>(null);

  const scroller = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const requests = useRef<AbortController | null>(null);
  const current = useRef<Message[] | null>(null);
  current.current = messages;
  const busyOlder = useRef(false);
  const busyNewer = useRef(false);
  // Where the view should be once the next list is laid out: at the end, or
  // this many pixels from it (so adding older messages on top doesn't move
  // what is being read).
  const settle = useRef<"end" | number | null>(null);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el || settle.current === null) return;
    el.scrollTop = settle.current === "end" ? el.scrollHeight : el.scrollHeight - settle.current;
    settle.current = null;
  }, [messages]);

  const url = useCallback(
    (params: Record<string, number>) =>
      `/api/chats/${chatId}/messages?${new URLSearchParams({ limit: String(PAGE), ...mapValues(params) })}`,
    [chatId],
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await api<Page>(url({}), { signal: requests.current?.signal });
      settle.current = "end";
      setChat(page.chat);
      setMessages(page.messages);
      setHasOlder(page.has_more);
    } catch (e) {
      if (aborted(e)) return;
      setError(e instanceof ApiError ? e.message : "تعذّر تحميل الرسائل.");
    }
  }, [url]);

  useEffect(() => {
    const controller = new AbortController();
    requests.current = controller;
    load();
    return () => controller.abort();
  }, [load]);

  const loadOlder = useCallback(async () => {
    const list = current.current;
    if (busyOlder.current || !list?.length) return;
    busyOlder.current = true;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await api<Page>(url({ before: list[0].id }), { signal: requests.current?.signal });
      const el = scroller.current;
      settle.current = el ? el.scrollHeight - el.scrollTop : null;
      setMessages((now) => (now ? merge(page.messages, now) : now));
      setHasOlder(page.has_more);
    } catch (e) {
      if (aborted(e)) return;
      setOlderError(e instanceof ApiError ? e.message : "تعذّر تحميل الرسائل الأقدم.");
    } finally {
      busyOlder.current = false;
      setLoadingOlder(false);
    }
  }, [url]);

  /** Fetches what arrived after the newest message shown. */
  const loadNewer = useCallback(
    async (quiet: boolean) => {
      const list = current.current;
      if (busyNewer.current || list === null) return;
      busyNewer.current = true;
      try {
        const after = list.length ? list[list.length - 1].id : 0;
        const page = await api<Page>(url({ after }), { signal: requests.current?.signal });
        if (!quiet) setError(null);
        if (page.messages.length === 0) return;
        const el = scroller.current;
        const atEnd = !el || el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_END_PX;
        if (page.has_more) {
          // More arrived than one page holds: start over from the newest.
          settle.current = "end";
          setMessages(page.messages);
          setHasOlder(true);
          setUnseen(0);
          return;
        }
        if (atEnd) settle.current = "end";
        else setUnseen((n) => n + page.messages.length);
        setMessages((now) => (now ? merge(now, page.messages) : now));
      } catch (e) {
        // The periodic check fails silently; the next one tries again.
        if (aborted(e) || quiet) return;
        setError(e instanceof ApiError ? e.message : "تعذّر تحديث الرسائل.");
      } finally {
        busyNewer.current = false;
      }
    },
    [url],
  );

  // New messages: checked while the page is visible.
  useEffect(() => {
    const check = () => {
      if (document.visibilityState === "visible") loadNewer(true);
    };
    const timer = window.setInterval(check, CHECK_EVERY_MS);
    document.addEventListener("visibilitychange", check);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", check);
    };
  }, [loadNewer]);

  // Older messages load when the top of the list comes near. Observing again
  // after each page keeps loading while the top is still in view.
  const firstId = messages?.[0]?.id;
  useEffect(() => {
    const top = sentinel.current;
    const root = scroller.current;
    if (!top || !root || !hasOlder || olderError) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadOlder();
      },
      { root, rootMargin: "400px 0px 0px 0px" },
    );
    observer.observe(top);
    return () => observer.disconnect();
  }, [firstId, hasOlder, olderError, loadOlder]);

  async function refresh() {
    setRefreshing(true);
    if (current.current === null) await load();
    else await loadNewer(false);
    setRefreshing(false);
  }

  function toEnd() {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    setUnseen(0);
  }

  function onScroll() {
    const el = scroller.current;
    if (unseen > 0 && el && el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_END_PX) setUnseen(0);
  }

  const jumpTo = useCallback((id: number) => {
    const target = document.getElementById(`message-${id}`);
    if (!target) return;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlash(id);
    window.setTimeout(() => setFlash((now) => (now === id ? null : now)), 1600);
  }, []);

  const setFile = useCallback((id: number, file: FileState) => {
    setMessages(
      (now) => now?.map((m) => (m.id === id && m.media ? { ...m, media: { ...m.media, file } } : m)) ?? now,
    );
  }, []);

  const byId = useMemo(() => new Map(messages?.map((m) => [m.id, m])), [messages]);
  const typeLabel = chat ? CHAT_TYPE_LABEL[chat.type] : "";

  return (
    <div className="flex h-[calc(100dvh-11.5rem)] min-h-96 flex-col gap-4 md:h-[calc(100dvh-4rem)]">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <Link to="/chats" className="text-binding underline underline-offset-4">
            → كل المحادثات
          </Link>
          <h1 className="page-title mt-2 break-words">
            <bdi>{chat?.title || "المحادثة"}</bdi>
          </h1>
          <p className="mt-1 text-sm text-ink-soft">{typeLabel ? `${typeLabel} · للقراءة فقط` : "للقراءة فقط"}</p>
        </div>
        <Button variant="secondary" onClick={refresh} busy={refreshing} disabled={messages === null && !error}>
          تحديث
        </Button>
      </header>

      {error && (
        <Alert>
          <span className="flex flex-wrap items-center justify-between gap-3">
            {error}
            {messages === null && (
              <Button variant="secondary" onClick={load}>
                أعد المحاولة
              </Button>
            )}
          </span>
        </Alert>
      )}

      <div className="relative min-h-0 flex-1">
        <div
          ref={scroller}
          onScroll={onScroll}
          tabIndex={0}
          role="region"
          aria-label="رسائل المحادثة"
          className="h-full overflow-y-auto rounded-xl border border-line bg-white [overflow-anchor:none]"
        >
          {messages === null ? (
            !error && (
              <div className="flex items-center gap-3 p-5 text-ink-soft">
                <Spinner className="size-5" /> جارٍ تحميل الرسائل من تيليغرام…
              </div>
            )
          ) : messages.length === 0 ? (
            <p className="p-5 text-ink-soft">لا توجد رسائل في هذه المحادثة.</p>
          ) : (
            <div className="space-y-2 px-3 py-4 sm:px-5">
              <div ref={sentinel} className="flex min-h-11 flex-wrap items-center justify-center gap-3 text-sm text-ink-soft">
                {!hasOlder ? (
                  "بداية المحادثة"
                ) : loadingOlder ? (
                  <>
                    <Spinner /> جارٍ تحميل رسائل أقدم…
                  </>
                ) : (
                  <>
                    {olderError && <span className="text-danger">{olderError}</span>}
                    <Button variant="quiet" onClick={loadOlder}>
                      {olderError ? "أعد المحاولة" : "حمّل رسائل أقدم"}
                    </Button>
                  </>
                )}
              </div>
              {messages.map((m, i) => {
                const prev = messages[i - 1];
                const newDay = !prev || dayKey(prev.date) !== dayKey(m.date);
                const reply = m.reply_to !== null ? byId.get(m.reply_to) : undefined;
                return (
                  <Fragment key={m.id}>
                    {newDay && m.date && (
                      <p className="mx-auto w-fit rounded-full bg-binding-tint px-3 py-1 text-sm text-binding-dark">
                        {day(m.date)}
                      </p>
                    )}
                    <MessageItem
                      chatId={chatId}
                      message={m}
                      showSender={
                        !!m.sender && !m.out && (newDay || !!prev.service || prev.out || prev.sender !== m.sender)
                      }
                      reply={reply}
                      flash={flash === m.id}
                      onJump={jumpTo}
                      onFile={setFile}
                    />
                  </Fragment>
                );
              })}
            </div>
          )}
        </div>
        {unseen > 0 && (
          <button
            type="button"
            onClick={toEnd}
            className="absolute bottom-4 start-1/2 translate-x-1/2 rounded-full bg-binding px-5 min-h-11 font-semibold text-white shadow-lg hover:bg-binding-dark"
          >
            رسائل جديدة ↓
          </button>
        )}
      </div>
    </div>
  );
}

const MessageItem = memo(function MessageItem({ chatId, message: m, showSender, reply, flash, onJump, onFile }: {
  chatId: number;
  message: Message;
  showSender: boolean;
  /** The message this one answers, when it is among those loaded. */
  reply: Message | undefined;
  flash: boolean;
  onJump: (id: number) => void;
  onFile: (id: number, file: FileState) => void;
}) {
  if (m.service) {
    return (
      <p id={`message-${m.id}`} className="py-1 text-center text-sm text-ink-soft">
        {SERVICE_LABEL[m.service] ?? SERVICE_LABEL.other}
        {m.sender && (
          <>
            {" · "}
            <bdi>{m.sender}</bdi>
          </>
        )}
        {m.date && <> · {time(m.date)}</>}
      </p>
    );
  }
  return (
    <div id={`message-${m.id}`} className={`flex ${m.out ? "justify-end" : "justify-start"}`}>
      <div
        className={`min-w-0 max-w-[min(100%,34rem)] rounded-2xl px-4 py-2.5 transition-shadow ${
          m.out ? "bg-binding-tint" : "bg-paper"
        } ${flash ? "ring-3 ring-gilt" : ""}`}
      >
        {showSender && <bdi className="mb-0.5 block text-sm font-semibold text-binding">{m.sender}</bdi>}
        {m.forwarded_from !== null && (
          <p className="mb-0.5 text-sm text-ink-soft">
            رسالة محوَّلة
            {m.forwarded_from && (
              <>
                {" من "}
                <bdi className="font-semibold">{m.forwarded_from}</bdi>
              </>
            )}
          </p>
        )}
        {m.reply_to !== null &&
          (reply ? (
            <button
              type="button"
              onClick={() => onJump(reply.id)}
              className="mb-1.5 block w-full rounded-e-md border-s-4 border-binding/50 bg-white/60 px-2 py-1 text-start text-sm text-ink-soft hover:bg-white"
            >
              <span className="block text-xs">ردّاً على{reply.sender && <bdi> {reply.sender}</bdi>}</span>
              <span dir="auto" className="line-clamp-1">
                {reply.text ||
                  (reply.service ? SERVICE_LABEL[reply.service] : MEDIA_LABEL[reply.media?.kind ?? ""]) ||
                  MEDIA_LABEL.other}
              </span>
            </button>
          ) : (
            <p className="mb-1.5 border-s-4 border-binding/50 ps-2 text-sm text-ink-soft">ردّ على رسالة أقدم</p>
          ))}
        {m.media && <MediaView chatId={chatId} message={m} media={m.media} onFile={onFile} />}
        {m.text && (
          <p dir="auto" className="whitespace-pre-wrap [overflow-wrap:anywhere]">
            <RichText text={m.text} entities={m.entities} />
          </p>
        )}
        <p className="mt-1 text-end text-xs text-ink-soft">
          {m.edited && "معدّلة · "}
          {time(m.date)}
        </p>
      </div>
    </div>
  );
});

function MediaView({ chatId, message: m, media, onFile }: {
  chatId: number;
  message: Message;
  media: Media;
  onFile: (id: number, file: FileState) => void;
}) {
  const [broken, setBroken] = useState(false);
  const label = MEDIA_LABEL[media.kind] ?? MEDIA_LABEL.other;

  if (media.preview && !broken) {
    const src = `/api/chats/${chatId}/messages/${m.id}/preview`;
    // The box has the picture's size and shape before it loads, so nothing
    // moves when it arrives.
    const ratio = Math.min(3, Math.max(0.45, media.width && media.height ? media.width / media.height : 4 / 3));
    const width = media.kind === "sticker" ? 8 : Math.min(20, 24 * ratio);
    const picture = (
      <img
        src={src}
        alt={label}
        loading="lazy"
        decoding="async"
        onError={() => setBroken(true)}
        className="size-full object-cover"
      />
    );
    return (
      <div
        className="relative mb-1.5 overflow-hidden rounded-lg bg-line/60"
        style={{ width: `${width}rem`, maxWidth: "100%", aspectRatio: String(ratio) }}
      >
        {media.kind === "photo" ? (
          <a
            href={`${src}?full=true`}
            target="_blank"
            rel="noreferrer"
            title="افتح الصورة بحجمها الكامل"
            className="block size-full"
          >
            {picture}
          </a>
        ) : (
          picture
        )}
        {media.kind !== "photo" && media.kind !== "sticker" && (
          <span className="absolute bottom-2 start-2 rounded bg-ink/75 px-2 py-0.5 text-xs text-white">
            {label}
            {media.duration !== null && ` · ${clock(media.duration)}`}
          </span>
        )}
      </div>
    );
  }

  if (media.kind === "file") {
    const name = media.name || "ملف بلا اسم";
    const ext = media.file?.ext ?? null;
    return (
      <div className="mb-1.5 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-white px-3 py-2">
        <span className="w-14 shrink-0 rounded-md bg-binding-tint py-1 text-center text-sm font-semibold text-binding-dark" dir="ltr">
          {(ext ?? "؟").replace(".", "").toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <bdi className="block font-semibold [overflow-wrap:anywhere]" title={name}>
            {stem(name, ext)}
          </bdi>
          <span className="text-sm text-ink-soft">{bytes(media.size)}</span>
        </div>
        {media.file && (
          <FileAction chatId={chatId} msgId={m.id} file={media.file} onChange={(f) => onFile(m.id, f)} />
        )}
      </div>
    );
  }

  return (
    <p className="mb-1 flex flex-wrap items-center gap-x-2 text-ink-soft">
      <span className="rounded-md border border-line bg-white px-2 py-0.5 text-sm">{label}</span>
      {media.name && <bdi className="[overflow-wrap:anywhere]">{media.name}</bdi>}
      {media.duration !== null && <span>{clock(media.duration)}</span>}
      {media.size !== null && media.kind !== "sticker" && <span className="text-sm">{bytes(media.size)}</span>}
    </p>
  );
}

/** Adds the message's file to the library, or says where it stands there. */
function FileAction({ chatId, msgId, file, onChange }: {
  chatId: number;
  msgId: number;
  file: FileState;
  onChange: (file: FileState) => void;
}) {
  const { revision } = useLive();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const waiting = file.status === "pending" || file.status === "downloading";

  // Follow a queued download until it lands in the library.
  useEffect(() => {
    if (!waiting || file.doc_id === null) return;
    let stale = false;
    api<{ status: string; has_file: boolean }>(`/api/documents/${file.doc_id}`)
      .then((d) => {
        if (!stale && (d.status !== file.status || d.has_file !== file.has_file)) {
          onChange({ ...file, status: d.status, has_file: d.has_file });
        }
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [revision, waiting, file.doc_id]);

  if (file.has_file) {
    return (
      <Link
        to={`/read/${file.doc_id}`}
        className="inline-flex min-h-11 items-center rounded-lg px-4 font-semibold text-binding hover:bg-binding-tint"
      >
        في مكتبتك
      </Link>
    );
  }
  if (waiting) {
    return <span className="inline-flex min-h-11 items-center text-binding">{STATUS_LABEL[file.status!]}</span>;
  }
  return (
    <Button
      busy={busy}
      title={failed ? "تعذّرت إضافة الملف. حاول مرة أخرى." : undefined}
      onClick={async () => {
        setBusy(true);
        setFailed(false);
        try {
          onChange(await post<FileState>(`/api/chats/${chatId}/messages/${msgId}/download`));
        } catch {
          setFailed(true);
        } finally {
          setBusy(false);
        }
      }}
    >
      {failed ? "أعد المحاولة" : "نزّل"}
    </Button>
  );
}

const ENTITY_STYLE: Record<string, string> = {
  bold: "font-semibold",
  italic: "italic",
  underline: "underline",
  strike: "line-through",
  code: "rounded bg-white/70 px-1 font-mono text-[0.92em]",
  pre: "rounded bg-white/70 px-1 font-mono text-[0.92em]",
  quote: "text-ink-soft",
};

/** Message text with Telegram's formatting. Formats may overlap, so the text
 * is cut at every boundary and each piece gets the formats covering it. */
function RichText({ text, entities }: { text: string; entities: Entity[] }) {
  if (entities.length === 0) return <>{text}</>;
  const clamp = (n: number) => Math.min(text.length, Math.max(0, n));
  const cuts = new Set([0, text.length]);
  for (const e of entities) {
    cuts.add(clamp(e.offset));
    cuts.add(clamp(e.offset + e.length));
  }
  const points = [...cuts].sort((a, b) => a - b);
  return (
    <>
      {points.slice(0, -1).map((start, i) => {
        const end = points[i + 1];
        const piece = text.slice(start, end);
        const active = entities.filter((e) => e.offset <= start && e.offset + e.length >= end);
        const style = active.map((e) => ENTITY_STYLE[e.type]).filter(Boolean).join(" ");
        const link = active.find((e) => e.type === "link");
        const href = link ? safeUrl(link.url ?? text.slice(link.offset, link.offset + link.length)) : null;
        const body = href ? (
          <a href={href} target="_blank" rel="noreferrer noopener" className="text-binding underline underline-offset-2">
            {piece}
          </a>
        ) : (
          piece
        );
        return style ? (
          <span key={start} className={style}>
            {body}
          </span>
        ) : (
          <Fragment key={start}>{body}</Fragment>
        );
      })}
    </>
  );
}

/** Only web addresses become links; "example.com" gets https://. */
function safeUrl(raw: string): string | null {
  const s = raw.trim();
  if (!/^https?:\/\//i.test(s) && /^[a-z][a-z0-9+.-]*:\/\//i.test(s)) return null;
  try {
    const u = new URL(/^https?:\/\//i.test(s) ? s : `https://${s}`);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

/** Both lists oldest first; `newer` wins when a message is in both. */
function merge(older: Message[], newer: Message[]): Message[] {
  const ids = new Set(newer.map((m) => m.id));
  return [...older.filter((m) => !ids.has(m.id)), ...newer];
}

const dayKey = (iso: string | null) => (iso ? day(iso) : "");

const aborted = (e: unknown) => (e as Error)?.name === "AbortError";

const mapValues = (o: Record<string, number>) =>
  Object.fromEntries(Object.entries(o).map(([k, v]) => [k, String(v)]));
