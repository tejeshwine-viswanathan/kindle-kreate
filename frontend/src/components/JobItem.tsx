import { useEffect, useRef, useState } from "react";
import { ApiError, downloadUrl, getJob, type Job } from "../api";
import type { Conversion } from "../formats";
import {
  AlertIcon,
  CheckIcon,
  CloseIcon,
  DownloadIcon,
  Spinner,
} from "./Icons";
import { HappyReading, RibbonIcon } from "./Doodles";
import ProgressBar from "./ProgressBar";
import { formatSize } from "./Upload";

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
  if (item.phase === "uploading") return "Uploading";
  const job = item.job;
  switch (job?.status) {
    case undefined:
    case "queued":
      return "Waiting to start";
    case "classifying":
      return "Looking at the pages";
    case "processing":
      return conversion.processingLabel(job);
    default:
      return `Packaging the ${conversion.to}`;
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

function Status({ phase }: { phase: Item["phase"] }) {
  const cls =
    "flex h-10 w-10 shrink-0 items-center justify-center rounded-full";
  if (phase === "done")
    return (
      <span className={`${cls} bg-sage-soft text-sage`}>
        <CheckIcon strokeWidth={2.5} />
      </span>
    );
  if (phase === "failed")
    return (
      <span className={`${cls} bg-rose-soft text-rose-deep`}>
        <AlertIcon />
      </span>
    );
  return (
    <span className={`${cls} bg-teal-soft text-teal`}>
      <Spinner />
    </span>
  );
}

export default function JobItem({
  conversion,
  item,
  onJob,
  onGone,
  onCancel,
  onDismiss,
}: Props) {
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
        if (err instanceof ApiError && err.status === 404)
          return callbacks.current.onGone();
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

  // shown for a few seconds after the download link is clicked
  const [celebrating, setCelebrating] = useState(false);
  useEffect(() => {
    if (!celebrating) return;
    const t = window.setTimeout(() => setCelebrating(false), 5000);
    return () => window.clearTimeout(t);
  }, [celebrating]);

  const running = item.phase === "uploading" || item.phase === "converting";
  const value = progressOf(item);
  const warnings = item.job?.warnings ?? [];
  const failedPages = item.job?.failed_pages ?? [];
  const lowConfidence = item.job?.low_confidence_pages ?? [];
  const hasNotes =
    warnings.length > 0 || failedPages.length > 0 || lowConfidence.length > 0;

  return (
    <li className="space-y-3 px-5 py-4 animate-rise">
      <div className="flex items-center gap-4">
        <Status phase={item.phase} />

        <div className="min-w-0 flex-1">
          <p className="truncate font-bold text-ink">{item.file.name}</p>
          <p className="mt-0.5 text-sm text-ink-2" aria-live="polite">
            <span className="text-ink-3">{formatSize(item.file.size)}</span>
            <span className="mx-1.5 text-ink-3">·</span>
            {running && (
              <>
                {stageLabel(conversion, item)}
                {value !== null && (
                  <span className="ml-1.5 font-bold text-teal tabular-nums">
                    {Math.round(value * 100)}%
                  </span>
                )}
              </>
            )}
            {item.phase === "done" && (
              <span className="font-bold text-sage">Ready</span>
            )}
            {item.phase === "failed" && (
              <span className="font-bold text-rose-deep">Didn't work</span>
            )}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {item.phase === "done" && item.jobId && (
            <span className="relative">
              <span
                className="absolute -top-3 -left-3 text-butter drop-shadow-sm animate-drop"
                aria-hidden
              >
                <RibbonIcon />
              </span>
              <a
                href={downloadUrl(item.jobId)}
                onClick={() => setCelebrating(true)}
                className="btn-main px-4 py-2 text-sm"
              >
                <DownloadIcon width={16} height={16} strokeWidth={2.5} />
                <span className="hidden sm:inline">Download</span>{" "}
                {conversion.to}
              </a>
            </span>
          )}
          {running ? (
            <button type="button" onClick={onCancel} className="btn-quiet">
              Cancel
            </button>
          ) : (
            <button
              type="button"
              onClick={onDismiss}
              className="flex h-9 w-9 items-center justify-center rounded-full text-ink-3 transition-colors hover:bg-cream-2 hover:text-ink"
              aria-label={`Dismiss ${item.file.name}`}
            >
              <CloseIcon width={16} height={16} />
            </button>
          )}
        </div>
      </div>

      {running && (
        <ProgressBar value={value} label={`${item.file.name} progress`} />
      )}

      {celebrating && (
        <div className="flex justify-end">
          <HappyReading />
        </div>
      )}

      {item.phase === "failed" && (
        <p
          role="alert"
          className="rounded-2xl bg-rose-soft px-4 py-2.5 text-sm text-rose-deep"
        >
          {item.error ?? item.job?.error ?? "Something went wrong."}
        </p>
      )}

      {item.phase === "done" && hasNotes && (
        <div className="space-y-1 rounded-2xl bg-butter-soft px-4 py-2.5 text-sm text-ink-2">
          {warnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
          {failedPages.length > 0 && (
            <p>Pages not converted: {formatPages(failedPages)}</p>
          )}
          {lowConfidence.length > 0 && (
            <p>Pages worth proofreading: {formatPages(lowConfidence)}</p>
          )}
        </div>
      )}
    </li>
  );
}
