import { Fragment, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api, post } from "../lib/api";
import { NOUNS, bytes, clock, count, date, day, stem, time } from "../lib/format";
import { CHAT_TYPE_LABEL, MEDIA_LABEL, STATUS_LABEL, messagePreview } from "../lib/labels";
import { useRevision } from "../lib/live";
import { Highlight } from "../components/Highlight";
import { ArrowDownIcon, BackIcon, CloseIcon, RefreshIcon, SearchIcon } from "../components/icons";
import { Alert, Avatar, Button, IconButton, Spinner } from "../components/ui";

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
  /** There are older messages before this page. */
  has_more: boolean;
  /** The page stops before the chat's newest message. */
  has_newer: boolean;
}

/** Search results, newest first. */
interface Found {
  messages: Message[];
  has_more: boolean;
}

const PAGE = 50;
const CHECK_EVERY_MS = 15_000;
/** Closer than this to the end counts as reading the newest messages. */
const NEAR_END_PX = 120;
/** Farther than this from the end, a button offers the way back to it. */
const FAR_FROM_END_PX = 700;
const SEARCH_AFTER_MS = 500;

/** Text shown straight on the wallpaper sits in one of these. */
const PILL = "rounded-full bg-pill px-3 py-1 text-sm text-white";

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
  // After opening a search result the list is a stretch from the middle of
  // the history: it grows both ways until it reaches the newest message.
  const [hasNewer, setHasNewer] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const [loadingNewer, setLoadingNewer] = useState(false);
  const [newerError, setNewerError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [unseen, setUnseen] = useState(0); // new messages below while reading older ones
  const [farFromEnd, setFarFromEnd] = useState(false);
  const [flash, setFlash] = useState<number | null>(null);
  const [searching, setSearching] = useState(false);
  const [resultsHidden, setResultsHidden] = useState(false); // narrow screens: results cover the messages
  const [opening, setOpening] = useState<number | null>(null); // the found message being fetched
  const [picked, setPicked] = useState<number | null>(null);

  const scroller = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const endSentinel = useRef<HTMLDivElement>(null);
  const requests = useRef<AbortController | null>(null);
  const current = useRef<Message[] | null>(null);
  current.current = messages;
  const middle = useRef(false);
  middle.current = hasNewer;
  const busyOlder = useRef(false);
  const busyNewer = useRef(false);
  // A link from a search result names the message to open the chat at.
  const [link] = useSearchParams();
  const startAt = useRef(Number(link.get("m")) || 0);
  // Where the view should be once the next list is laid out: at the end, this
  // many pixels from it (so adding older messages on top doesn't move what is
  // being read), or on one message.
  const settle = useRef<"end" | number | { message: number } | null>(null);

  const highlight = useCallback((id: number) => {
    setFlash(id);
    window.setTimeout(() => setFlash((now) => (now === id ? null : now)), 1800);
  }, []);

  /** Brings a loaded message to the middle of the view. */
  const show = useCallback((id: number, smooth: boolean) => {
    const el = scroller.current;
    const target = document.getElementById(`message-${id}`);
    if (!el || !target) return false;
    const box = el.getBoundingClientRect();
    const t = target.getBoundingClientRect();
    el.scrollTo({ top: el.scrollTop + t.top - box.top - (box.height - t.height) / 2, behavior: smooth ? "smooth" : "auto" });
    return true;
  }, []);

  useLayoutEffect(() => {
    const el = scroller.current;
    const to = settle.current;
    if (!el || to === null) return;
    settle.current = null;
    if (to === "end") el.scrollTop = el.scrollHeight;
    else if (typeof to === "number") el.scrollTop = el.scrollHeight - to;
    else {
      // A deleted message isn't there: its nearest neighbour stands in.
      const list = current.current ?? [];
      const near = list.find((m) => m.id >= to.message) ?? list[list.length - 1];
      if (near && show(near.id, false) && near.id === to.message) highlight(near.id);
    }
  }, [messages, show, highlight]);

  const url = useCallback(
    (params: Record<string, number>) =>
      `/api/chats/${chatId}/messages?${new URLSearchParams({ limit: String(PAGE), ...mapValues(params) })}`,
    [chatId],
  );

  /** Shows the chat's newest messages — or, the first time, the stretch
   * around the message the page was opened at. */
  const load = useCallback(async () => {
    setError(null);
    const target = startAt.current;
    try {
      const page = await api<Page>(url(target ? { around: target } : {}), { signal: requests.current?.signal });
      startAt.current = 0;
      settle.current = target ? { message: target } : "end";
      setChat(page.chat);
      setMessages(page.messages);
      setHasOlder(page.has_more);
      setHasNewer(page.has_newer);
      setUnseen(0);
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

  /** Reading from the middle of the history: the page that follows the last message shown. */
  const loadFollowing = useCallback(async () => {
    const list = current.current;
    if (busyNewer.current || !list?.length) return;
    busyNewer.current = true;
    setLoadingNewer(true);
    setNewerError(null);
    try {
      const page = await api<Page>(url({ after: list[list.length - 1].id, forward: 1 }), {
        signal: requests.current?.signal,
      });
      setMessages((now) => (now ? merge(now, page.messages) : now));
      setHasNewer(page.has_newer);
    } catch (e) {
      if (aborted(e)) return;
      setNewerError(e instanceof ApiError ? e.message : "تعذّر تحميل الرسائل الأحدث.");
    } finally {
      busyNewer.current = false;
      setLoadingNewer(false);
    }
  }, [url]);

  /** Fetches what arrived after the newest message shown. */
  const loadNewer = useCallback(
    async (quiet: boolean) => {
      const list = current.current;
      if (busyNewer.current || list === null || middle.current) return;
      busyNewer.current = true;
      try {
        const after = list.length ? list[list.length - 1].id : 0;
        const page = await api<Page>(url({ after }), { signal: requests.current?.signal });
        if (!quiet) setError(null);
        if (page.messages.length === 0 || middle.current) return;
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

  // The same at the other end, while the list doesn't reach the newest message.
  const lastId = messages?.[messages.length - 1]?.id;
  useEffect(() => {
    const bottom = endSentinel.current;
    const root = scroller.current;
    if (!bottom || !root || !hasNewer || newerError) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadFollowing();
      },
      { root, rootMargin: "0px 0px 400px 0px" },
    );
    observer.observe(bottom);
    return () => observer.disconnect();
  }, [lastId, hasNewer, newerError, loadFollowing]);

  async function refresh() {
    setRefreshing(true);
    if (current.current === null) await load();
    else if (hasNewer) await loadFollowing();
    else await loadNewer(false);
    setRefreshing(false);
  }

  function toEnd() {
    setUnseen(0);
    if (hasNewer) {
      load();
      return;
    }
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }

  function onScroll() {
    const el = scroller.current;
    if (!el) return;
    const fromEnd = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (unseen > 0 && !hasNewer && fromEnd < NEAR_END_PX) setUnseen(0);
    setFarFromEnd(fromEnd > FAR_FROM_END_PX);
  }

  const jumpTo = useCallback(
    (id: number) => {
      if (show(id, true)) highlight(id);
    },
    [show, highlight],
  );

  /** Goes to a message found by search: scrolls to it when it is among those
   * loaded, otherwise shows the stretch of the chat around it. */
  const openAt = useCallback(
    async (id: number) => {
      setPicked(id);
      if (!window.matchMedia("(min-width: 64rem)").matches) setResultsHidden(true);
      if (current.current?.some((m) => m.id === id)) {
        jumpTo(id);
        return;
      }
      setOpening(id);
      setError(null);
      try {
        const page = await api<Page>(url({ around: id }), { signal: requests.current?.signal });
        settle.current = { message: id };
        setMessages(page.messages);
        setHasOlder(page.has_more);
        setHasNewer(page.has_newer);
        setUnseen(0);
        setOlderError(null);
        setNewerError(null);
      } catch (e) {
        if (aborted(e)) return;
        setError(e instanceof ApiError ? e.message : "تعذّر فتح الرسالة.");
      } finally {
        setOpening((now) => (now === id ? null : now));
      }
    },
    [url, jumpTo],
  );

  function toggleSearch() {
    if (searching && resultsHidden) setResultsHidden(false);
    else {
      setSearching(!searching);
      setResultsHidden(false);
    }
  }

  const setFile = useCallback((id: number, file: FileState) => {
    setMessages(
      (now) => now?.map((m) => (m.id === id && m.media ? { ...m, media: { ...m.media, file } } : m)) ?? now,
    );
  }, []);

  const byId = useMemo(() => new Map(messages?.map((m) => [m.id, m])), [messages]);
  const typeLabel = chat ? CHAT_TYPE_LABEL[chat.type] : "";
  const title = chat?.title || "المحادثة";
  // A channel's posts each stand alone; elsewhere a run of messages from one
  // person reads as a group.
  const grouped = chat?.type !== "channel";
  // Where days and runs begin and end. Worked out when the list changes, not
  // on every render: formatting a date per message is the costly part, and
  // the view renders again on scrolling, flashes and loading states.
  const layout = useMemo(() => {
    const list = messages ?? [];
    const days = list.map((m) => (m.date ? day(m.date) : ""));
    // Two neighbouring messages that read as one person's run.
    const run = (i: number, j: number) => {
      const a = list[i];
      const b = list[j];
      return !!a && !!b && !a.service && !b.service && a.out === b.out && a.sender === b.sender && days[i] === days[j];
    };
    return list.map((_, i) => ({
      day: i === 0 || days[i - 1] !== days[i] ? days[i] : "",
      first: !grouped || !run(i - 1, i),
      last: !grouped || !run(i, i + 1),
    }));
  }, [messages, grouped]);

  return (
    <div className="flex h-[calc(100dvh-10.5rem)] min-h-96 flex-col overflow-hidden rounded-2xl border border-line bg-white shadow-xs md:h-[calc(100dvh-4rem)]">
      <header className="flex items-center gap-2 border-b border-line px-2 py-2 sm:gap-3 sm:px-3">
        <Link
          to="/chats"
          aria-label="كل المحادثات"
          title="كل المحادثات"
          className="grid size-11 shrink-0 place-items-center rounded-full text-ink-soft transition-colors hover:bg-canvas hover:text-ink"
        >
          <BackIcon />
        </Link>
        <Avatar id={chatId} name={title} className="size-11 text-base" />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold leading-snug">
            <bdi>{title}</bdi>
          </h1>
          <p className="truncate text-sm text-ink-soft">{typeLabel ? `${typeLabel} · للقراءة فقط` : "للقراءة فقط"}</p>
        </div>
        <IconButton
          label="ابحث في المحادثة"
          aria-expanded={searching}
          onClick={toggleSearch}
          disabled={messages === null}
          className={searching ? "bg-accent-tint text-accent-strong" : ""}
        >
          <SearchIcon />
        </IconButton>
        <IconButton label="تحديث" onClick={refresh} busy={refreshing} disabled={messages === null && !error}>
          <RefreshIcon />
        </IconButton>
      </header>

      {error && (
        <div className="border-b border-line p-3">
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
        </div>
      )}

      <div className="relative flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1">
          <div
            ref={scroller}
            onScroll={onScroll}
            tabIndex={0}
            role="region"
            aria-label="رسائل المحادثة"
            className="chat-wallpaper thin-scroll h-full overflow-y-auto [overflow-anchor:none]"
          >
            {messages === null ? (
              !error && (
                <div className="flex justify-center p-5">
                  <span className={`flex items-center gap-2 ${PILL}`}>
                    <Spinner /> جارٍ تحميل الرسائل من تيليغرام…
                  </span>
                </div>
              )
            ) : messages.length === 0 ? (
              <div className="flex justify-center p-5">
                <span className={PILL}>لا توجد رسائل في هذه المحادثة.</span>
              </div>
            ) : (
              <div className="mx-auto max-w-3xl px-3 py-3 sm:px-5">
                <div ref={sentinel} className="flex min-h-11 flex-wrap items-center justify-center gap-2">
                  {!hasOlder ? (
                    <span className={PILL}>بداية المحادثة</span>
                  ) : loadingOlder ? (
                    <span className={`flex items-center gap-2 ${PILL}`}>
                      <Spinner /> جارٍ تحميل رسائل أقدم…
                    </span>
                  ) : (
                    <>
                      {olderError && <span className="rounded-full bg-danger px-3 py-1 text-sm text-white">{olderError}</span>}
                      <button type="button" onClick={loadOlder} className={`${PILL} min-h-9 font-semibold hover:bg-ink/70`}>
                        {olderError ? "أعد المحاولة" : "حمّل رسائل أقدم"}
                      </button>
                    </>
                  )}
                </div>
                {messages.map((m, i) => {
                  const { day: newDay, first, last } = layout[i];
                  const reply = m.reply_to !== null ? byId.get(m.reply_to) : undefined;
                  return (
                    <Fragment key={m.id}>
                      {newDay && (
                        <p className="my-2 flex justify-center">
                          <span className={`${PILL} font-semibold`}>{newDay}</span>
                        </p>
                      )}
                      <MessageItem
                        chatId={chatId}
                        message={m}
                        showSender={!!m.sender && !m.out && first}
                        first={first}
                        last={last}
                        reply={reply}
                        flash={flash === m.id}
                        onJump={jumpTo}
                        onFile={setFile}
                      />
                    </Fragment>
                  );
                })}
                {hasNewer && (
                  <div ref={endSentinel} className="mt-2 flex min-h-11 flex-wrap items-center justify-center gap-2">
                    {loadingNewer ? (
                      <span className={`flex items-center gap-2 ${PILL}`}>
                        <Spinner /> جارٍ تحميل رسائل أحدث…
                      </span>
                    ) : (
                      <>
                        {newerError && <span className="rounded-full bg-danger px-3 py-1 text-sm text-white">{newerError}</span>}
                        <button type="button" onClick={loadFollowing} className={`${PILL} min-h-9 font-semibold hover:bg-ink/70`}>
                          {newerError ? "أعد المحاولة" : "حمّل رسائل أحدث"}
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {opening !== null && (
            <p role="status" className="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
              <span className={`flex items-center gap-2 shadow-lg ${PILL}`}>
                <Spinner /> جارٍ فتح الرسالة…
              </span>
            </p>
          )}

          {messages !== null && (unseen > 0 || hasNewer || farFromEnd) && (
            <button
              type="button"
              onClick={toEnd}
              aria-label={unseen > 0 ? `${count(unseen, NOUNS.message)} جديدة. اذهب إلى أحدث الرسائل` : "اذهب إلى أحدث الرسائل"}
              title="أحدث الرسائل"
              className="absolute bottom-4 end-4 grid size-12 place-items-center rounded-full bg-white text-ink-soft shadow-lg transition-colors hover:text-accent"
            >
              <ArrowDownIcon />
              {unseen > 0 && (
                <span className="absolute -top-2 start-1/2 min-w-6 translate-x-1/2 rounded-full bg-accent px-1.5 text-center text-xs font-semibold leading-6 text-white">
                  {unseen > 99 ? "+٩٩" : unseen.toLocaleString("ar-EG")}
                </span>
              )}
            </button>
          )}
        </div>

        {searching && (
          <SearchPanel
            chatId={chatId}
            title={title}
            hidden={resultsHidden}
            picked={picked}
            onPick={openAt}
            onClose={() => setSearching(false)}
          />
        )}
      </div>
    </div>
  );
}

/** Finds messages of the chat with Telegram's own search; picking one shows
 * it in its place in the chat. */
function SearchPanel({ chatId, title, hidden, picked, onPick, onClose }: {
  chatId: number;
  title: string;
  /** Kept alive but out of sight (narrow screens, while reading a result). */
  hidden: boolean;
  picked: number | null;
  onPick: (id: number) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [query, setQuery] = useState(""); // what the results shown are for
  const [found, setFound] = useState<Message[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const run = useRef(0); // only the latest request may show its answer
  const asked = useRef("");

  const search = useCallback(
    async (q: string, before = 0) => {
      const mine = ++run.current;
      asked.current = q;
      setBusy(true);
      setError(null);
      try {
        const params = new URLSearchParams({ q, ...(before ? { before: String(before) } : {}) });
        const res = await api<Found>(`/api/chats/${chatId}/messages/search?${params}`);
        if (mine !== run.current) return;
        setQuery(q);
        setFound((now) => (before && now ? [...now, ...res.messages] : res.messages));
        setHasMore(res.has_more);
      } catch (e) {
        if (mine !== run.current) return;
        setError(e instanceof ApiError ? e.message : "تعذّر البحث.");
      } finally {
        if (mine === run.current) setBusy(false);
      }
    },
    [chatId],
  );

  // Searches shortly after typing stops.
  useEffect(() => {
    const q = text.trim().replace(/\s+/g, " ");
    if (!q) {
      run.current++;
      asked.current = "";
      setFound(null);
      setBusy(false);
      setError(null);
      return;
    }
    const timer = window.setTimeout(() => {
      if (q !== asked.current) search(q);
    }, SEARCH_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [text, search]);

  useEffect(() => {
    if (!hidden) input.current?.focus();
  }, [hidden]);

  useEffect(
    () => () => {
      run.current++;
    },
    [],
  );

  return (
    <aside
      aria-label="البحث في المحادثة"
      className={`absolute inset-0 z-10 flex-col bg-white lg:static lg:w-80 lg:shrink-0 lg:border-s lg:border-line ${
        hidden ? "hidden lg:flex" : "flex"
      }`}
    >
      <form
        role="search"
        className="flex items-center gap-1 border-b border-line p-2"
        onSubmit={(e) => {
          e.preventDefault();
          const q = text.trim().replace(/\s+/g, " ");
          if (q) search(q);
        }}
      >
        <div className="relative min-w-0 flex-1">
          <SearchIcon className="pointer-events-none absolute start-3 top-1/2 size-5 -translate-y-1/2 text-ink-soft" />
          <input
            ref={input}
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
            }}
            maxLength={200}
            placeholder="ابحث في الرسائل"
            aria-label="ابحث في رسائل هذه المحادثة"
            className="min-h-11 w-full rounded-full border border-transparent bg-canvas pe-4 ps-10 focus:border-accent focus:bg-white focus:outline-none [&::-webkit-search-cancel-button]:hidden"
          />
        </div>
        <IconButton label="أغلق البحث" onClick={onClose}>
          <CloseIcon />
        </IconButton>
      </form>

      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto">
        <p role="status" className="px-4 py-2 text-sm text-ink-soft">
          {error ? (
            <span className="text-danger">{error}</span>
          ) : busy && found === null ? (
            <span className="flex items-center gap-2">
              <Spinner /> جارٍ البحث…
            </span>
          ) : found === null ? (
            "اكتب كلمة للبحث عنها في نص الرسائل وأسماء الملفات."
          ) : found.length === 0 ? (
            "لا توجد رسائل مطابقة."
          ) : (
            `${count(found.length, NOUNS.message)}${hasMore ? " حتى الآن" : ""}`
          )}
        </p>
        {found !== null && found.length > 0 && (
          <ul className="divide-y divide-line/70">
            {found.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => onPick(m.id)}
                  aria-current={m.id === picked}
                  className={`block w-full px-4 py-2.5 text-start transition-colors ${
                    m.id === picked ? "bg-accent-tint" : "hover:bg-canvas"
                  }`}
                >
                  <span className="flex items-baseline justify-between gap-3">
                    <bdi className="truncate font-semibold">{m.sender ?? (m.out ? "أنت" : title)}</bdi>
                    <span className="shrink-0 text-xs text-ink-soft">{date(m.date)}</span>
                  </span>
                  <span dir="auto" className="mt-0.5 line-clamp-2 text-sm text-ink-soft [overflow-wrap:anywhere]">
                    <Highlight text={messagePreview(m)} query={query} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {found !== null && hasMore && (
          <div className="flex justify-center p-3">
            <Button variant="secondary" busy={busy} onClick={() => search(query, found[found.length - 1].id)}>
              نتائج أقدم
            </Button>
          </div>
        )}
      </div>
    </aside>
  );
}

const NAME_COLORS = [
  "text-[#c0392b]",
  "text-[#b45f06]",
  "text-[#7e4bc9]",
  "text-[#2e7d32]",
  "text-[#1c7d8c]",
  "text-[#2078c5]",
  "text-[#b83a7c]",
];

/** A colour per person, the same every time, as Telegram does in groups. */
function nameColor(name: string): string {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)!) >>> 0;
  return NAME_COLORS[h % NAME_COLORS.length];
}

const MessageItem = memo(function MessageItem({ chatId, message: m, showSender, first, last, reply, flash, onJump, onFile }: {
  chatId: number;
  message: Message;
  showSender: boolean;
  /** First / last of a run of messages from the same person. */
  first: boolean;
  last: boolean;
  /** The message this one answers, when it is among those loaded. */
  reply: Message | undefined;
  flash: boolean;
  onJump: (id: number) => void;
  onFile: (id: number, file: FileState) => void;
}) {
  if (m.service) {
    return (
      <p id={`message-${m.id}`} className="my-2 flex justify-center">
        <span className={`${PILL} text-center ${flash ? "ring-3 ring-accent" : ""}`}>
          {SERVICE_LABEL[m.service] ?? SERVICE_LABEL.other}
          {m.sender && (
            <>
              {" · "}
              <bdi>{m.sender}</bdi>
            </>
          )}
          {m.date && <> · {time(m.date)}</>}
        </span>
      </p>
    );
  }
  // The corner next to the sender's side is squared off on the last bubble of a group, like a tail.
  const tail = last ? (m.out ? "rounded-ee-md" : "rounded-es-md") : "";
  return (
    <div id={`message-${m.id}`} className={`flex ${m.out ? "justify-end" : "justify-start"} ${first ? "mt-2" : "mt-0.5"}`}>
      <div
        className={`min-w-0 max-w-[min(100%,34rem)] rounded-2xl px-3 py-1.5 shadow-bubble transition-shadow duration-300 ${
          m.out ? "bg-bubble-out" : "bg-white"
        } ${tail} ${flash ? "ring-3 ring-accent" : ""}`}
      >
        {showSender && <bdi className={`block text-sm font-semibold ${nameColor(m.sender!)}`}>{m.sender}</bdi>}
        {m.forwarded_from !== null && (
          <p className="text-sm text-accent">
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
              className="my-1 block w-full rounded-md border-s-[3px] border-accent bg-accent/10 px-2 py-1 text-start text-sm hover:bg-accent/15"
            >
              <span className="block font-semibold text-accent">
                {reply.sender ? <bdi>{reply.sender}</bdi> : reply.out ? "أنت" : "ردّاً على"}
              </span>
              <span dir="auto" className="line-clamp-1 text-ink-soft">
                {reply.text ||
                  (reply.service ? SERVICE_LABEL[reply.service] : MEDIA_LABEL[reply.media?.kind ?? ""]) ||
                  MEDIA_LABEL.other}
              </span>
            </button>
          ) : (
            <p className="my-1 rounded-md border-s-[3px] border-accent bg-accent/10 px-2 py-1 text-sm text-ink-soft">
              ردّ على رسالة أقدم
            </p>
          ))}
        {m.media && <MediaView chatId={chatId} message={m} media={m.media} onFile={onFile} />}
        {m.text && (
          <p dir="auto" className="whitespace-pre-wrap [overflow-wrap:anywhere]">
            <RichText text={m.text} entities={m.entities} />
          </p>
        )}
        <p className={`-mb-0.5 text-end text-xs ${m.out ? "text-bubble-out-meta" : "text-ink-soft"}`}>
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
    const box = (
      <div
        className="relative my-1 overflow-hidden rounded-xl bg-line/60"
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
          <span className="absolute bottom-2 start-2 rounded-full bg-ink/75 px-2 py-0.5 text-xs text-white">
            {label}
            {media.duration !== null && ` · ${clock(media.duration)}`}
          </span>
        )}
      </div>
    );
    if (!media.file) return box;
    return (
      <>
        {box}
        <div className="mb-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1" style={{ maxWidth: `${width}rem` }}>
          <span className="text-sm text-ink-soft">{bytes(media.size)}</span>
          <FileAction compact chatId={chatId} msgId={m.id} file={media.file} onChange={(f) => onFile(m.id, f)} />
        </div>
      </>
    );
  }

  if (media.kind === "file") {
    const name = media.name || "ملف بلا اسم";
    const ext = media.file?.ext ?? null;
    return (
      <div className="my-1 flex flex-wrap items-center gap-3">
        <span
          className="grid size-12 shrink-0 place-items-center rounded-full bg-accent text-xs font-semibold text-white"
          dir="ltr"
        >
          {(ext ?? "؟").replace(".", "").toUpperCase().slice(0, 4)}
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
    <p className="my-0.5 flex flex-wrap items-center gap-x-2 text-ink-soft">
      <span className="rounded-full bg-accent-tint px-2.5 py-0.5 text-sm text-accent-strong">{label}</span>
      {media.name && <bdi className="[overflow-wrap:anywhere]">{media.name}</bdi>}
      {media.duration !== null && <span>{clock(media.duration)}</span>}
      {media.size !== null && media.kind !== "sticker" && <span className="text-sm">{bytes(media.size)}</span>}
      {media.file && (
        <FileAction compact chatId={chatId} msgId={m.id} file={media.file} onChange={(f) => onFile(m.id, f)} />
      )}
    </p>
  );
}

/** Adds the message's file to the library, or says where it stands there. */
function FileAction({ chatId, msgId, file, onChange, compact }: {
  chatId: number;
  msgId: number;
  file: FileState;
  onChange: (file: FileState) => void;
  /** Under a picture or beside a recording: smaller, and says where it goes. */
  compact?: boolean;
}) {
  const revision = useRevision();
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
        className={`inline-flex items-center font-semibold text-accent hover:bg-accent-tint ${
          compact ? "min-h-9 rounded-full px-3 text-sm" : "min-h-11 rounded-lg px-4"
        }`}
      >
        في مكتبتك
      </Link>
    );
  }
  if (waiting) {
    return (
      <span className={`inline-flex items-center text-accent ${compact ? "min-h-9 text-sm" : "min-h-11"}`}>
        {STATUS_LABEL[file.status!]}
      </span>
    );
  }
  return (
    <Button
      busy={busy}
      variant={compact ? "secondary" : "primary"}
      className={compact ? "min-h-9! rounded-full! px-3! text-sm" : ""}
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
      {failed ? "أعد المحاولة" : compact ? "نزّل إلى المكتبة" : "نزّل"}
    </Button>
  );
}

const ENTITY_STYLE: Record<string, string> = {
  bold: "font-semibold",
  italic: "italic",
  underline: "underline",
  strike: "line-through",
  code: "rounded bg-ink/8 px-1 font-mono text-[0.92em]",
  pre: "rounded bg-ink/8 px-1 font-mono text-[0.92em]",
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
          <a href={href} target="_blank" rel="noreferrer noopener" className="text-accent underline underline-offset-2">
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

const aborted = (e: unknown) => (e as Error)?.name === "AbortError";

const mapValues = (o: Record<string, number>) =>
  Object.fromEntries(Object.entries(o).map(([k, v]) => [k, String(v)]));
