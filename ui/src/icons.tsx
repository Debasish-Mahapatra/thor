// Small line icons drawn in the current text colour.

const base = {
  width: 16,
  height: 16,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export const IconPlay = () => (
  <svg {...base} width={12} height={12} viewBox="0 0 12 12" stroke="none" fill="currentColor">
    <path d="M2.5 1.5v9l8-4.5z" />
  </svg>
);

export const IconMinus = () => (
  <svg {...base}>
    <path d="M3.5 8h9" />
  </svg>
);

export const IconPlus = () => (
  <svg {...base}>
    <path d="M3.5 8h9M8 3.5v9" />
  </svg>
);

export const IconFitWidth = () => (
  <svg {...base}>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="M5 8h6M5 8l1.5-1.5M5 8l1.5 1.5M11 8l-1.5-1.5M11 8l-1.5 1.5" />
  </svg>
);

export const IconSync = () => (
  <svg {...base}>
    <path d="M2.5 8h8M8 5l3 3-3 3" />
    <path d="M13.5 3v10" />
  </svg>
);

export const IconSidebar = () => (
  <svg {...base}>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="M6 3v10" />
  </svg>
);

export const IconGear = () => (
  <svg {...base}>
    <circle cx="8" cy="8" r="2.2" />
    <path d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6M12.6 8h1.6M3.6 3.6l1.1 1.1M11.3 11.3l1.1 1.1M3.6 12.4l1.1-1.1M11.3 4.7l1.1-1.1" />
  </svg>
);

export const IconChevron = ({ up }: { up: boolean }) => (
  <svg {...base} width={14} height={14}>
    <path d={up ? "M4 10l4-4 4 4" : "M4 6l4 4 4-4"} />
  </svg>
);
