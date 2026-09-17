import { useEffect, useRef, useState } from "react";
import type { Conversion } from "../formats";

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
        className={`flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 transition-colors ${
          dragging ? "border-indigo-500 bg-indigo-50" : "border-slate-300 bg-white hover:border-slate-400"
        }`}
      >
        <span className="text-4xl" aria-hidden>
          {conversion.from === "PDF" ? "📄" : "📘"}
        </span>
        <span className="font-medium text-slate-800">
          Drop {conversion.from} files here or click to choose
        </span>
        <span className="text-sm text-slate-500">Several at once is fine · up to {MAX_UPLOAD_MB} MB each</span>
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
        <div role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          <p className="font-medium">Skipped {rejected.length === 1 ? "1 file" : `${rejected.length} files`}:</p>
          <ul className="list-inside list-disc">
            {rejected.map((r, i) => (
              <li key={i} className="break-words">
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {staged.length > 0 && (
        <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
          <ul className="divide-y divide-slate-100">
            {staged.map((s) => (
              <li key={s.key} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="truncate font-medium text-slate-800">{s.file.name}</p>
                  <p className="text-sm text-slate-500">
                    {formatSize(s.file.size)}
                    {s.pages === undefined && " · counting pages…"}
                    {typeof s.pages === "number" && ` · ${s.pages} page${s.pages === 1 ? "" : "s"}`}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setStaged((list) => list.filter((x) => x.key !== s.key))}
                  className="shrink-0 rounded-md px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                  aria-label={`Remove ${s.file.name}`}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
          <div className="flex justify-end border-t border-slate-100 px-4 py-3">
            <button
              type="button"
              onClick={submit}
              className="rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700"
            >
              Convert {staged.length === 1 ? "1 file" : `${staged.length} files`} to {conversion.to}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
