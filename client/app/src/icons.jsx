// Inline SVG, drawn here rather than fetched.
//
// The prototype pulled an icon font from unpkg, which meant every icon
// vanished on a machine with no route to the internet. This drive is a normal
// thing to run on a LAN with the outside blocked, so the icons are part of the
// bundle. They are also the only reason the app would have needed a network
// origin other than its own server at all -- so removing them removes the
// whole category.

const STROKE = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

const PATHS = {
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  folderOpen:
    "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1M3 9h18l-2.2 8.2a2 2 0 0 1-1.9 1.8H5a2 2 0 0 1-2-2z",
  file: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5",
  text: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M8.5 13h7M8.5 16.5h4.5",
  pdf: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M8.6 17v-4h1.3a1.2 1.2 0 0 1 0 2.4H8.6M13.4 17v-4h1.1a1.3 1.3 0 0 1 1.3 1.3v1.4a1.3 1.3 0 0 1-1.3 1.3z",
  sheet:
    "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M8 12h8M8 15.5h8M12 12v7",
  image:
    "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM3 16l4.5-4.5 3.5 3.5 3-3L21 17M15.5 8.5h.01",
  video: "M3 6a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1zM15 10l6-3.5v11L15 14z",
  audio: "M9 17V6l10-2v11M9 17a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0zM19 15a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
  archive:
    "M4 7h16v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM3 3h18v4H3zM11 11h2M11 14h2M11 17h2",
  arrowLeft: "M19 12H5M11 6l-6 6 6 6",
  arrowRight: "M5 12h14M13 6l6 6-6 6",
  arrowUp: "M12 19V5M6 11l6-6 6 6",
  caretRight: "M9.5 5.5 16 12l-6.5 6.5",
  caretDown: "M5.5 9.5 12 16l6.5-6.5",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  upload: "M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  download: "M12 4v12M7 11l5 5 5-5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  trash: "M4 7h16M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M10 11v6M14 11v6",
  folderPlus:
    "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10.5v5M9.5 13h5",
  list: "M4 7h16M4 12h16M4 17h16",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  x: "M6 6l12 12M18 6L6 18",
  check: "M5 13l4.5 4.5L19 7",
  shieldCheck: "M12 3l7 3v6c0 4.2-2.9 7.7-7 9-4.1-1.3-7-4.8-7-9V6zM9 12l2.2 2.2L15.5 10",
  shield: "M12 3l7 3v6c0 4.2-2.9 7.7-7 9-4.1-1.3-7-4.8-7-9V6z",
  shieldWarning:
    "M12 3l7 3v6c0 4.2-2.9 7.7-7 9-4.1-1.3-7-4.8-7-9V6zM12 8.5v4M12 15.5h.01",
  key: "M15.5 3a5.5 5.5 0 1 0-4.3 8.9L10 13H8v2H6v2H3v-2.6l7.1-7.1A5.5 5.5 0 0 0 15.5 3zM16.8 6.7h.01",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5.2l3.2 1.9",
  users:
    "M15.5 20v-1.6a3.5 3.5 0 0 0-3.5-3.4H7a3.5 3.5 0 0 0-3.5 3.4V20M9.5 11.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM20.5 20v-1.6a3.5 3.5 0 0 0-2.6-3.3M15.5 4.6a3.5 3.5 0 0 1 0 6.8",
  pencil: "M4 20h4L19.2 8.8a2 2 0 0 0 0-2.8l-1.2-1.2a2 2 0 0 0-2.8 0L4 16z",
  refresh: "M20 11a8 8 0 1 0-1.2 5.2M20 5v6h-6",
  signOut: "M15 17l5-5-5-5M20 12H9M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h6",
  translate:
    "M3 6h10M8 4v2c0 4-2 7-5 8M6.5 10.5c1.4 2.6 3.4 4.2 6 5M12.5 20l4-10 4 10M14 17h5",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5M12 8h.01",
  warning: "M12 3.5 22 20H2zM12 10v4M12 17h.01",
  hardDrives:
    "M4 4h16a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM4 14h16a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1zM6.5 7h.01M6.5 17h.01",
  restore: "M4 12a8 8 0 1 1 2.3 5.6M4 7v5h5",
  plus: "M12 5v14M5 12h14",
  gauge: "M12 20a8 8 0 1 1 8-8M12 12l4.5-3.5M20 20l1-1",
  cloudSlash:
    "M7 18a4 4 0 0 1-.4-8A6 6 0 0 1 17.5 8.4M18 10a4 4 0 0 1 1.6 7.6M3.5 3.5l17 17",
};

export function Icon({ name, size = 16, style, title, ...rest }) {
  const d = PATHS[name] || PATHS.file;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      role={title ? "img" : "presentation"}
      aria-hidden={title ? undefined : "true"}
      style={{ flex: "none", display: "block", ...style }}
      {...rest}
    >
      {title ? <title>{title}</title> : null}
      <path d={d} {...STROKE} />
    </svg>
  );
}

// The mapping `format.iconFor` returns, resolved to the names above.
const BY_KIND = {
  folder: "folder",
  file: "file",
  text: "text",
  pdf: "pdf",
  sheet: "sheet",
  image: "image",
  video: "video",
  audio: "audio",
  archive: "archive",
};

export function kindIcon(kind) {
  return BY_KIND[kind] || "file";
}

export function Spinner({ size = 14 }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true"
         style={{ flex: "none", animation: "dd-spin 900ms linear infinite" }}>
      <circle cx="12" cy="12" r="9" {...STROKE} opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" {...STROKE} />
    </svg>
  );
}
