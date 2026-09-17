/** Loose, single-stroke margin drawings. Decorative only; hidden below the lg breakpoint. */

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2.2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function CoffeeDoodle({ steaming }: { steaming: boolean }) {
  return (
    <svg viewBox="0 0 160 160" className="h-full w-full" aria-hidden {...stroke}>
      {/* steam */}
      {[62, 80, 98].map((x, i) => (
        <path key={x} d={`M${x} ${i === 1 ? 54 : 58}c-6-8 6-12 0-20`} opacity={steaming ? 1 : 0.45}>
          {steaming && (
            <>
              <animate attributeName="opacity" values="0.2;1;0.2" dur="2.4s" begin={`${i * 0.4}s`} repeatCount="indefinite" />
              <animateTransform
                attributeName="transform"
                type="translate"
                values="0 0;0 -6;0 0"
                dur="2.4s"
                begin={`${i * 0.4}s`}
                repeatCount="indefinite"
              />
            </>
          )}
        </path>
      ))}
      {/* cup */}
      <path d="M44 72h76l-6 44c-1 7-7 12-14 12H64c-7 0-13-5-14-12z" />
      <path d="M120 80c14-2 22 6 20 16-2 9-10 13-20 12" />
      <path d="M52 90c2 12 6 24 12 30" strokeOpacity="0.5" />
      {/* saucer */}
      <path d="M34 136c10 6 82 6 92 0" />
      {/* a bean */}
      <ellipse cx="32" cy="118" rx="7" ry="10" transform="rotate(-30 32 118)" />
      <path d="M28 111c4 4 4 10 2 16" transform="rotate(-30 32 118)" />
    </svg>
  );
}

export function BooksDoodle() {
  return (
    <svg viewBox="0 0 200 180" className="h-full w-full" aria-hidden {...stroke}>
      {/* stack of three books, slightly askew */}
      <path d="M30 152h104v-20H30z" />
      <path d="M38 132h100v-20H38z" transform="rotate(-2 88 122)" />
      <path d="M32 110h92v-20H32z" transform="rotate(1.5 78 100)" />
      <path d="M44 142h70M52 122h64M44 100h56" strokeOpacity="0.45" />
      {/* bookmark hanging out of the middle book */}
      <path d="M96 112v24l4-5 4 5v-24" transform="rotate(-2 88 122)" />
      {/* glasses resting on top */}
      <path d="M52 78a8 8 0 1 0 16 0a8 8 0 1 0-16 0M80 78a8 8 0 1 0 16 0a8 8 0 1 0-16 0M68 78h12M52 76l-8-4M96 76l8-4" />
      {/* desk lamp */}
      <path d="M150 152h36" />
      <path d="M168 152v-40" />
      <path d="M168 112l-14-30" />
      <path d="M144 84l20-8 14 20-20 8z" />
      <path d="M150 76l14 20" strokeOpacity="0.4" />
      {/* light cone */}
      <path d="M160 100l-30 52M172 96l16 56" strokeOpacity="0.2" strokeDasharray="3 6" />
    </svg>
  );
}

export function CupIcon({ className, spinning }: { className?: string; spinning?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" className={className} aria-hidden {...stroke} strokeWidth={2}>
      <path d="M9 3c-1 1.5 1 2 0 3.5M13 3c-1 1.5 1 2 0 3.5">
        {spinning && <animate attributeName="opacity" values="0.2;1;0.2" dur="1.6s" repeatCount="indefinite" />}
      </path>
      <path d="M5 10h12v6a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" />
      <path d="M17 12h1.5a2.5 2.5 0 0 1 0 5H17" />
    </svg>
  );
}

export function RibbonIcon() {
  return (
    <svg viewBox="0 0 16 28" width="14" height="24" aria-hidden>
      <path d="M1 0h14v26l-7-6-7 6z" fill="currentColor" />
    </svg>
  );
}

export function HappyReading() {
  return (
    <span
      role="status"
      className="inline-flex items-center gap-2 rounded-full bg-butter-soft px-3 py-1 text-sm font-bold text-coffee animate-drop"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden {...stroke} strokeWidth={2}>
        <path d="M3 6c3-1.5 6-1.5 9 0v13c-3-1.5-6-1.5-9 0zM21 6c-3-1.5-6-1.5-9 0v13c3-1.5 6-1.5 9 0z" />
        <path d="M12 6v13" strokeOpacity="0.4" />
        <path d="M12 6c-2.5-1.2-5-1.4-7.5-.6v12c2.5-.8 5-.6 7.5.6z" fill="currentColor" fillOpacity="0.25" stroke="none">
          <animate
            attributeName="d"
            dur="0.9s"
            begin="0.3s"
            fill="freeze"
            values="M12 6c-2.5-1.2-5-1.4-7.5-.6v12c2.5-.8 5-.6 7.5.6z;M12 6c-1-2-2-3-3-3v13c1 0 2 1 3 3z;M12 6c2.5-1.2 5-1.4 7.5-.6v12c-2.5-.8-5-.6-7.5.6z"
          />
        </path>
      </svg>
      Happy reading!
    </span>
  );
}
