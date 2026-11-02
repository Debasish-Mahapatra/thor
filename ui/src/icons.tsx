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

export const IconSideArrow = ({ open }: { open: boolean }) => (
  <svg {...base}>
    <path d="M2.5 3v10" />
    <path d={open ? "M13 8H5.5M8.5 5l-3 3 3 3" : "M5.5 8H13M10 5l3 3-3 3"} />
  </svg>
);

export const IconGear = () => (
  <svg {...base} strokeWidth={1.2}>
    <path d="M7.18 3.17L7.22 1.45L8.78 1.45L8.82 3.17L10.83 4L12.09 2.82L13.18 3.91L12 5.17L12.83 7.18L14.55 7.22L14.55 8.78L12.83 8.82L12 10.83L13.18 12.09L12.09 13.18L10.83 12L8.82 12.83L8.78 14.55L7.22 14.55L7.18 12.83L5.17 12L3.91 13.18L2.82 12.09L4 10.83L3.17 8.82L1.45 8.78L1.45 7.22L3.17 7.18L4 5.17L2.82 3.91L3.91 2.82L5.17 4z" />
    <circle cx="8" cy="8" r="2" />
  </svg>
);

export const IconSearch = () => (
  <svg {...base}>
    <circle cx="7" cy="7" r="4.5" />
    <path d="M10.4 10.4L14 14" />
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
