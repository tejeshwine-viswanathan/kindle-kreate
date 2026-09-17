import type { Job, JobKind } from "./api";

export interface Conversion {
  kind: JobKind;
  from: "PDF" | "EPUB";
  to: "PDF" | "EPUB";
  description: string;
  /** value for <input accept> */
  accept: string;
  extension: string;
  processingLabel: (job: Job) => string;
}

export const PDF_TO_EPUB: Conversion = {
  kind: "pdf_to_epub",
  from: "PDF",
  to: "EPUB",
  description: "Turn a PDF into a clean, reflowable e-book for your Kindle. Scanned pages get real text.",
  accept: "application/pdf,.pdf",
  extension: ".pdf",
  processingLabel: (job) => `Reading pages ${job.pages_done} of ${job.pages_total}`,
};

export const EPUB_TO_PDF: Conversion = {
  kind: "epub_to_pdf",
  from: "EPUB",
  to: "PDF",
  description: "Lay out an e-book as a tidy PDF with searchable text and a clickable outline.",
  accept: "application/epub+zip,.epub",
  extension: ".epub",
  processingLabel: (job) =>
    job.pages_total ? `Rendering pages ${job.pages_done} of ${job.pages_total}` : "Laying out pages",
};
