import { useCallback, useState } from "react";
import Converter from "./components/Converter";
import { BooksDoodle, CoffeeDoodle, CupIcon } from "./components/Doodles";
import { ArrowIcon, BookIcon, PageIcon } from "./components/Icons";
import { EPUB_TO_PDF, PDF_TO_EPUB, type Conversion } from "./formats";

const CONVERSIONS: Conversion[] = [PDF_TO_EPUB, EPUB_TO_PDF];

const NOTES = [
  { color: "bg-teal", title: "Stays on your machine", text: "Nothing is sent anywhere. Close the tab and it's gone." },
  { color: "bg-sage", title: "Reads scanned books", text: "Photographed or scanned pages are turned into real, selectable text." },
  { color: "bg-butter", title: "Kindle-ready", text: "Every EPUB is checked before you download it, so it just works." },
  { color: "bg-coffee", title: "Batch friendly", text: "Drop a whole folder of files at once and grab each one as it finishes." },
];

function Mark() {
  return (
    <div className="relative mx-auto mb-7 h-20 w-20 animate-bob" aria-hidden>
      <div className="absolute inset-0 translate-x-1.5 translate-y-1.5 rounded-2xl bg-butter" />
      <div className="absolute inset-0 rounded-2xl bg-teal" />
      <div className="absolute inset-0 flex items-center justify-center text-cream">
        <BookIcon width={40} height={40} strokeWidth={1.8} />
      </div>
    </div>
  );
}

export default function App() {
  const [active, setActive] = useState<Conversion["kind"]>(PDF_TO_EPUB.kind);
  const [running, setRunning] = useState<Record<string, number>>({});
  const busy = Object.values(running).some((n) => n > 0);
  const onActive = useCallback(
    (kind: Conversion["kind"]) => (count: number) => setRunning((r) => (r[kind] === count ? r : { ...r, [kind]: count })),
    [],
  );

  return (
    <main className="mx-auto max-w-4xl px-5 pt-16 pb-24 sm:px-8 sm:pt-24">
      {/* margin doodles */}
      <div className="pointer-events-none fixed inset-0 -z-10 hidden text-coffee lg:block" aria-hidden>
        <div className="absolute top-14 left-10 w-40 opacity-[0.28] xl:left-20 xl:w-48">
          <CoffeeDoodle steaming={busy} />
        </div>
        <div className="absolute right-8 bottom-8 w-56 opacity-[0.28] xl:right-16 xl:w-64">
          <BooksDoodle />
        </div>
      </div>

      <header className="mb-12 text-center animate-rise">
        <Mark />
        <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-cream-3 bg-white px-3 py-1 text-xs font-bold tracking-wide text-coffee uppercase">
          <CupIcon spinning={busy} className={busy ? "text-teal" : "text-coffee"} />
          {busy ? "brewing" : "brewing locally"}
        </p>
        <h1 className="font-display text-5xl font-semibold tracking-tight text-ink sm:text-6xl">
          Kindle <em className="font-light text-teal italic">Kreate</em>
        </h1>
        <p className="mx-auto mt-4 max-w-lg text-lg text-coffee">
          Books, made portable. Convert between PDF and EPUB right here on your own computer.
        </p>
      </header>

      <div
        className="mb-6 grid grid-cols-2 gap-3 animate-rise"
        style={{ animationDelay: "60ms" }}
        role="tablist"
        aria-label="Conversion direction"
      >
        {CONVERSIONS.map((c) => {
          const selected = c.kind === active;
          const FromIcon = c.from === "PDF" ? PageIcon : BookIcon;
          const ToIcon = c.to === "PDF" ? PageIcon : BookIcon;
          return (
            <button
              key={c.kind}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`${c.kind}-panel`}
              onClick={() => setActive(c.kind)}
              className={`flex items-center justify-center gap-3 rounded-2xl border-2 px-4 py-4 font-display text-lg font-semibold transition-all duration-200 sm:text-xl ${
                selected
                  ? "border-teal bg-teal text-white shadow-lift"
                  : "border-cream-3 bg-white text-coffee hover:border-coffee-soft hover:bg-coffee-soft/40 hover:text-coffee-2"
              }`}
            >
              <span className={`flex items-center gap-1 ${selected ? "text-white/80" : "text-ink-3"}`}>
                <FromIcon width={18} height={18} />
                <ArrowIcon width={14} height={14} />
                <ToIcon width={18} height={18} />
              </span>
              {c.from} to {c.to}
            </button>
          );
        })}
      </div>

      {CONVERSIONS.map((c) => (
        <div key={c.kind} id={`${c.kind}-panel`} role="tabpanel" hidden={c.kind !== active}>
          <Converter conversion={c} onActiveChange={onActive(c.kind)} />
        </div>
      ))}

      <section className="mt-20 grid gap-6 sm:grid-cols-2 lg:grid-cols-4" aria-label="Good to know">
        {NOTES.map((n, i) => (
          <div key={n.title} className="animate-rise" style={{ animationDelay: `${160 + i * 60}ms` }}>
            <div className={`mb-3 h-1.5 w-8 rounded-full ${n.color}`} aria-hidden />
            <p className="font-display text-lg font-semibold text-ink">{n.title}</p>
            <p className="mt-1 text-sm leading-relaxed text-ink-2">{n.text}</p>
          </div>
        ))}
      </section>

      <footer className="mt-16 text-center text-sm text-ink-3">Free, open source, and happily offline.</footer>
    </main>
  );
}
