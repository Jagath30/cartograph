import { LINE_WIDTH, POINTS, POINT_RADIUS, VIEW } from "@/lib/frame/glyph";

/** The glyph, in the colour of the text around it. Decorative: the
 *  wordmark beside it says what it is. */
export function Glyph({ size = 22 }: { size?: number }) {
  const line = POINTS.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x} ${y}`).join(" ");
  return (
    <svg width={size} height={size} viewBox={`0 0 ${VIEW} ${VIEW}`} aria-hidden="true" focusable="false">
      <path d={line} fill="none" stroke="currentColor" strokeWidth={LINE_WIDTH} strokeLinejoin="round" />
      {POINTS.map(([x, y]) => (
        <circle key={`${x},${y}`} cx={x} cy={y} r={POINT_RADIUS} fill="currentColor" />
      ))}
    </svg>
  );
}
