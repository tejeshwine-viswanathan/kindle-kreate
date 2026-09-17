import { useRef, useState } from "react";
import { createJob, deleteJob } from "../api";
import type { Conversion } from "../formats";
import JobItem, { type Item } from "./JobItem";
import Upload from "./Upload";

export default function Converter({ conversion }: { conversion: Conversion }) {
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

  return (
    <section className="space-y-4" aria-labelledby={`${conversion.kind}-title`}>
      <div className="space-y-1">
        <h2 id={`${conversion.kind}-title`} className="text-xl font-semibold text-slate-900">
          {conversion.from} to {conversion.to}
        </h2>
        <p className="text-slate-600">{conversion.description}</p>
      </div>

      <Upload conversion={conversion} onSubmit={submit} />

      {items.length > 0 && (
        <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
            <p className="text-sm font-medium text-slate-600">
              {items.length - finished.length > 0 ? `${items.length - finished.length} in progress · ` : ""}
              {finished.length} finished
            </p>
            {finished.length > 1 && (
              <button
                type="button"
                onClick={() => finished.forEach(dismiss)}
                className="text-sm font-medium text-slate-500 hover:text-slate-800"
              >
                Clear finished
              </button>
            )}
          </div>
          <ul className="divide-y divide-slate-100">
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
