import { useEffect, useMemo, useRef, useState } from "react";

import {
  chunksOf,
  exactBytes,
  humanBytes,
  iconFor,
  joinPath,
  kindOf,
  octal,
  relativeDays,
  stamp,
} from "./format.js";
import { Icon, Spinner, kindIcon } from "./icons.jsx";

/* ----------------------------------------------------------------- sidebar */

export function Sidebar({ t, cwd, view, tree, expanded, onNavigate, onToggle, trashCount }) {
  return (
    <aside className="sidebar">
      <div className="group">{t("side.myDrive")}</div>
      {tree.map((node) => (
        <button
          key={node.path}
          className={`treeitem${view === "files" && cwd === node.path ? " on" : ""}`}
          style={{ paddingLeft: 6 + node.depth * 13 }}
          onClick={() => onNavigate(node.path)}
        >
          <span
            className="twist"
            role="presentation"
            onClick={(event) => {
              event.stopPropagation();
              if (node.hasChildren || !node.loaded) onToggle(node.path);
            }}
          >
            {node.hasChildren || !node.loaded ? (
              <Icon name={node.open ? "caretDown" : "caretRight"} size={10} />
            ) : null}
          </span>
          <Icon
            name="folder"
            size={14}
            style={{
              color:
                view === "files" && cwd === node.path
                  ? "var(--color-accent)"
                  : "var(--color-neutral-600)",
            }}
          />
          <span className="label">{node.name}</span>
        </button>
      ))}

      <div className="group" style={{ paddingTop: 14 }}>
        {t("side.system")}
      </div>
      <button
        className={`treeitem${view === "trash" ? " on" : ""}`}
        style={{ paddingLeft: 26 }}
        onClick={() => onNavigate(null, "trash")}
      >
        <Icon
          name="trash"
          size={14}
          style={{ color: view === "trash" ? "var(--color-accent)" : "var(--color-neutral-600)" }}
        />
        <span className="label">{t("side.trash")}</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10, color: "var(--color-neutral-600)" }}>
          {trashCount || ""}
        </span>
      </button>
      <div style={{ flex: 1, minHeight: 40 }} />
    </aside>
  );
}

/* -------------------------------------------------------------- file lists */

/**
 * The shield column.
 *
 * Every live row gets the outline, never a tick. Listing verifies *membership*
 * -- who is in this directory -- and not each entry's own tag, so a green
 * check next to a size that has never been checked is the interface asserting
 * something the server did not say. Opening or downloading is what verifies,
 * and that is where a failure turns the shield red.
 */
function ShieldCell({ t, failed }) {
  return (
    <div style={{ textAlign: "center", color: failed ? "var(--integrity)" : "var(--color-neutral-700)" }}>
      <Icon
        name={failed ? "shieldWarning" : "shield"}
        size={12}
        title={failed ? t("integrity.title") : t("detail.unverifiedNote")}
        style={{ margin: "0 auto" }}
      />
    </div>
  );
}

export function FileList({
  t,
  lang,
  entries,
  cwd,
  display,
  selected,
  failedPaths,
  onSelect,
  onOpen,
  onContextRename,
}) {
  if (display === "grid") {
    return (
      <div className="gridwrap">
        {entries.map((entry) => {
          const path = joinPath(cwd, entry.name);
          return (
            <button
              key={entry.name}
              className={`card${selected.has(entry.name) ? " sel" : ""}`}
              onClick={(event) => onSelect(entry.name, event)}
              onDoubleClick={() => onOpen(entry)}
            >
              <Icon
                name={kindIcon(iconFor(entry))}
                size={34}
                style={{
                  color: entry.is_dir ? "var(--color-accent)" : "var(--color-neutral-500)",
                }}
              />
              <span
                className="cardname"
                style={{ color: failedPaths.has(path) ? "var(--integrity)" : undefined }}
              >
                {entry.name}
              </span>
              <span className="mono" style={{ fontSize: 10.5, color: "var(--color-neutral-600)" }}>
                {entry.is_dir ? "—" : humanBytes(entry.size)}
              </span>
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div>
      {entries.map((entry) => {
        const path = joinPath(cwd, entry.name);
        return (
          <div
            key={entry.name}
            className={`rowgrid row${selected.has(entry.name) ? " sel" : ""}`}
            onClick={(event) => onSelect(entry.name, event)}
            onDoubleClick={() => onOpen(entry)}
            onContextMenu={(event) => {
              event.preventDefault();
              onContextRename(entry);
            }}
          >
            <div className="cellname">
              <Icon
                name={kindIcon(iconFor(entry))}
                size={15}
                style={{ color: entry.is_dir ? "var(--color-accent)" : "var(--color-neutral-500)" }}
              />
              <span
                className="text"
                style={{ color: failedPaths.has(path) ? "var(--integrity)" : undefined }}
              >
                {entry.name}
              </span>
            </div>
            <div className="num">{entry.is_dir ? "—" : humanBytes(entry.size)}</div>
            <div className="when">{stamp(entry.modified_at)}</div>
            <ShieldCell t={t} failed={failedPaths.has(path)} />
            <div style={{ minWidth: 0 }}>
              <span className="tag">{kindOf(entry, lang)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function EmptyDirectory({ t }) {
  return (
    <div className="blank">
      <Icon name="folderOpen" size={34} style={{ color: "var(--color-neutral-700)" }} />
      <div
        style={{
          fontFamily: "var(--font-heading)",
          fontWeight: 500,
          fontSize: 15,
          color: "var(--color-neutral-300)",
          marginTop: 14,
        }}
      >
        {t("empty.title")}
      </div>
      <div style={{ fontSize: 12.5, marginTop: 4 }}>{t("empty.hint")}</div>
      <div
        style={{
          maxWidth: 520,
          marginTop: 26,
          padding: "13px 15px",
          textAlign: "left",
          background: "var(--color-surface)",
          borderRadius: "var(--radius-md)",
          boxShadow: "var(--shadow-sm)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 6 }}>
          <Icon name="key" size={14} style={{ color: "var(--color-accent)" }} />
          <span style={{ fontFamily: "var(--font-heading)", fontWeight: 500, fontSize: 12.5 }}>
            {t("empty.explainTitle")}
          </span>
        </div>
        <div style={{ fontSize: 12, lineHeight: 1.6, color: "var(--color-neutral-400)" }}>
          {t("empty.explainBody")}
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- details */

export function Details({ t, lang, cwd, entries, selected, failedPaths, onDownload, onRename, onDelete }) {
  const names = [...selected];
  const one = names.length === 1 ? entries.find((e) => e.name === names[0]) : null;
  const many = names.length > 1;

  const totalBytes = entries.reduce((n, e) => n + (e.is_dir ? 0 : e.size), 0);
  const selectedBytes = names.reduce((n, name) => {
    const entry = entries.find((e) => e.name === name);
    return n + (entry && !entry.is_dir ? entry.size : 0);
  }, 0);

  return (
    <aside className="details">
      <div className="head">
        <Icon name="info" size={14} style={{ color: "var(--color-accent)" }} />
        {t("detail.title")}
      </div>

      {!one && !many ? (
        <div style={{ padding: "20px 14px", textAlign: "center", color: "var(--color-neutral-600)" }}>
          <div style={{ fontSize: 12 }}>{t("detail.none")}</div>
          <div className="mono" style={{ fontSize: 11, marginTop: 6 }}>
            {t("status.items", { n: entries.length })} · {humanBytes(totalBytes)}
          </div>
        </div>
      ) : null}

      {one ? (
        <div style={{ display: "flex", flexDirection: "column", padding: "14px 13px", gap: 14 }}>
          <div style={{ display: "grid", justifyItems: "center", gap: 9 }}>
            <Icon
              name={kindIcon(iconFor(one))}
              size={40}
              style={{ color: one.is_dir ? "var(--color-accent)" : "var(--color-neutral-500)" }}
            />
            <div
              style={{
                fontFamily: "var(--font-heading)",
                fontWeight: 500,
                fontSize: 14,
                textAlign: "center",
                wordBreak: "break-all",
              }}
            >
              {one.name}
            </div>
            <div className="mono hint" style={{ textAlign: "center", wordBreak: "break-all" }}>
              {joinPath(cwd, one.name)}
            </div>
          </div>

          <div style={{ display: "flex", gap: 6 }}>
            <button
              className="btn btn-primary"
              style={{ flex: 1 }}
              disabled={one.is_dir}
              onClick={() => onDownload(one)}
            >
              <Icon name="download" size={13} />
              {t("detail.download")}
            </button>
            <button className="btn" style={{ width: 32, padding: 0 }} title={t("detail.rename")} onClick={() => onRename(one)}>
              <Icon name="pencil" size={13} />
            </button>
            <button className="btn" style={{ width: 32, padding: 0 }} title={t("detail.delete")} onClick={onDelete}>
              <Icon name="trash" size={13} />
            </button>
          </div>

          <div className="kv">
            <div>{t("detail.kind")}</div>
            <div>{kindOf(one, lang)}</div>
            <div>{t("detail.size")}</div>
            <div className="mono">{one.is_dir ? "—" : exactBytes(one.size)}</div>
            <div>{t("detail.chunks")}</div>
            <div className="mono">{one.is_dir ? "—" : chunksOf(one.size)}</div>
            <div>{t("detail.modified")}</div>
            <div className="mono">{stamp(one.modified_at)}</div>
            <div>{t("detail.permissions")}</div>
            <div className="mono">{octal(one.permissions)}</div>
          </div>

          <VerifyNote t={t} failed={failedPaths.has(joinPath(cwd, one.name))} />
        </div>
      ) : null}

      {many ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "18px 14px" }}>
          <div style={{ fontFamily: "var(--font-heading)", fontWeight: 500, fontSize: 13 }}>
            {t("detail.multi", { n: names.length })}
          </div>
          <div className="kv">
            <div>{t("detail.totalSize")}</div>
            <div className="mono">{humanBytes(selectedBytes)}</div>
            <div>{t("detail.chunks")}</div>
            <div className="mono">
              {names.reduce((n, name) => {
                const entry = entries.find((e) => e.name === name);
                return n + (entry && !entry.is_dir ? chunksOf(entry.size) : 0);
              }, 0)}
            </div>
          </div>
          <button className="btn" onClick={onDelete}>
            <Icon name="trash" size={13} />
            {t("detail.moveToTrash")}
          </button>
        </div>
      ) : null}
    </aside>
  );
}

function VerifyNote({ t, failed }) {
  return (
    <div
      style={{
        padding: "9px 10px",
        borderRadius: "var(--radius-md)",
        background: failed
          ? "color-mix(in srgb, var(--integrity) 10%, transparent)"
          : "var(--color-bg)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 4,
          color: failed ? "var(--integrity)" : "var(--color-neutral-400)",
        }}
      >
        <Icon name={failed ? "shieldWarning" : "shield"} size={13} />
        <span style={{ fontFamily: "var(--font-heading)", fontWeight: 500, fontSize: 12 }}>
          {failed ? t("integrity.title") : t("detail.unverified")}
        </span>
      </div>
      <div style={{ fontSize: 11, lineHeight: 1.5, color: "var(--color-neutral-500)" }}>
        {failed ? t("integrity.body", { path: "" }) : t("detail.unverifiedNote")}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- trash */

export function TrashList({ t, lang, items, retentionSeconds, busyId, onRestore, onPurge }) {
  const now = Date.now() / 1000;
  if (!items.length) {
    return (
      <div className="blank">
        <Icon name="trash" size={30} style={{ color: "var(--color-neutral-700)" }} />
        <div style={{ marginTop: 12, fontSize: 13, color: "var(--color-neutral-400)" }}>
          {t("trash.nothing")}
        </div>
      </div>
    );
  }

  return (
    <div>
      {items.map((item) => {
        const due = (item.trashed_at || 0) + (retentionSeconds || 0);
        const days = relativeDays(due, now);
        const label =
          days <= 0
            ? t("trash.expiresDue")
            : days === 1
              ? t("trash.expiresTomorrow")
              : t("trash.expiresIn", { n: days });
        return (
          <div key={item.id} className="rowgrid row" style={{ height: 38 }}>
            <div className="cellname">
              <Icon
                name={kindIcon(iconFor(item))}
                size={15}
                style={{ color: item.is_dir ? "var(--color-accent)" : "var(--color-neutral-500)" }}
              />
              <span className="text">{item.name}</span>
              <span
                className="mono"
                style={{
                  flex: "none",
                  fontSize: 10,
                  padding: "2px 6px",
                  borderRadius: 3,
                  background: "var(--color-neutral-900)",
                  color: "var(--color-neutral-600)",
                  maxWidth: 240,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={item.original_path}
              >
                {item.original_path}
              </span>
            </div>
            <div className="num">{item.is_dir ? "—" : humanBytes(item.size)}</div>
            <div className="when" style={{ color: days <= 1 ? "var(--warn)" : undefined }}>
              {label}
            </div>
            <div />
            <div style={{ display: "flex", gap: 5, justifyContent: "flex-end" }}>
              {busyId === item.id ? (
                <Spinner />
              ) : (
                <>
                  <button className="btn" style={{ width: 26, height: 24, padding: 0 }} title={t("trash.restore")} onClick={() => onRestore(item)}>
                    <Icon name="restore" size={13} />
                  </button>
                  <button className="btn" style={{ width: 26, height: 24, padding: 0 }} title={t("trash.purge")} onClick={() => onPurge(item)}>
                    <Icon name="x" size={13} />
                  </button>
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ search */

export function SearchOverlay({ t, lang, onClose, onRun, onReveal }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Debounced. Every keystroke is a full verified walk of the tree on the
  // server, so firing one per character would turn a four-letter query into
  // four whole-tree scans, three of them thrown away.
  useEffect(() => {
    const needle = query.trim();
    if (!needle) {
      setResult(null);
      return undefined;
    }
    setBusy(true);
    const timer = setTimeout(async () => {
      try {
        setResult(await onRun(needle));
      } finally {
        setBusy(false);
      }
    }, 260);
    return () => {
      clearTimeout(timer);
      setBusy(false);
    };
  }, [query, onRun]);

  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return (
    <div className="scrim" style={{ alignItems: "flex-start", paddingTop: 84 }} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog wide" onMouseDown={(e) => e.stopPropagation()} style={{ gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          {busy ? <Spinner size={16} /> : <Icon name="search" size={16} style={{ color: "var(--color-accent)" }} />}
          <input
            ref={inputRef}
            className="input"
            style={{ border: 0, background: "transparent", height: 26, padding: 0, fontSize: 15 }}
            placeholder={t("search.placeholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="btn-icon" onClick={onClose} title={t("dlg.cancel")}>
            <Icon name="x" size={13} />
          </button>
        </div>

        <div className="hint">{t("search.hint")}</div>

        {result?.truncated ? (
          <div className="alert warn">
            <div className="body">{t("search.truncated", { n: result.results.length })}</div>
          </div>
        ) : null}

        {result ? (
          <div style={{ maxHeight: 320, overflowY: "auto", margin: "0 -4px" }}>
            {result.results.length === 0 ? (
              <div className="hint" style={{ padding: "18px 4px", textAlign: "center" }}>
                {t("search.none")}
              </div>
            ) : (
              result.results.map((hit) => (
                <button
                  key={hit.path}
                  className="choice"
                  style={{ marginBottom: 6, alignItems: "center" }}
                  onClick={() => onReveal(hit)}
                >
                  <Icon
                    name={kindIcon(iconFor({ ...hit, name: hit.name }))}
                    size={16}
                    style={{ color: hit.is_dir ? "var(--color-accent)" : "var(--color-neutral-500)" }}
                  />
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <b style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {hit.name}
                    </b>
                    <small className="mono">{hit.path}</small>
                  </span>
                  <span className="mono hint" style={{ flex: "none" }}>
                    {hit.is_dir ? "—" : humanBytes(hit.size)}
                  </span>
                </button>
              ))
            )}
          </div>
        ) : null}

        {result ? (
          <div className="hint">{t("search.scanned", { n: result.scanned ?? 0 })}</div>
        ) : null}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- transfers */

export function TransferTray({ t, transfers, onCancel, onClear, onClose }) {
  const active = transfers.filter((item) => item.state === "running").length;
  return (
    <div className="tray">
      <div className="head">
        <Icon name="upload" size={13} style={{ color: "var(--color-accent)" }} />
        {t("transfer.title")}
        <span className="mono" style={{ fontSize: 10.5, color: "var(--color-neutral-600)" }}>
          {active}/{transfers.length}
        </span>
        <span style={{ flex: 1 }} />
        <button className="btn-icon" style={{ width: 20, height: 20 }} onClick={onClear} title={t("transfer.clear")}>
          <Icon name="check" size={11} />
        </button>
        <button className="btn-icon" style={{ width: 20, height: 20 }} onClick={onClose}>
          <Icon name="x" size={11} />
        </button>
      </div>
      <div className="items">
        {transfers.map((item) => {
          const pct = item.total ? Math.round((item.sent / item.total) * 100) : 0;
          return (
            <div key={item.id} style={{ display: "flex", flexDirection: "column", gap: 5, padding: "8px 11px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
                <Icon
                  name="upload"
                  size={12}
                  style={{ color: item.state === "failed" ? "var(--integrity)" : "var(--color-accent)" }}
                />
                <span
                  style={{
                    flex: 1,
                    minWidth: 0,
                    fontSize: 12,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={item.path}
                >
                  {item.name}
                </span>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--color-neutral-500)" }}>
                  {item.state === "done"
                    ? t("transfer.done")
                    : item.state === "failed"
                      ? t("transfer.failed")
                      : item.state === "cancelled"
                        ? t("transfer.cancelled")
                        : `${pct}%`}
                </span>
                {item.state === "running" ? (
                  <button className="btn-icon" style={{ width: 18, height: 18 }} onClick={() => onCancel(item)} title={t("transfer.cancel")}>
                    <Icon name="x" size={10} />
                  </button>
                ) : null}
              </div>
              <div className={`bar${item.state === "failed" ? " bad" : ""}`}>
                <i style={{ width: `${item.state === "done" ? 100 : pct}%` }} />
              </div>
              <div className="mono" style={{ display: "flex", gap: 8, fontSize: 10, color: "var(--color-neutral-600)" }}>
                <span>
                  {humanBytes(item.sent)} / {humanBytes(item.total)}
                </span>
                <span style={{ flex: 1 }} />
                {item.error ? <span style={{ color: "var(--integrity)" }}>{item.error}</span> : null}
              </div>
            </div>
          );
        })}
      </div>
      <div className="hint" style={{ padding: "8px 11px", borderTop: "1px solid var(--color-divider)" }}>
        {t("transfer.note")}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- status bar */

export function StatusBar({
  t,
  left,
  idleLeft,
  absoluteLeft,
  connections,
  integrityCount,
  online,
  onSessions,
  onLogout,
  formatClock,
}) {
  return (
    <div className="statusbar">
      <span className="pill">
        <span className={`dot${online ? "" : " bad"}`} />
        {online ? left : t("status.offline")}
      </span>
      <span style={{ flex: 1 }} />

      {integrityCount > 0 ? (
        <span className="pill" style={{ color: "var(--integrity)" }}>
          <Icon name="shieldWarning" size={11} />
          {t("status.integrity", { n: integrityCount })}
        </span>
      ) : (
        <span className="pill" title={t("detail.unverifiedNote")}>
          <Icon name="shield" size={11} />
          {t("status.verified")}
        </span>
      )}

      <button className="pill" onClick={onSessions} title={t("dlg.sessions.body")}>
        <Icon name="users" size={11} />
        {t("status.connections", { n: connections })}
      </button>

      <span className="pill" title={`${t("status.idle")} / ${t("status.absolute")}`}>
        <Icon name="clock" size={11} style={{ color: idleLeft < 120 ? "var(--warn)" : undefined }} />
        <span style={{ color: idleLeft < 120 ? "var(--warn)" : undefined }}>
          {formatClock(idleLeft)}
        </span>
        <span style={{ opacity: 0.5 }}>/ {formatClock(absoluteLeft)}</span>
      </span>

      <button className="pill" onClick={onLogout} title={t("act.logout")}>
        <Icon name="signOut" size={11} />
      </button>
    </div>
  );
}
