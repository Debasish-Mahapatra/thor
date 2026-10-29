import { useId } from "react";

// The display name. "Thor" is a working name; change it here, in backend/thor/paths.py and in shell/Info.plist.
export const APP_NAME = "Thor";

// The bolt in a unit square, the same shape as the app icon (shell/make_icon.py), scaled to 66% and centred.
const BOLT = [
  [0.52, 0.06],
  [0.28, 0.55],
  [0.47, 0.55],
  [0.36, 0.95],
  [0.73, 0.43],
  [0.54, 0.43],
  [0.74, 0.06],
]
  .map(([x, y]) => `${(17 + 66 * x).toFixed(2)},${(17 + 66 * y).toFixed(2)}`)
  .join(" ");

/** The app icon in small: a lightning bolt on a storm-blue square. */
export function BoltLogo({ size = 18 }: { size?: number }) {
  const id = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
      <defs>
        <linearGradient id={`${id}bg`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#283878" />
          <stop offset="1" stopColor="#0e1434" />
        </linearGradient>
        <linearGradient id={`${id}bolt`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffe266" />
          <stop offset="1" stopColor="#ffaa00" />
        </linearGradient>
      </defs>
      <rect width="100" height="100" rx="22" fill={`url(#${id}bg)`} />
      <polygon points={BOLT} fill={`url(#${id}bolt)`} />
    </svg>
  );
}
