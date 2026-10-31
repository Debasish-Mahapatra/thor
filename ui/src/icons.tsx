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

export const IconAgent = () => (
  <svg {...base}>
    <path d="M8 1.8l1.5 4.7 4.7 1.5-4.7 1.5L8 14.2 6.5 9.5 1.8 8l4.7-1.5z" />
  </svg>
);

export const IconBack = () => (
  <svg {...base} width={14} height={14}>
    <path d="M10 3.5L5.5 8l4.5 4.5" />
  </svg>
);

export const IconEditorPane = () => (
  <svg {...base}>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="M4.5 6h5M4.5 8h7M4.5 10h4" />
  </svg>
);

export const IconPdfPane = () => (
  <svg {...base}>
    <path d="M4 2h5.5L12.5 5v9H4z" />
    <path d="M9.5 2v3h3" />
  </svg>
);

export const IconDockBottom = () => (
  <svg {...base}>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="M2 9.5h12" />
  </svg>
);

export const IconDockRight = () => (
  <svg {...base}>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="M10 3v10" />
  </svg>
);

export const IconClose = () => (
  <svg {...base} width={14} height={14}>
    <path d="M4 4l8 8M12 4l-8 8" />
  </svg>
);
