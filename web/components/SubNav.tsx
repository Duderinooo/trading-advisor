"use client";

import { useEffect, useRef, useState } from "react";

export type NavItem = {
  id: string;
  label: string;
  count?: number;
};

export default function SubNav({
  items,
  onJump,
}: {
  items: NavItem[];
  onJump: (id: string) => void;
}) {
  const [active, setActive] = useState<string>(items[0]?.id ?? "");
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (items.length === 0) return;
    const ids = items.map((i) => i.id);

    const compute = () => {
      rafRef.current = null;
      const top = window.scrollY + 140;
      let cur = ids[0];
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.offsetTop <= top) cur = id;
      }
      setActive((prev) => (prev === cur ? prev : cur));
    };

    const onScroll = () => {
      if (rafRef.current != null) return;
      rafRef.current = requestAnimationFrame(compute);
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    compute();
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [items]);

  return (
    <div className="subnav">
      <div className="subnav-inner">
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`subnav-item ${active === item.id ? "active" : ""}`}
            onClick={() => onJump(item.id)}
          >
            {item.label}
            {item.count != null && (
              <span className="count">{item.count}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
