import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as api from "./api.js";
import {
  ConfirmDialog,
  ConflictDialog,
  ErrorDialog,
  ExpiredOverlay,
  IntegrityBanner,
  PromptDialog,
  PurgeProgressDialog,
  SessionsDialog,
  TooSmall,
  UploadFailedDialog,
} from "./Dialogs.jsx";
import Login from "./Login.jsx";
import {
  Details,
  EmptyDirectory,
  FileList,
  Sidebar,
  SearchOverlay,
  StatusBar,
  TransferTray,
  TrashList,
} from "./Panes.jsx";
import {
  baseName,
  clock,
  crumbsOf,
  humanBytes,
  joinPath,
  parentOf,
} from "./format.js";
import { Icon, Spinner } from "./icons.jsx";
import { useTranslate } from "./i18n.js";

// How often the client asks the server what the session's deadlines are.
// The countdown between polls is interpolated from a wall-clock delta, so it
// can run slow (harmless -- it under-reports the time left) but never fast.
// The prototype decremented a counter once per 700ms `setInterval` tick and
// called that a second, which made its clock run 43% fast in the one
// direction that matters.
const SESSION_POLL_MS = 10_000;

const MIN_WIDTH = 1024;
const MIN_HEIGHT = 640;

function measureViewport() {
  return {
    w: window.innerWidth || document.documentElement.clientWidth || 0,
    h: window.innerHeight || document.documentElement.clientHeight || 0,
  };
}

/**
 * Whether to draw the "too small" curtain.
 *
 * A zero is not a small window, it is the absence of a measurement -- which is
 * what a window reports before it has painted. Treating the two the same put
 * the curtain up over a 1280x720 window and left it there, because nothing
 * resizes afterwards to correct it.
 */
function isTooSmall({ w, h }) {
  if (!w || !h) return false;
  return w < MIN_WIDTH || h < MIN_HEIGHT;
}

export default function App() {
  const [lang, setLang] = useState(() => localStorage.getItem("dd.lang") || "zh");
  const t = useTranslate(lang);

  const [phase, setPhase] = useState("checking");
  const [account, setAccount] = useState(null);
  const [syncedAt, setSyncedAt] = useState(0);
  const [, setNowTick] = useState(0);
  const [online, setOnline] = useState(true);

  const [view, setView] = useState("files");
  const [nav, setNav] = useState({ stack: ["/"], index: 0 });
  const cwd = nav.stack[nav.index];

  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [display, setDisplay] = useState(() => localStorage.getItem("dd.display") || "list");

  const [dirCache, setDirCache] = useState({});
  const [expanded, setExpanded] = useState({ "/": true });

  const [trash, setTrash] = useState({ entries: [], retention_seconds: 0 });
  const [trashBusy, setTrashBusy] = useState(null);
  // The purge running on the server, if there is one. Server state that this
  // tab is watching -- not this tab's state -- which is why a reload picks it
  // back up rather than starting a second one.
  const [purgeJob, setPurgeJob] = useState(null);

  const [transfers, setTransfers] = useState([]);
  const [trayOpen, setTrayOpen] = useState(false);
  const [dragDepth, setDragDepth] = useState(0);

  const [dialog, setDialog] = useState(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [toast, setToast] = useState(null);
  const [integrityLog, setIntegrityLog] = useState([]);
  const [integrityBanner, setIntegrityBanner] = useState(null);
  const [failedPaths, setFailedPaths] = useState(() => new Set());
  const [viewport, setViewport] = useState(measureViewport);

  const fileInput = useRef(null);
  const toastTimer = useRef(null);

  useEffect(() => {
    localStorage.setItem("dd.lang", lang);
    // index.html hard-codes zh-Hant, which stops being true the moment the
    // chip is clicked. It is what a screen reader picks a voice from and what
    // the browser picks fonts and hyphenation by, so leaving it wrong makes
    // the English interface read aloud in Chinese.
    document.documentElement.lang = lang === "en" ? "en" : "zh-Hant";
  }, [lang]);
  useEffect(() => localStorage.setItem("dd.display", display), [display]);

  /* ------------------------------------------------------------- errors */

  const notify = useCallback((text) => {
    setToast(text);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3200);
  }, []);

  const handle = useCallback((error) => {
    if (!error || error.name === "AbortError") return;

    // A 401 is the end of the session, never a prompt to sign back in behind
    // the user's back: the password is what unwraps the master key and the
    // browser does not have it.
    if (error instanceof api.ApiError && error.isUnauthorized) {
      setPhase("expired");
      return;
    }

    // Never retried, never downgraded, never partially shown. It means the
    // database or the Discord side was changed without the key.
    if (error instanceof api.ApiError && error.isIntegrity) {
      const path = error.body.path || "";
      setFailedPaths((current) => new Set(current).add(path));
      setIntegrityBanner({
        path,
        detail: error.body.detail || "",
        at: Date.now(),
      });
      return;
    }

    // A write that did not finish. The server says what it managed to take
    // back, and that decides what to say: an upload that cleaned up after
    // itself is worth retrying, one that could not is worth looking at.
    if (error instanceof api.ApiError && error.isUploadFailed) {
      setDialog({ type: "uploadfailed", error });
      return;
    }

    if (!(error instanceof api.ApiError)) {
      // A fetch that never got a response. Not the same as an error the
      // server chose to send, and saying "that did not work" for it would
      // point at the wrong thing entirely.
      setOnline(false);
      return;
    }

    setDialog({ type: "error", error });
  }, []);

  /* ------------------------------------------------------------ session */

  const applySession = useCallback((body) => {
    setOnline(true);
    if (!body.signed_in) {
      setAccount(null);
      setPhase((current) => (current === "ready" ? "expired" : "signedout"));
      return;
    }
    // Restored on every poll, so a page reload with a live cookie comes back
    // signed in rather than pretending the session is gone.
    api.setCsrfToken(body.csrf_token);
    setAccount(body);
    setSyncedAt(Date.now());
    setPhase("ready");
  }, []);

  const refreshSession = useCallback(async () => {
    try {
      applySession(await api.session());
    } catch (error) {
      if (error instanceof api.ApiError && error.isUnauthorized) setPhase("expired");
      else setOnline(false);
    }
  }, [applySession]);

  useEffect(() => {
    refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    if (phase !== "ready") return undefined;
    const poll = setInterval(refreshSession, SESSION_POLL_MS);
    const tick = setInterval(() => setNowTick((n) => n + 1), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [phase, refreshSession]);

  const elapsed = account ? (Date.now() - syncedAt) / 1000 : 0;
  const idleLeft = Math.max(0, (account?.idle_expires_in ?? 0) - elapsed);
  const absoluteLeft = Math.max(0, (account?.absolute_expires_in ?? 0) - elapsed);

  /* ------------------------------------------------------------ listing */

  const loadDir = useCallback(
    async (path) => {
      setLoading(true);
      try {
        const body = await api.listDir(path);
        setEntries(body.entries);
        setDirCache((current) => ({
          ...current,
          [path]: body.entries.filter((e) => e.is_dir).map((e) => e.name),
        }));
        setOnline(true);
      } catch (error) {
        // A directory that is gone leaves the listing where it was rather than
        // showing an empty folder that does not exist. Another connection may
        // have moved it -- this account can be signed in from several places.
        setEntries([]);
        handle(error);
      } finally {
        setLoading(false);
      }
    },
    [handle],
  );

  const loadTrash = useCallback(async () => {
    try {
      setTrash(await api.listTrash());
      setOnline(true);
    } catch (error) {
      handle(error);
    }
  }, [handle]);

  useEffect(() => {
    if (phase !== "ready") return;
    if (view === "files") loadDir(cwd);
    else loadTrash();
  }, [phase, view, cwd, loadDir, loadTrash]);

  const refreshCurrent = useCallback(async () => {
    if (view === "files") await loadDir(cwd);
    else await loadTrash();
    await refreshSession();
  }, [view, cwd, loadDir, loadTrash, refreshSession]);

  /* --------------------------------------------------------- navigation */

  const go = useCallback((path) => {
    setView("files");
    setSelected(new Set());
    setNav((current) => {
      if (current.stack[current.index] === path) return current;
      // Anything ahead of the cursor is dropped, which is what every file
      // manager and browser does: navigating somewhere new from the middle of
      // a history is a new branch, not an insertion.
      const stack = [...current.stack.slice(0, current.index + 1), path];
      return { stack, index: stack.length - 1 };
    });
  }, []);

  const back = useCallback(() => {
    setSelected(new Set());
    setView("files");
    setNav((c) => (c.index > 0 ? { ...c, index: c.index - 1 } : c));
  }, []);

  const forward = useCallback(() => {
    setSelected(new Set());
    setView("files");
    setNav((c) => (c.index < c.stack.length - 1 ? { ...c, index: c.index + 1 } : c));
  }, []);

  const openTrash = useCallback(() => {
    setView("trash");
    setSelected(new Set());
  }, []);

  const toggleDir = useCallback(
    async (path) => {
      const open = !!expanded[path];
      setExpanded((current) => ({ ...current, [path]: !open }));
      if (!open && dirCache[path] === undefined) {
        try {
          const body = await api.listDir(path);
          setDirCache((current) => ({
            ...current,
            [path]: body.entries.filter((e) => e.is_dir).map((e) => e.name),
          }));
        } catch (error) {
          handle(error);
        }
      }
    },
    [expanded, dirCache, handle],
  );

  const tree = useMemo(() => {
    const out = [];
    const walk = (path, depth) => {
      const children = dirCache[path];
      out.push({
        path,
        name: path === "/" ? "/" : baseName(path),
        depth,
        open: !!expanded[path],
        loaded: children !== undefined,
        hasChildren: (children?.length ?? 0) > 0,
      });
      if (expanded[path] && children) {
        [...children]
          .sort((a, b) => a.localeCompare(b))
          .forEach((name) => walk(joinPath(path, name), depth + 1));
      }
    };
    walk("/", 0);
    return out;
  }, [dirCache, expanded]);

  /* ---------------------------------------------------------- selection */

  const selectEntry = useCallback((name, event) => {
    const additive = event && (event.metaKey || event.ctrlKey || event.shiftKey);
    setSelected((current) => {
      if (!additive) return new Set([name]);
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const openEntry = useCallback(
    (entry) => {
      if (entry.is_dir) go(joinPath(cwd, entry.name));
      else startDownload(entry);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cwd, go],
  );

  /* ----------------------------------------------------------- download */

  function startDownload(entry) {
    // A plain link rather than fetch-into-a-blob. A 2 GiB file read into
    // memory to hand to `URL.createObjectURL` is 2 GiB of renderer heap; the
    // link streams it to disk and the browser owns the progress UI. The cost
    // is that the tray cannot show a bar for downloads, which is why it does
    // not pretend to.
    const link = document.createElement("a");
    link.href = api.downloadUrl(joinPath(cwd, entry.name));
    link.download = entry.name;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  /* ------------------------------------------------------------- upload */

  const runUploads = useCallback(
    async (files, targetDir) => {
      setTrayOpen(true);
      for (const file of files) {
        const id = `${Date.now()}-${file.name}-${Math.random()}`;
        const controller = new AbortController();
        const path = joinPath(targetDir, file.name);
        setTransfers((current) => [
          ...current,
          { id, name: file.name, path, sent: 0, total: file.size, state: "running", controller },
        ]);
        try {
          await api.upload(path, file, {
            signal: controller.signal,
            onProgress: (sent, total) =>
              setTransfers((current) =>
                current.map((item) => (item.id === id ? { ...item, sent, total } : item)),
              ),
          });
          setTransfers((current) =>
            current.map((item) =>
              item.id === id ? { ...item, state: "done", sent: item.total } : item,
            ),
          );
        } catch (error) {
          const cancelled = error.name === "AbortError";
          // `upload_failed` is the machine-readable token the endpoint is
          // specified to return, and `message` falls back to it. Printing it
          // in the transfer row would put a wire constant in front of a
          // person; the dialog is where the three numbers get explained.
          const shown =
            error instanceof api.ApiError && error.isUploadFailed
              ? t("upload.failed.short")
              : error.message;
          setTransfers((current) =>
            current.map((item) =>
              item.id === id
                ? {
                    ...item,
                    state: cancelled ? "cancelled" : "failed",
                    error: cancelled ? "" : shown,
                  }
                : item,
            ),
          );
          if (!cancelled) handle(error);
        }
      }
      await refreshCurrent();
    },
    [handle, refreshCurrent, t],
  );

  const askUpload = useCallback(
    (files) => {
      const list = [...files].filter((f) => f.size !== undefined);
      if (!list.length) return;
      const taken = new Set(entries.map((e) => e.name));
      const clashes = list.filter((f) => taken.has(f.name));
      if (clashes.length) {
        // `PUT /api/file` truncates and replaces. Everywhere else in this app
        // a name collision is refused -- `rename` will not clobber and a
        // restore asks -- so an upload that silently replaced would be the one
        // way to lose a file without being asked.
        setDialog({ type: "overwrite", files: list, clashes, dir: cwd });
        return;
      }
      runUploads(list, cwd);
    },
    [entries, cwd, runUploads],
  );

  /* -------------------------------------------------------- mutations */

  const run = useCallback(
    async (action, after) => {
      try {
        const result = await action();
        if (after) after(result);
        await refreshCurrent();
        return result;
      } catch (error) {
        handle(error);
        return null;
      }
    },
    [handle, refreshCurrent],
  );

  /* --------------------------------------------------------- purge jobs */

  /**
   * Watch a purge to its end, keeping the dialog's numbers current.
   *
   * Polling rather than a stream, matching the server: the work outlives the
   * request that started it, so it has to be reachable by id rather than tied
   * to a connection this tab might lose.
   */
  const watchJob = useCallback(
    async (job) => {
      setPurgeJob(job);
      try {
        const final = await api.followJob(job.id, { onProgress: setPurgeJob });
        setPurgeJob(final);
      } catch (error) {
        // The job itself is unaffected by this tab losing sight of it -- it is
        // running on the server. Say so rather than implying it stopped.
        handle(error);
      } finally {
        await refreshCurrent();
      }
    },
    [handle, refreshCurrent],
  );

  const startPurge = useCallback(
    async (begin) => {
      try {
        const { job } = await begin();
        await watchJob(job);
      } catch (error) {
        if (error instanceof api.ApiError && error.isConflict) {
          notify(t("dlg.job.busy"));
          return;
        }
        handle(error);
      }
    },
    [watchJob, handle, notify, t],
  );

  // A reload, or a second tab, finds the purge that is already running. The
  // server keeps finished jobs around for a few minutes, so this deliberately
  // only re-attaches to a live one: re-opening a dialog for something that
  // finished while the tab was closed would be a notification, not progress.
  useEffect(() => {
    if (phase !== "in") return;
    let cancelled = false;
    (async () => {
      try {
        const { jobs } = await api.listJobs();
        const running = (jobs || []).find((job) => job.state === "running");
        if (running && !cancelled) await watchJob(running);
      } catch {
        // Nothing to recover; the drive works without this.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [phase, watchJob]);

  const askDelete = useCallback(() => {
    const items = [...selected]
      .map((name) => entries.find((e) => e.name === name))
      .filter(Boolean);
    if (!items.length) return;
    setDialog({ type: "trash", items });
  }, [selected, entries]);

  /* ------------------------------------------------------------ search */

  const runSearch = useCallback(
    async (needle) => {
      try {
        return await api.search(needle);
      } catch (error) {
        handle(error);
        return { results: [], truncated: false, scanned: 0 };
      }
    },
    [handle],
  );

  const reveal = useCallback(
    (hit) => {
      setSearchOpen(false);
      const dir = hit.is_dir ? hit.path : parentOf(hit.path);
      go(dir);
      // Selected by name once the listing for that directory arrives.
      setTimeout(() => setSelected(new Set([baseName(hit.path)])), 0);
    },
    [go],
  );

  /* --------------------------------------------------------- shortcuts */

  useEffect(() => {
    function onKey(event) {
      if (phase !== "ready") return;
      const typing = ["INPUT", "TEXTAREA"].includes(event.target?.tagName);
      const meta = event.metaKey || event.ctrlKey;

      if (meta && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
        return;
      }
      if (typing || dialog || searchOpen) return;

      if (meta && event.key.toLowerCase() === "a") {
        event.preventDefault();
        setSelected(new Set(entries.map((e) => e.name)));
        return;
      }
      if (event.key === "F5" || (meta && event.key.toLowerCase() === "r")) {
        event.preventDefault();
        refreshCurrent();
        return;
      }
      if (event.key === "Delete" && view === "files" && selected.size) {
        event.preventDefault();
        askDelete();
        return;
      }
      if (event.key === "F2" && selected.size === 1) {
        event.preventDefault();
        const entry = entries.find((e) => e.name === [...selected][0]);
        if (entry) setDialog({ type: "rename", entry });
        return;
      }
      if (event.key === "Enter" && selected.size === 1 && view === "files") {
        const entry = entries.find((e) => e.name === [...selected][0]);
        if (entry) openEntry(entry);
        return;
      }
      if (event.key === "Backspace" && view === "files" && cwd !== "/") {
        event.preventDefault();
        go(parentOf(cwd));
        return;
      }
      if (event.key === "Escape") setSelected(new Set());
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    phase, dialog, searchOpen, entries, selected, view, cwd,
    askDelete, refreshCurrent, openEntry, go,
  ]);

  useEffect(() => {
    const onResize = () => setViewport(measureViewport());
    window.addEventListener("resize", onResize);
    // A ResizeObserver as well as the event, because the first measurement can
    // be 0x0 -- a window that has not painted yet, or a pane the compositor
    // has not started. No `resize` event follows that, so an app that only
    // listened for one would sit behind the "window too small" curtain for
    // ever on the strength of a measurement it never actually took.
    const observer = new ResizeObserver(onResize);
    observer.observe(document.documentElement);
    return () => {
      window.removeEventListener("resize", onResize);
      observer.disconnect();
    };
  }, []);

  /* ------------------------------------------------------------ render */

  if (phase === "checking") {
    return (
      <div className="app" style={{ display: "grid", placeItems: "center" }}>
        <Spinner size={22} />
      </div>
    );
  }

  if (phase === "signedout") {
    return (
      <Login
        t={t}
        lang={lang}
        onLanguage={() => setLang(lang === "zh" ? "en" : "zh")}
        onSignedIn={(body) => {
          setNav({ stack: ["/"], index: 0 });
          setDirCache({});
          setExpanded({ "/": true });
          setSelected(new Set());
          setTransfers([]);
          setIntegrityLog([]);
          setIntegrityBanner(null);
          setFailedPaths(new Set());
          applySession(body);
        }}
      />
    );
  }

  const tooSmall = isTooSmall(viewport);
  const crumbs = crumbsOf(cwd);
  const trashCount = trash.entries.length;
  const totalBytes = entries.reduce((n, e) => n + (e.is_dir ? 0 : e.size), 0);
  const trashBytes = trash.entries.reduce((n, item) => n + (item.size || 0), 0);

  const statusLeft =
    view === "trash"
      ? t("status.trash", { n: trashCount, size: humanBytes(trashBytes) })
      : `${t("status.items", { n: entries.length })} · ${humanBytes(totalBytes)}` +
        (selected.size ? `  ·  ${t("status.selected", { n: selected.size })}` : "");

  return (
    <div className="app">
      <div className="titlebar">
        <div className="title">
          <Icon name="hardDrives" size={14} style={{ color: "var(--color-accent)" }} />
          <span className="name">{t("app.name")}</span>
          <span className="path">{view === "trash" ? t("side.trash") : cwd}</span>
        </div>
        <button
          className="chip"
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          title={t("act.language")}
          // The visible label is the language you are in, which does not say
          // what the control does. The accessible name says both, and keeps
          // the visible word inside itself so voice control can still reach it.
          aria-label={`${t("act.language")}: ${lang === "zh" ? "中文" : "EN"}`}
        >
          <Icon name="translate" size={13} />
          {lang === "zh" ? "中文" : "EN"}
        </button>
      </div>

      <div className="toolbar">
        <div style={{ display: "flex", gap: 2, flex: "none" }}>
          <button className="btn-icon" onClick={back} disabled={nav.index === 0} title={t("nav.back")}>
            <Icon name="arrowLeft" size={15} />
          </button>
          <button
            className="btn-icon"
            onClick={forward}
            disabled={nav.index >= nav.stack.length - 1}
            title={t("nav.forward")}
          >
            <Icon name="arrowRight" size={15} />
          </button>
          <button
            className="btn-icon"
            onClick={() => go(parentOf(cwd))}
            disabled={view === "files" && cwd === "/"}
            title={t("nav.up")}
          >
            <Icon name="arrowUp" size={15} />
          </button>
          <button className="btn-icon" onClick={refreshCurrent} title={t("nav.refresh")}>
            {loading ? <Spinner /> : <Icon name="refresh" size={15} />}
          </button>
        </div>

        <div className="crumbs">
          {view === "trash" ? (
            <span className="crumb current">{t("side.trash")}</span>
          ) : (
            crumbs.map((crumb, i) => (
              <span key={crumb.path} style={{ display: "flex", alignItems: "center", flex: "none" }}>
                <button
                  className={`crumb${i === crumbs.length - 1 ? " current" : ""}`}
                  onClick={() => go(crumb.path)}
                >
                  {crumb.name}
                </button>
                {i === crumbs.length - 1 ? null : (
                  <Icon name="caretRight" size={10} style={{ color: "var(--color-neutral-700)" }} />
                )}
              </span>
            ))
          )}
        </div>

        <div style={{ flex: 1 }} />

        <button className="searchbox" onClick={() => setSearchOpen(true)}>
          <Icon name="search" size={13} />
          <span>{t("nav.search")}</span>
          <span style={{ flex: 1 }} />
          <span className="kbd">Ctrl K</span>
        </button>

        <div className="segmented">
          <button className={display === "list" ? "on" : ""} onClick={() => setDisplay("list")} title={t("nav.list")}>
            <Icon name="list" size={14} />
          </button>
          <button className={display === "grid" ? "on" : ""} onClick={() => setDisplay("grid")} title={t("nav.grid")}>
            <Icon name="grid" size={14} />
          </button>
        </div>

        <button
          className="btn"
          style={{ flex: "none" }}
          disabled={view !== "files"}
          onClick={() => setDialog({ type: "newdir" })}
        >
          <Icon name="folderPlus" size={14} />
          {t("nav.newFolder")}
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: "none" }}
          disabled={view !== "files"}
          onClick={() => fileInput.current?.click()}
        >
          <Icon name="upload" size={14} />
          {t("nav.upload")}
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            askUpload(event.target.files);
            event.target.value = "";
          }}
        />
      </div>

      <div className="body">
        <Sidebar
          t={t}
          cwd={cwd}
          view={view}
          tree={tree}
          expanded={expanded}
          trashCount={trashCount}
          onNavigate={(path, target) => (target === "trash" ? openTrash() : go(path))}
          onToggle={toggleDir}
        />

        <main
          className="main"
          onDragEnter={(event) => {
            if (view !== "files") return;
            event.preventDefault();
            setDragDepth((n) => n + 1);
          }}
          onDragOver={(event) => view === "files" && event.preventDefault()}
          onDragLeave={() => setDragDepth((n) => Math.max(0, n - 1))}
          onDrop={(event) => {
            event.preventDefault();
            setDragDepth(0);
            if (view === "files") askUpload(event.dataTransfer.files);
          }}
        >
          <div className="pane">
            {integrityBanner ? (
              <IntegrityBanner
                t={t}
                event={integrityBanner}
                count={integrityLog.length + 1}
                onAcknowledge={() => {
                  setIntegrityLog((log) => [...log, integrityBanner]);
                  setIntegrityBanner(null);
                }}
              />
            ) : null}

            {!online ? (
              <div className="banner banner-warn">
                <Icon name="cloudSlash" size={17} style={{ color: "var(--warn)", marginTop: 1 }} />
                <div className="text">
                  <div className="head">{t("status.offline")}</div>
                  <div>{t("login.serverDown.body")}</div>
                </div>
                <button className="btn" style={{ height: 26 }} onClick={refreshCurrent}>
                  {t("nav.refresh")}
                </button>
              </div>
            ) : null}

            {view === "trash" ? (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  padding: "9px 14px",
                  borderBottom: "1px solid var(--color-divider)",
                }}
              >
                <span className="hint" style={{ maxWidth: "78ch" }}>
                  {t("trash.hint")}
                  {trash.retention_seconds
                    ? ` · ${t("trash.retention", { n: Math.round(trash.retention_seconds / 86400) })}`
                    : ""}
                </span>
                <button
                  className="btn"
                  style={{ flex: "none", height: 26 }}
                  disabled={!trashCount}
                  onClick={() => setDialog({ type: "empty" })}
                >
                  <Icon name="trash" size={13} />
                  {t("trash.empty")}
                </button>
              </div>
            ) : (
              <div className="rowgrid listhead">
                <div>{t("col.name")}</div>
                <div style={{ textAlign: "right" }}>{t("col.size")}</div>
                <div>{t("col.modified")}</div>
                <div style={{ textAlign: "center" }} title={t("col.integrity")}>
                  <Icon name="shield" size={12} style={{ margin: "0 auto" }} />
                </div>
                <div>{t("col.kind")}</div>
              </div>
            )}

            <div className="listbody">
              {view === "trash" ? (
                <TrashList
                  t={t}
                  lang={lang}
                  items={trash.entries}
                  retentionSeconds={trash.retention_seconds}
                  busyId={trashBusy}
                  onRestore={async (item) => {
                    setTrashBusy(item.id);
                    try {
                      await api.restoreTrash(item.id);
                      notify(t("toast.restored", { path: parentOf(item.original_path) }));
                      await refreshCurrent();
                    } catch (error) {
                      if (error instanceof api.ApiError && error.isConflict) {
                        // The occupant's own numbers, fetched rather than
                        // guessed, so the comparison in the dialog is two
                        // facts instead of one fact and a placeholder.
                        let existing = null;
                        try {
                          existing = await api.stat(item.original_path);
                        } catch {
                          existing = null;
                        }
                        setDialog({ type: "conflict", item, existing });
                      } else {
                        handle(error);
                      }
                    } finally {
                      setTrashBusy(null);
                    }
                  }}
                  onPurge={(item) => setDialog({ type: "purge", item })}
                />
              ) : entries.length === 0 && !loading ? (
                <EmptyDirectory t={t} />
              ) : (
                <FileList
                  t={t}
                  lang={lang}
                  entries={entries}
                  cwd={cwd}
                  display={display}
                  selected={selected}
                  failedPaths={failedPaths}
                  onSelect={selectEntry}
                  onOpen={openEntry}
                  onContextRename={(entry) => {
                    setSelected(new Set([entry.name]));
                    setDialog({ type: "rename", entry });
                  }}
                />
              )}
            </div>

            {dragDepth > 0 ? (
              <div className="dropzone">
                <div>
                  <Icon name="upload" size={30} style={{ color: "var(--color-accent)" }} />
                  <div style={{ fontSize: 13, color: "var(--color-accent-200)" }}>
                    {t("nav.upload")}
                  </div>
                  <div className="mono" style={{ fontSize: 12 }}>
                    {cwd}
                  </div>
                </div>
              </div>
            ) : null}

            {trayOpen && transfers.length ? (
              <TransferTray
                t={t}
                transfers={transfers}
                onCancel={(item) => item.controller?.abort()}
                onClear={() => setTransfers((c) => c.filter((i) => i.state === "running"))}
                onClose={() => setTrayOpen(false)}
              />
            ) : null}
          </div>
        </main>

        {view === "files" ? (
          <Details
            t={t}
            lang={lang}
            cwd={cwd}
            entries={entries}
            selected={selected}
            failedPaths={failedPaths}
            onDownload={startDownload}
            onRename={(entry) => setDialog({ type: "rename", entry })}
            onDelete={askDelete}
          />
        ) : null}
      </div>

      <StatusBar
        t={t}
        left={statusLeft}
        idleLeft={idleLeft}
        absoluteLeft={absoluteLeft}
        connections={account?.connections ?? 1}
        integrityCount={integrityLog.length + (integrityBanner ? 1 : 0)}
        online={online}
        formatClock={clock}
        onSessions={() => setDialog({ type: "sessions" })}
        onLogout={async () => {
          try {
            await api.logout();
          } catch {
            /* the session is over either way */
          }
          setAccount(null);
          setPhase("signedout");
        }}
      />

      {toast ? <div className="toast">{toast}</div> : null}

      {searchOpen ? (
        <SearchOverlay
          t={t}
          lang={lang}
          onClose={() => setSearchOpen(false)}
          onRun={runSearch}
          onReveal={reveal}
        />
      ) : null}

      {dialog ? renderDialog() : null}

      {/* Outside `renderDialog`, because a purge is server state this tab is
          watching rather than something this tab opened. It survives the
          confirm dialog closing, and a reload re-attaches to it. */}
      {purgeJob ? (
        <PurgeProgressDialog
          t={t}
          job={purgeJob}
          onCancel={async () => {
            try {
              const { job } = await api.cancelJob(purgeJob.id);
              setPurgeJob(job);
            } catch (error) {
              handle(error);
            }
          }}
          onClose={() => setPurgeJob(null)}
        />
      ) : null}

      {phase === "expired" ? (
        <ExpiredOverlay t={t} onSignIn={() => setPhase("signedout")} />
      ) : null}

      {tooSmall ? <TooSmall t={t} width={viewport.w} height={viewport.h} /> : null}
    </div>
  );

  function renderDialog() {
    const close = () => setDialog(null);

    if (dialog.type === "error") {
      return <ErrorDialog t={t} error={dialog.error} onClose={close} />;
    }

    if (dialog.type === "uploadfailed") {
      return <UploadFailedDialog t={t} error={dialog.error} onClose={close} />;
    }

    if (dialog.type === "sessions") {
      return (
        <SessionsDialog
          t={t}
          connections={account?.connections ?? 1}
          onClose={close}
          onRevoke={async () => {
            const result = await run(() => api.revokeOtherSessions());
            if (result) notify(t("dlg.sessions.revoked", { n: result.signed_out }));
            close();
          }}
        />
      );
    }

    if (dialog.type === "newdir") {
      return (
        <PromptDialog
          t={t}
          title={t("nav.newFolder")}
          label={t("dlg.newFolder")}
          initial=""
          confirmLabel={t("dlg.create")}
          onClose={close}
          onSubmit={async (name) => {
            await run(
              () => api.makeDir(joinPath(cwd, name)),
              () => notify(t("toast.created", { name })),
            );
            close();
          }}
        />
      );
    }

    if (dialog.type === "rename") {
      const entry = dialog.entry;
      return (
        <PromptDialog
          t={t}
          title={t("dlg.rename")}
          label={t("dlg.renameTo")}
          note={t("dlg.renameNote")}
          initial={entry.name}
          confirmLabel={t("dlg.confirmRename")}
          onClose={close}
          onSubmit={async (name) => {
            if (name === entry.name) {
              close();
              return;
            }
            await run(
              () => api.rename(joinPath(cwd, entry.name), joinPath(cwd, name)),
              () => {
                notify(t("toast.renamed", { name }));
                setSelected(new Set([name]));
              },
            );
            close();
          }}
        />
      );
    }

    if (dialog.type === "overwrite") {
      return (
        <ConfirmDialog
          t={t}
          title={t("nav.upload")}
          sub={dialog.clashes.map((f) => f.name).join(", ")}
          facts={[
            { label: t("dlg.purge.count"), value: String(dialog.files.length) },
            { label: t("error.conflict"), value: String(dialog.clashes.length) },
          ]}
          note={t("dlg.trash.note")}
          confirmLabel={t("nav.upload")}
          onClose={close}
          onConfirm={async () => {
            close();
            await runUploads(dialog.files, dialog.dir);
          }}
        />
      );
    }

    if (dialog.type === "trash") {
      const items = dialog.items;
      const dirs = items.filter((i) => i.is_dir);
      return (
        <ConfirmDialog
          t={t}
          title={t("dlg.trash.title")}
          sub={
            items.length === 1
              ? t("dlg.trash.one", { name: items[0].name })
              : t("dlg.trash.many", { n: items.length })
          }
          facts={[
            {
              label: t("detail.totalSize"),
              value: humanBytes(items.reduce((n, i) => n + (i.is_dir ? 0 : i.size), 0)),
            },
          ]}
          note={dirs.length ? t("dlg.trash.recursive") : t("dlg.trash.note")}
          noteTone={dirs.length ? "warn" : "plain"}
          confirmLabel={t("dlg.trash.go")}
          onClose={close}
          onConfirm={async () => {
            close();
            await run(
              async () => {
                // Every selected item, not just the first. The prototype's
                // confirm path only ever looked at `selected[0]`, so a
                // multi-selection containing a non-empty folder skipped the
                // one warning that mattered.
                for (const item of items) {
                  const path = joinPath(cwd, item.name);
                  if (item.is_dir) await api.removeDir(path, { recursive: true });
                  else await api.removeFile(path);
                }
              },
              () => {
                setSelected(new Set());
                notify(t("toast.trashed"));
              },
            );
          }}
        />
      );
    }

    if (dialog.type === "purge") {
      const item = dialog.item;
      return (
        <ConfirmDialog
          t={t}
          danger
          title={t("dlg.purge.title", { name: item.name })}
          sub={t("dlg.purge.sub")}
          facts={[{ label: t("detail.size"), value: humanBytes(item.size) }]}
          note={t("dlg.purge.note")}
          confirmLabel={t("dlg.purge.go")}
          onClose={close}
          onConfirm={async () => {
            close();
            await startPurge(() => api.purgeTrash(item.id));
          }}
        />
      );
    }

    if (dialog.type === "empty") {
      return (
        <ConfirmDialog
          t={t}
          danger
          title={t("dlg.purge.emptyTitle")}
          sub={t("dlg.purge.sub")}
          facts={[
            { label: t("dlg.purge.count"), value: String(trash.entries.length) },
            { label: t("detail.totalSize"), value: humanBytes(trashBytes) },
          ]}
          note={t("dlg.purge.note")}
          confirmLabel={t("dlg.purge.go")}
          onClose={close}
          onConfirm={async () => {
            close();
            await startPurge(() => api.emptyTrash());
          }}
        />
      );
    }

    if (dialog.type === "conflict") {
      return (
        <ConflictDialog
          t={t}
          item={dialog.item}
          existing={dialog.existing}
          incoming={dialog.item}
          onClose={close}
          onChoose={async (strategy) => {
            close();
            if (strategy === "skip") {
              notify(t("toast.skipped"));
              return;
            }
            await run(
              () => api.restoreTrash(dialog.item.id, strategy),
              () => notify(t("toast.restored", { path: parentOf(dialog.item.original_path) })),
            );
          }}
        />
      );
    }

    return null;
  }
}
