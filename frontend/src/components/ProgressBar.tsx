interface Props {
  /** 0..1, or null for an indeterminate bar */
  value: number | null;
  label: string;
}

export default function ProgressBar({ value, label }: Props) {
  return (
    <div
      className="h-1.5 overflow-hidden rounded-full bg-slate-100"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value === null ? undefined : Math.round(value * 100)}
    >
      <div
        className={`h-full rounded-full bg-indigo-600 transition-[width] duration-300 ${value === null ? "w-1/3 animate-pulse" : ""}`}
        style={value === null ? undefined : { width: `${value * 100}%` }}
      />
    </div>
  );
}
