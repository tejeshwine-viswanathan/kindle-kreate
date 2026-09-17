export type JobStatus = "queued" | "classifying" | "processing" | "packaging" | "done" | "failed";
export type JobKind = "pdf_to_epub" | "epub_to_pdf";

export interface Job {
  job_id: string;
  kind: JobKind;
  filename: string;
  status: JobStatus;
  pages_total: number;
  pages_done: number;
  error: string | null;
  failed_pages: number[];
  low_confidence_pages: number[];
  warnings: string[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") message = body.detail;
  } catch {
    // non-JSON error body: keep the generic message
  }
  return new ApiError(message, res.status);
}

/** Uploads with XHR (fetch has no upload progress events). */
export function createJob(
  file: File,
  onProgress: (fraction: number) => void,
  signal: AbortSignal,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    signal.addEventListener("abort", () => xhr.abort());
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    const form = new FormData();
    form.append("file", file);
    xhr.open("POST", "/api/jobs");
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status === 202) return resolve(xhr.response.job_id);
      const detail = xhr.response?.detail;
      reject(new ApiError(typeof detail === "string" ? detail : `Upload failed (${xhr.status})`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("Could not reach the server.", 0));
    xhr.send(form);
  });
}

export async function getJob(jobId: string, signal?: AbortSignal): Promise<Job> {
  const res = await fetch(`/api/jobs/${jobId}`, { signal });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

export async function deleteJob(jobId: string): Promise<void> {
  const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw await errorFrom(res);
}

export const downloadUrl = (jobId: string) => `/api/jobs/${jobId}/download`;
