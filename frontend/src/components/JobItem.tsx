import { useEffect, useRef } from "react";
import { ApiError, downloadUrl, getJob, type Job } from "../api";
import type { Conversion } from "../formats";
import ProgressBar from "./ProgressBar";

const POLL_MS = 1500;

export interface Item {
  key: number;
  file: File;
  phase: "uploading" | "converting" | "done" | "failed";
  uploadProgress: number;
  jobId?: string;
  job?: Job;
  error?: string;
  upload: AbortController;
}

interface Props {
  conversion: Conversion;
  item: Item;
  onJob: (job: Job) => void;
  onGone: () => void;
  onCancel: () => void;
  onDismiss: () => void;
}

function stageLabel(conversion: Conversion, item: Item): string {
  if (item.phase === "uploading") return "Uploading…";
  const job = item.job;
  switch (job?.status) {
    case undefined:
    case "queued":
      return "Waiting to start…";
    case "classifying":
      return "Detecting page types…";
    case "processing":
      return conversion.processingLabel(job);
    default:
      return `Packaging ${conversion.to}…`;
  }
}

function progressOf(item: Item): number | null {
  if (item.phase === "uploading") return item.uploadProgress;
  const job = item.job;
  if (!job || job.pages_total === 0) return null;
  if (job.status === "packaging") return 1;
  return job.pages_done / job.pages_total;
}

function formatPages(pages: number[]) {
  const shown = pages.slice(0, 20).join(", ");
  return pages.length > 20 ? `${shown} and ${pages.length - 20} more` : shown;
}

export default function JobItem({ conversion, item, onJob, onGone, onCancel, onDismiss }: Props) {
  // keep the latest callbacks without restarting the poll loop on every render
  const callbacks = useRef({ onJob, onGone });
  callbacks.current = { onJob, onGone };

  const pollingId = item.phase === "converting" ? item.jobId : undefined;
  useEffect(() => {
    if (!pollingId) return;
    const controller = new AbortController();
    let timer: number | undefined;

    const poll = async () => {
      try {
        const job = await getJob(pollingId, controller.signal);
        callbacks.current.onJob(job);
        if (job.status === "done" || job.status === "failed") return;
      } catch (err) {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) return callbacks.current.onGone();
        // transient network error: keep polling
      }
      timer = window.setTimeout(poll, POLL_MS);
    };
    poll();

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [pollingId]);

  const running = item.phase === "uploading" || item.phase === "converting";
  const value = progressOf(item);
  const warnings = item.job?.warnings ?? [];
  const failedPages = item.job?.failed_pages ?? [];
  const lowConfidence = item.job?.low_confidence_pages ?? [];

  return (
    <li className="space-y-2 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-800">{item.file.name}</p>
          <p className="text-sm text-slate-500" aria-live="polite">
            {running && (
              <>
                {stageLabel(conversion, item)}
                {value !== null && <span className="tabular-nums"> · {Math.round(value * 100)}%</span>}
              </>
            )}
            {item.phase === "done" && <span className="text-emerald-700">Ready</span>}
            {item.phase === "failed" && <span className="text-red-700">Failed</span>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {item.phase === "done" && item.jobId && (
            <a
              href={downloadUrl(item.jobId)}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
            >
              Download {conversion.to}
            </a>
          )}
          {running ? (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md px-2 py-1 text-sm font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"
            >
              Cancel
            </button>
          ) : (
            <button
              type="button"
              onClick={onDismiss}
              className="rounded-md px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
              aria-label={`Dismiss ${item.file.name}`}
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {running && <ProgressBar value={value} label={`${item.file.name} progress`} />}

      {item.phase === "failed" && (
        <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          {item.error ?? item.job?.error ?? "Something went wrong."}
        </p>
      )}

      {item.phase === "done" && (warnings.length > 0 || failedPages.length > 0 || lowConfidence.length > 0) && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {warnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
          {failedPages.length > 0 && <p>Pages not converted: {formatPages(failedPages)}</p>}
          {lowConfidence.length > 0 && <p>Pages worth proofreading: {formatPages(lowConfidence)}</p>}
        </div>
      )}
    </li>
  );
}
