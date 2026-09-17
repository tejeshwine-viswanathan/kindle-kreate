import { useEffect, useRef, useState } from "react";
import type { Conversion } from "../formats";
import { BookIcon, CloseIcon, PageIcon, UploadIcon } from "./Icons";

export const MAX_UPLOAD_MB = 500;
const PAGE_COUNT_LIMIT_MB = 100; // parsing huge files client-side just for a count isn't worth it

interface Props {
  conversion: Conversion;
  onSubmit: (files: File[]) => void;
}

interface Staged {
  key: number;
  file: File;
  /** undefined = not counted yet, null = unknown */
  pages?: number | null;
}

export function formatSize(bytes: number) {
  return bytes >= 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;
}

async function countPages(file: File): Promise<number | null> {
  if (file.size > PAGE_COUNT_LIMIT_MB << 20) return null;
  try {
    const { PDFDocument } = await import("pdf-lib");
    const doc = await PDFDocument.load(await file.arrayBuffer(), { ignoreEncryption: true, updateMetadata: false });
    return doc.getPageCount();
  } catch {
    return null; // the server does the authoritative validation
  }
}

const article = (format: string) => (format === "EPUB" ? "an" : "a");

export default function Upload({ conversion, onSubmit }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const nextKey = useRef(0);
  const [staged, setStaged] = useState<Staged[]>([]);
  const [dragging, setDragging] = useState(false);
  const [rejected, setRejected] = useState<string[]>([]);

  // Count PDF pages one file at a time so a big multi-file drop doesn't load everything into memory at once.
  const pending = conversion.from === "PDF" ? staged.find((s) => s.pages === undefined) : undefined;
  useEffect(() => {
    if (!pending) return;
    let current = true;
    countPages(pending.file).then((pages) => {
      if (current) setStaged((list) => list.map((s) => (s.key === pending.key ? { ...s, pages } : s)));
    });
    return () => {
      current = false;
    };
  }, [pending]);

  function add(files: FileList | null | undefined) {
    if (!files?.length) return;
    const accepted: Staged[] = [];
    const problems: string[] = [];
    for (const file of Array.from(files)) {
      if (!file.name.toLowerCase().endsWith(conversion.extension)) {
        problems.push(`${file.name}: not ${article(conversion.from)} ${conversion.from} file`);
      } else if (file.size > MAX_UPLOAD_MB << 20) {
        problems.push(`${file.name}: larger than ${MAX_UPLOAD_MB} MB`);
      } else if (
        !staged.some((s) => s.file.name === file.name && s.file.size === file.size && s.file.lastModified === file.lastModified)
      ) {
        accepted.push({ key: nextKey.current++, file, pages: conversion.from === "PDF" ? undefined : null });
      }
    }
    setRejected(problems);
    setStaged((list) => [...list, ...accepted]);
  }

  function submit() {
    onSubmit(staged.map((s) => s.file));
    setStaged([]);
    setRejected([]);
  }

  const FileIcon = conversion.from === "PDF" ? PageIcon : BookIcon;

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          add(e.dataTransfer.files);
        }}
        className={`group flex w-full flex-col items-center gap-3 rounded-3xl border-2 border-dashed px-6 py-12 transition-all duration-200 ${
          dragging
            ? "scale-[1.01] border-teal bg-teal-soft"
            : "border-cream-3 bg-white/60 hover:border-ink-3 hover:bg-white"
        }`}
      >
        <span
          className={`flex h-14 w-14 items-center justify-center rounded-2xl transition-colors ${
            dragging ? "bg-teal text-cream" : "bg-cream-2 text-ink-2 group-hover:bg-teal-soft group-hover:text-teal"
          }`}
        >
          <UploadIcon width={26} height={26} />
        </span>
        <span className="font-display text-xl font-semibold text-ink">
          {dragging ? "Let go!" : `Drop ${article(conversion.from)} ${conversion.from} here`}
        </span>
        <span className="text-sm text-ink-2">
          or <span className="font-bold text-teal underline underline-offset-4">browse your files</span>
          <span className="text-ink-3"> · several at once is fine, up to {MAX_UPLOAD_MB} MB each</span>
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={conversion.accept}
        className="hidden"
        onChange={(e) => {
          add(e.target.files);
          e.target.value = "";
        }}
      />

      {rejected.length > 0 && (
        <div role="alert" className="rounded-2xl bg-rose-soft px-4 py-3 text-sm text-rose-deep">
          <p className="font-bold">Skipped {rejected.length === 1 ? "1 file" : `${rejected.length} files`}</p>
          <ul className="mt-1 list-inside list-disc">
            {rejected.map((r, i) => (
              <li key={i} className="break-words">
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {staged.length > 0 && (
        <div className="card overflow-hidden animate-rise">
          <ul className="divide-y divide-cream-3">
            {staged.map((s) => (
              <li key={s.key} className="flex items-center gap-4 px-5 py-3.5">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-cream-2 text-ink-2">
                  <FileIcon />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-bold text-ink">{s.file.name}</p>
                  <p className="text-sm text-ink-2">
                    {formatSize(s.file.size)}
                    {s.pages === undefined && <span className="text-ink-3"> · counting pages…</span>}
                    {typeof s.pages === "number" && ` · ${s.pages} page${s.pages === 1 ? "" : "s"}`}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setStaged((list) => list.filter((x) => x.key !== s.key))}
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-ink-3 transition-colors hover:bg-cream-2 hover:text-ink"
                  aria-label={`Remove ${s.file.name}`}
                >
                  <CloseIcon width={16} height={16} />
                </button>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between gap-3 border-t border-cream-3 bg-coffee-soft/30 px-5 py-3.5">
            <p className="text-sm text-ink-2">
              {staged.length === 1 ? "1 file" : `${staged.length} files`} ready
            </p>
            <button type="button" onClick={submit} className="btn-main">
              Convert to {conversion.to}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
