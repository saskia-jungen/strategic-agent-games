import type { ReactNode } from 'react';

type Variant = 'default' | 'success' | 'warning' | 'danger' | 'accent';

const VARIANTS: Record<Variant, string> = {
  default: 'bg-border-light text-text-muted',
  success: 'bg-success/15 text-success',
  warning: 'bg-warning/15 text-warning',
  danger: 'bg-danger/15 text-danger',
  accent: 'bg-accent/15 text-accent-light',
};

export default function Badge({
  children,
  variant = 'default',
  tooltip,
}: {
  children: ReactNode;
  variant?: Variant;
  tooltip?: string;
}) {
  const badge = (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${VARIANTS[variant]}`}>
      {children}
    </span>
  );

  if (!tooltip) return badge;

  return (
    <span className="relative group inline-flex">
      {badge}
      <span className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2.5 py-1.5 rounded-sm bg-text text-bg text-[11px] font-normal leading-snug whitespace-normal w-max max-w-[240px] opacity-0 group-hover:opacity-100 transition-opacity z-20 shadow-sm">
        {tooltip}
      </span>
    </span>
  );
}
