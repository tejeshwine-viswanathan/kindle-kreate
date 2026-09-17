import { useEffect, useRef, useState } from "react";
import { createJob, deleteJob } from "../api";
import type { Conversion } from "../formats";
import JobItem, { type Item } from "./JobItem";
import Upload from "./Upload";

interface Props {
  conversion: Conversion;
  /** reports how many uploads/conversions are currently running */
  onActiveChange?: (count: number) => void;
}

export default function Converter({ conversion, onActiveChange }: Props) {
  const [items, setItems] = useState<Item[]>([]);
  const nextKey = useRef(0);

  const update = (key: number, patch: Partial<Item>) =>
    setItems((list) => list.map((item) => (item.key === key ? { ...item, ...patch } : item)));
  const remove = (key: number) => setItems((list) => list.filter((item) => item.key !== key));

  async function startUpload(item: Item) {
    try {
      const jobId = await createJob(
        item.file,
        (uploadProgress) => update(item.key, { uploadProgress }),
        item.upload.signal,
      );
      update(item.key, { phase: "converting", jobId });
    } catch (err) {
      if (item.upload.signal.aborted) return;
      update(item.key, { phase: "failed", error: err instanceof Error ? err.message : "Upload failed." });
    }
  }

  // Uploads start here, in the click handler, not in an effect: React's dev-mode
  // double-mounting would otherwise send every file twice.
  function submit(files: File[]) {
    const added = files.map<Item>((file) => ({
      key: nextKey.current++,
      file,
      phase: "uploading",
      uploadProgress: 0,
      upload: new AbortController(),
    }));
    setItems((list) => [...added, ...list]);
    added.forEach(startUpload);
  }

  function cancel(item: Item) {
    item.upload.abort();
    if (item.jobId) deleteJob(item.jobId).catch(() => undefined);
    remove(item.key);
  }

  function dismiss(item: Item) {
    // finished jobs would expire anyway; deleting now frees the disk space immediately
    if (item.jobId) deleteJob(item.jobId).catch(() => undefined);
    remove(item.key);
  }

  const finished = items.filter((item) => item.phase === "done" || item.phase === "failed");
  const active = items.length - finished.length;
  const activeChange = useRef(onActiveChange);
  activeChange.current = onActiveChange;
  useEffect(() => activeChange.current?.(active), [active]);

  return (
    <section className="space-y-5 animate-rise" aria-labelledby={`${conversion.kind}-title`}>
      <h2 id={`${conversion.kind}-title`} className="sr-only">
        {conversion.from} to {conversion.to}
      </h2>
      <p className="text-center text-coffee">{conversion.description}</p>

      <Upload conversion={conversion} onSubmit={submit} />

      {items.length > 0 && (
        <div className="card overflow-hidden animate-rise">
          <div className="flex items-center justify-between border-b border-cream-3 bg-coffee-soft/30 px-5 py-3">
            <p className="text-sm font-bold text-ink-2">
              {active > 0 && (
                <span className="text-teal">
                  {active} in progress
                  {finished.length > 0 && <span className="text-ink-3"> · </span>}
                </span>
              )}
              {finished.length > 0 && <span>{finished.length} finished</span>}
            </p>
            {finished.length > 1 && (
              <button
                type="button"
                onClick={() => finished.forEach(dismiss)}
                className="text-sm font-bold text-ink-3 underline-offset-4 hover:text-ink hover:underline"
              >
                Clear finished
              </button>
            )}
          </div>
          <ul className="divide-y divide-cream-3">
            {items.map((item) => (
              <JobItem
                key={item.key}
                conversion={conversion}
                item={item}
                onJob={(job) =>
                  update(item.key, {
                    job,
                    phase: job.status === "done" ? "done" : job.status === "failed" ? "failed" : "converting",
                  })
                }
                onGone={() => update(item.key, { phase: "failed", error: "This conversion no longer exists." })}
                onCancel={() => cancel(item)}
                onDismiss={() => dismiss(item)}
              />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
