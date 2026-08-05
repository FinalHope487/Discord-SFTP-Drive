// Formatting, and the path arithmetic the whole app agrees on.

export const MIB = 1024 * 1024;
// The server splits at 9 MiB. Shown as a derived count in the details panel
// and nowhere else -- it is arithmetic on a size the server reported, not a
// number the client has any other way of knowing.
export const CHUNK = 9 * MIB;

export function humanBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units[i]}`;
}

export function exactBytes(bytes) {
  const n = Number(bytes) || 0;
  return `${humanBytes(n)} (${n.toLocaleString("en-US")} B)`;
}

export function chunksOf(bytes) {
  const n = Number(bytes) || 0;
  // A zero-length file still occupies a node, but no chunk and no attachment.
  // Reporting 1 would overstate what is on Discord by exactly one message.
  return n === 0 ? 0 : Math.ceil(n / CHUNK);
}

// The server sends unix seconds. Multiplying is the whole conversion, and
// getting it wrong is silent: a value in seconds read as milliseconds lands in
// January 1970 and looks like a corrupt timestamp rather than a unit bug.
export function stamp(seconds) {
  if (!seconds) return "—";
  const date = new Date(Number(seconds) * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

export function relativeDays(seconds, now = Date.now() / 1000) {
  return Math.ceil((Number(seconds) - now) / 86400);
}

export function clock(totalSeconds) {
  const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const pad = (n) => String(n).padStart(2, "0");
  const hours = Math.floor(s / 3600);
  const rest = s % 3600;
  const body = `${pad(Math.floor(rest / 60))}:${pad(rest % 60)}`;
  return hours ? `${pad(hours)}:${body}` : body;
}

export function octal(permissions) {
  const n = Number(permissions) || 0;
  return "0" + n.toString(8).padStart(3, "0");
}

// ------------------------------------------------------------------- paths

export function joinPath(dir, name) {
  const base = dir === "/" ? "" : dir.replace(/\/+$/, "");
  return `${base}/${name}`;
}

export function parentOf(path) {
  if (!path || path === "/") return "/";
  const cut = path.lastIndexOf("/");
  return cut <= 0 ? "/" : path.slice(0, cut);
}

export function baseName(path) {
  if (!path || path === "/") return "/";
  return path.slice(path.lastIndexOf("/") + 1);
}

export function crumbsOf(path) {
  const parts = path === "/" ? [] : path.slice(1).split("/");
  return [{ name: "/", path: "/" }].concat(
    parts.map((name, i) => ({
      name,
      path: "/" + parts.slice(0, i + 1).join("/"),
    })),
  );
}

/**
 * `name (2).ext`, matching what the server's `keep_both` produces.
 *
 * The client only ever shows this as a preview of what the button will do;
 * the name that actually lands is whatever the server picked. If the two ever
 * disagree the server wins, and the label is the thing that was wrong.
 */
export function suffixed(name) {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? `${name.slice(0, dot)} (2)${name.slice(dot)}` : `${name} (2)`;
}

// -------------------------------------------------------------------- kinds

const KINDS = {
  md: ["Markdown", "Markdown"],
  txt: ["文字", "Text"],
  pdf: ["PDF", "PDF"],
  gz: ["封存檔", "Archive"],
  tgz: ["封存檔", "Archive"],
  zip: ["封存檔", "Archive"],
  rar: ["封存檔", "Archive"],
  "7z": ["封存檔", "Archive"],
  xlsx: ["試算表", "Spreadsheet"],
  xls: ["試算表", "Spreadsheet"],
  csv: ["資料表", "Data"],
  json: ["資料", "Data"],
  png: ["圖片", "Image"],
  jpg: ["圖片", "Image"],
  jpeg: ["圖片", "Image"],
  gif: ["圖片", "Image"],
  webp: ["圖片", "Image"],
  svg: ["圖片", "Image"],
  mp4: ["影片", "Video"],
  mov: ["影片", "Video"],
  mkv: ["影片", "Video"],
  mp3: ["音訊", "Audio"],
  flac: ["音訊", "Audio"],
  wav: ["音訊", "Audio"],
  log: ["紀錄", "Log"],
  sql: ["傾印", "Dump"],
  bin: ["二進位", "Binary"],
  iso: ["磁碟映像", "Disc image"],
};

export function extensionOf(name) {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

export function kindOf(entry, lang) {
  if (entry.is_dir) return lang === "en" ? "Folder" : "資料夾";
  const pair = KINDS[extensionOf(entry.name || "")];
  if (!pair) return lang === "en" ? "File" : "檔案";
  return lang === "en" ? pair[1] : pair[0];
}

export function iconFor(entry) {
  if (entry.is_dir) return "folder";
  const ext = extensionOf(entry.name || "");
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "image";
  if (["mp4", "mov", "mkv", "avi"].includes(ext)) return "video";
  if (["mp3", "flac", "wav", "ogg"].includes(ext)) return "audio";
  if (["zip", "gz", "tgz", "rar", "7z", "iso"].includes(ext)) return "archive";
  if (["md", "txt", "log", "json", "csv"].includes(ext)) return "text";
  if (ext === "pdf") return "pdf";
  if (["xlsx", "xls"].includes(ext)) return "sheet";
  return "file";
}
