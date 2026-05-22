import type { ReactNode } from 'react';

interface CardProps {
  children: ReactNode;
  className?: string;
  glow?: boolean;
}

export default function Card({ children, className = '', glow }: CardProps) {
  return (
    <div
      className={`rounded-sm border border-border bg-surface ${
        glow ? 'shadow-[0_0_12px_rgba(108,92,231,0.10)]' : ''
      } ${className}`}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className = '', onClick }: { children: ReactNode; className?: string; onClick?: () => void }) {
  return <div className={`px-3 sm:px-5 py-3 sm:py-4 border-b border-border ${className}`} onClick={onClick}>{children}</div>;
}

export function CardBody({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`px-3 sm:px-5 py-3 sm:py-4 ${className}`}>{children}</div>;
}
