"use client";

import type { ReactNode } from "react";

export default function CollapsibleSection({
  id,
  title,
  meta,
  open,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  meta?: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div id={id} className="section">
      <button type="button" className="section-head" onClick={onToggle}>
        <div className="section-title">
          <span className={`chevron ${open ? "open" : ""}`}>
            <svg
              viewBox="0 0 12 12"
              width="12"
              height="12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <path d="M4 2.5L8 6L4 9.5" />
            </svg>
          </span>
          {title}
        </div>
        {meta && <div className="section-meta">{meta}</div>}
      </button>
      {open && <div className="stack">{children}</div>}
    </div>
  );
}
