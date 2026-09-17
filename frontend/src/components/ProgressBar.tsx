interface Props {
  /** 0..1, or null for an indeterminate bar */
  value: number | null;
  label: string;
}

export default function ProgressBar({ value, label }: Props) {
  return (
    <div
      className="relative h-2 overflow-hidden rounded-full bg-cream-2"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value === null ? undefined : Math.round(value * 100)}
    >
      <div
        className={`h-full rounded-full bg-teal transition-[width] duration-500 ease-out ${value === null ? "w-1/4 animate-slide" : ""}`}
        style={value === null ? undefined : { width: `${Math.max(value * 100, 1.5)}%` }}
      />
    </div>
  );
}
