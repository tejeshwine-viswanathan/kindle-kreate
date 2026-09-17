import Converter from "./components/Converter";
import { EPUB_TO_PDF, PDF_TO_EPUB } from "./formats";

export default function App() {
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-16">
      <div className="mx-auto max-w-xl space-y-12">
        <header className="space-y-2 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">Kindle Kreate</h1>
          <p className="text-slate-600">Convert between PDF and EPUB, entirely on your own machine.</p>
        </header>

        <Converter conversion={PDF_TO_EPUB} />
        <hr className="border-slate-200" />
        <Converter conversion={EPUB_TO_PDF} />
      </div>
    </main>
  );
}
