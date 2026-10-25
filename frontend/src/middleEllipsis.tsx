import { useLayoutEffect, useRef, useState } from "react";

const KEEP = 10;

/**
 * Shorten a name in the middle, keeping at least the first and last 10 characters
 * (e.g. "2019-05-12…_Shoot_Ana"), so shoots stay recognisable at both ends.
 */
export function middleEllipsis(name: string, maxChars: number): string {
  const budget = Math.max(maxChars, KEEP * 2 + 1);
  if (name.length <= budget) return name;
  const tail = Math.max(KEEP, Math.floor((budget - 1) / 2));
  const head = Math.max(KEEP, budget - 1 - tail);
  return `${name.slice(0, head)}…${name.slice(name.length - tail)}`;
}

/** Full name when it fits in `lines` lines of the element, else middle-ellipsis; full name in the tooltip. */
export function MiddleEllipsis({
  text,
  lines = 2,
  className,
  charWidthEm = 0.58,
}: {
  text: string;
  lines?: number;
  className?: string;
  charWidthEm?: number;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const [maxChars, setMaxChars] = useState<number | null>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const width = element.clientWidth;
      if (!width) return;
      const fontSize = parseFloat(getComputedStyle(element).fontSize) || 13;
      const perLine = Math.max(1, Math.floor(width / (fontSize * charWidthEm)));
      setMaxChars(perLine * lines);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [lines, charWidthEm]);

  const shown = maxChars == null ? text : middleEllipsis(text, maxChars);
  return (
    <span ref={ref} className={className} title={text} data-full={text}>
      {shown}
    </span>
  );
}
