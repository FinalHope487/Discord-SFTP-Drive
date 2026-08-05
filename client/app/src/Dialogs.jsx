import { useEffect, useRef, useState } from "react";

import { humanBytes, stamp, suffixed, baseName, parentOf } from "./format.js";
import { Icon, Spinner } from "./icons.jsx";

/** Escape closes, and focus is trapped to the dialog while it is open. */
function useDialogKeys(onClose) {
  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
}

function Scrim({ children, onClose, wide }) {
  useDialogKeys(onClose);
  return (
    <div
      className="scrim"
      onMouseDown={(event) => {
        // Only a click that both starts and ends on the backdrop closes it. A
        // drag that began inside the dialog (selecting text in a path) used to
        // count as a backdrop click and threw the dialog away mid-sentence.
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className={wide ? "dialog wide" : "dialog"} onMouseDown={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ prompt */

export function PromptDialog({ t, title, label, note, initial, confirmLabel, onSubmit, onClose }) {
  const [value, setValue] = useState(initial || "");
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const input = ref.current;
    if (!input) return;
    input.focus();
    // Select the stem, not the extension. Renaming `report.pdf` almost never
    // means renaming it to something that is not a PDF, and selecting the lot
    // makes retyping `.pdf` the default outcome.
    const dot = (initial || "").lastIndexOf(".");
    if (dot > 0) input.setSelectionRange(0, dot);
    else input.select();
  }, [initial]);

  async function submit(event) {
    event.preventDefault();
    const name = value.trim();
    if (!name || busy) return;
    setBusy(true);
    try {
      await onSubmit(name);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Scrim onClose={onClose}>
      <form onSubmit={submit} style={{ display: "contents" }}>
        <h2>{title}</h2>
        <div className="field">
          <label>{label}</label>
          <input
            ref={ref}
            className="input"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </div>
        {note ? <div className="hint">{note}</div> : null}
        <div className="actions">
          <button type="button" className="btn" onClick={onClose}>
            {t("dlg.cancel")}
          </button>
          <button type="submit" className="btn btn-primary" disabled={!value.trim() || busy}>
            {busy ? <Spinner /> : null}
            {confirmLabel}
          </button>
        </div>
      </form>
    </Scrim>
  );
}

/* ----------------------------------------------------------------- confirm */

export function ConfirmDialog({
  t,
  title,
  sub,
  facts,
  note,
  noteTone = "warn",
  confirmLabel,
  danger,
  onConfirm,
  onClose,
}) {
  const [busy, setBusy] = useState(false);

  async function go() {
    if (busy) return;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Scrim onClose={onClose}>
      <h2>{title}</h2>
      {sub ? <div className="sub">{sub}</div> : null}
      {facts?.length ? (
        <div className="factbox">
          {facts.map((fact) => [
            <div key={`${fact.label}-k`}>{fact.label}</div>,
            <div key={`${fact.label}-v`}>{fact.value}</div>,
          ])}
        </div>
      ) : null}
      {note ? (
        <div
          style={{
            display: "flex",
            gap: 8,
            fontSize: 11.5,
            lineHeight: 1.5,
            color: noteTone === "warn" ? "var(--warn)" : "var(--color-neutral-500)",
          }}
        >
          <Icon name="warning" size={14} style={{ marginTop: 2 }} />
          <span>{note}</span>
        </div>
      ) : null}
      <div className="actions">
        {/* Explicitly a button. Without a type, HTML defaults to `submit`,
            which is harmless only for as long as this stays outside a
            <form> -- and the day somebody wraps it, "cancel" starts
            submitting. PromptDialog's cancel already says this. */}
        <button type="button" className="btn" onClick={onClose}>
          {t("dlg.cancel")}
        </button>
        <button
          type="button"
          className={danger ? "btn btn-danger" : "btn btn-primary"}
          onClick={go}
          disabled={busy}
        >
          {busy ? <Spinner /> : null}
          {confirmLabel}
        </button>
      </div>
    </Scrim>
  );
}

/* ------------------------------------------------------------ purge progress */

/**
 * A batch purge while it runs, and what it left behind when it stops.
 *
 * The bar is driven by attachments rather than by entries because attachments
 * are what take the time: one Discord round trip each, behind a rate limiter.
 * An entry-based bar sits at 0/1 for the whole of a thousand-file directory.
 *
 * Cancelling is offered, and the wording around it is the careful part. It
 * stops the rest; it does not give back what has already gone. So the count of
 * what is already destroyed stays on screen the entire time -- before the
 * cancel button is pressed, while it drains, and afterwards -- rather than the
 * dialog reporting only the fact that it stopped.
 */
export function PurgeProgressDialog({ t, job, onCancel, onClose }) {
  const [cancelling, setCancelling] = useState(false);

  const total = job.attachments?.total || 0;
  const done = job.attachments?.done || 0;
  // A purge of directories only has no attachments at all, and 0/0 is honestly
  // "nothing to count" rather than 0%. Entries carry the bar in that case.
  const fraction = total > 0
    ? done / total
    : (job.entries?.total ? job.entries.done / job.entries.total : 0);

  const running = job.state === "running";
  const heading = {
    running: t("dlg.job.running"),
    done: t("dlg.job.done"),
    cancelled: t("dlg.job.cancelled"),
    failed: t("dlg.job.failed"),
  }[job.state] || t("dlg.job.running");

  async function requestCancel() {
    if (cancelling) return;
    setCancelling(true);
    try {
      await onCancel();
    } finally {
      setCancelling(false);
    }
  }

  return (
    <Scrim onClose={running ? () => {} : onClose}>
      <h2>{heading}</h2>
      <div className="sub">
        {running && job.current
          ? t("dlg.job.current", { name: job.current })
          : t("dlg.job.sub")}
      </div>

      <div className={job.state === "failed" ? "bar bad" : "bar"}>
        <i style={{ width: `${Math.round(fraction * 100)}%` }} />
      </div>

      <div className="factbox">
        <div>{t("dlg.job.entries")}</div>
        <div>{`${job.entries?.done || 0} / ${job.entries?.total || 0}`}</div>
        <div>{t("dlg.job.attachments")}</div>
        <div>{`${done} / ${total}`}</div>
      </div>

      {/* Never conditional on the outcome. Whatever this number is, those
          attachments are gone -- saying so only after a cancellation would
          read as though finishing normally were the reversible option. */}
      <div style={{ display: "flex", gap: 8, fontSize: 11.5, lineHeight: 1.5,
                    color: "var(--warn)" }}>
        <Icon name="warning" size={14} style={{ marginTop: 2 }} />
        <span>{t("dlg.job.irreversible")}</span>
      </div>

      {job.error ? (
        <div className="sub" style={{ color: "var(--integrity)" }}>{job.error}</div>
      ) : null}

      <div className="actions">
        {running ? (
          <button
            type="button"
            className="btn"
            onClick={requestCancel}
            disabled={cancelling || job.cancel_requested}
          >
            {cancelling || job.cancel_requested ? <Spinner /> : null}
            {job.cancel_requested ? t("dlg.job.stopping") : t("dlg.job.stop")}
          </button>
        ) : (
          <button type="button" className="btn btn-primary" onClick={onClose}>
            {t("dlg.close")}
          </button>
        )}
      </div>
    </Scrim>
  );
}

/* ---------------------------------------------------------------- conflict */

/**
 * Restoring onto a name that is taken.
 *
 * The server refuses by default and answers 409 with both sides' size and
 * time, which is the whole reason this dialog can show a comparison instead of
 * asking people to guess. Replace puts the copy that is already there into the
 * trash rather than destroying it -- having a trash and still losing something
 * for ever makes no sense.
 */
export function ConflictDialog({ t, item, existing, incoming, onChoose, onClose }) {
  const [busy, setBusy] = useState(false);
  const name = item?.name || "";
  const target = item?.original_path ? parentOf(item.original_path) : "/";

  async function choose(strategy) {
    if (busy) return;
    setBusy(true);
    try {
      await onChoose(strategy);
    } finally {
      setBusy(false);
    }
  }

  const choices = [
    {
      key: "replace",
      icon: "refresh",
      title: t("dlg.conflict.replace"),
      note: t("dlg.conflict.replaceNote"),
    },
    {
      key: "skip",
      icon: "arrowLeft",
      title: t("dlg.conflict.skip"),
      note: t("dlg.conflict.skipNote"),
    },
    {
      key: "keep_both",
      icon: "plus",
      title: t("dlg.conflict.both"),
      note: t("dlg.conflict.bothNote", { name: suffixed(name) }),
    },
  ];

  return (
    <Scrim onClose={onClose} wide>
      <h2>{t("dlg.conflict.title")}</h2>
      <div className="sub">{t("dlg.conflict.sub", { name, dir: target })}</div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {choices.map((choice) => (
          <button
            key={choice.key}
            className="choice"
            disabled={busy}
            onClick={() => choose(choice.key)}
          >
            <Icon name={choice.icon} size={16} style={{ color: "var(--color-accent)", marginTop: 1 }} />
            <span style={{ minWidth: 0 }}>
              <b>{choice.title}</b>
              <small>{choice.note}</small>
            </span>
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <Side title={t("dlg.conflict.inTrash")} data={incoming} t={t} />
        <Side title={t("dlg.conflict.alreadyThere")} data={existing} t={t} accent />
      </div>

      <div className="actions">
        <button className="btn" onClick={onClose} disabled={busy}>
          {t("dlg.cancel")}
        </button>
      </div>
    </Scrim>
  );
}

function Side({ title, data, t, accent }) {
  return (
    <div
      style={{
        padding: "10px 11px",
        border: `1px solid ${accent ? "var(--color-accent)" : "var(--color-divider)"}`,
        borderRadius: "var(--radius-md)",
        background: accent ? "color-mix(in srgb, var(--color-accent) 10%, transparent)" : "transparent",
      }}
    >
      <div
        style={{
          fontSize: 10,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: accent ? "var(--color-accent-300)" : "var(--color-neutral-600)",
          marginBottom: 6,
        }}
      >
        {title}
      </div>
      <div className="kv" style={{ fontSize: 11 }}>
        <div>{t("detail.size")}</div>
        <div className="mono">{data ? humanBytes(data.size) : "—"}</div>
        <div>{t("detail.modified")}</div>
        <div className="mono">{data ? stamp(data.mtime ?? data.modified_at) : "—"}</div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- sessions */

export function SessionsDialog({ t, connections, onRevoke, onClose }) {
  const [busy, setBusy] = useState(false);
  const alone = connections <= 1;

  return (
    <Scrim onClose={onClose}>
      <h2>{t("dlg.sessions.title", { n: connections })}</h2>
      <div className="sub">{t("dlg.sessions.body")}</div>
      {alone ? (
        <div className="hint">{t("dlg.sessions.alone")}</div>
      ) : (
        <>
          <div className="hint">{t("dlg.sessions.revokeNote")}</div>
          <button
            className="btn btn-danger btn-block"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onRevoke();
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? <Spinner /> : <Icon name="signOut" size={14} />}
            {t("dlg.sessions.revoke")}
          </button>
        </>
      )}
      <div className="actions">
        <button className="btn" onClick={onClose}>
          {t("dlg.cancel")}
        </button>
      </div>
    </Scrim>
  );
}

/* ------------------------------------------------------------------- error */

export function ErrorDialog({ t, error, onClose }) {
  const message =
    error.status === 409
      ? t("error.conflict")
      : error.status === 404
        ? t("error.notFound")
        : error.isRateLimited
          ? t("error.rateLimited")
          : error.message;

  return (
    <Scrim onClose={onClose}>
      <h2>{t("error.title")}</h2>
      <div className="sub">{message}</div>
      {error.status && error.status !== 409 && error.status !== 404 ? (
        <div className="factbox">
          <div>HTTP</div>
          <div>{error.status}</div>
          {error.body?.detail ? (
            <>
              <div>detail</div>
              <div style={{ wordBreak: "break-all" }}>{error.body.detail}</div>
            </>
          ) : null}
        </div>
      ) : null}
      <div className="actions">
        <button className="btn btn-primary" onClick={onClose}>
          {t("error.dismiss")}
        </button>
      </div>
    </Scrim>
  );
}

/* ----------------------------------------------------------- upload failed */

/**
 * A write that did not finish, reported as what it left behind.
 *
 * The three numbers come from the server; none of them is inferred here. The
 * only decision this component makes is which of two things to say, and it
 * makes it on `orphans` alone:
 *
 *   orphans === 0  the unwind reclaimed everything it had uploaded, so the
 *                  file simply is not there and trying again is reasonable.
 *   orphans !== 0  chunks are on Discord that nothing references and nothing
 *                  will collect. Retrying uploads a second copy instead of
 *                  reclaiming the first, so this asks for a person rather
 *                  than offering a button.
 */
export function UploadFailedDialog({ t, error, onClose }) {
  const orphans = error.orphans;
  const stale = error.staleNode;
  const body = error.body || {};

  // Order matters: a stale row is what the user will actually run into, since
  // it is the one that shows up in the listing looking like a working file.
  const summary = stale
    ? t("upload.failed.stale")
    : orphans
      ? t("upload.failed.orphaned")
      : t("upload.failed.reclaimed");

  return (
    <Scrim onClose={onClose}>
      <h2>{t("upload.failed.title")}</h2>
      <div className="sub">{summary}</div>
      <div className="factbox">
        <div>{t("upload.failed.uploaded")}</div>
        <div>{Number(body.chunks_uploaded) || 0}</div>
        <div>{t("upload.failed.released")}</div>
        <div>{Number(body.attachments_released) || 0}</div>
        <div>{t("upload.failed.orphans")}</div>
        <div style={{ color: orphans ? "var(--integrity)" : undefined }}>{orphans}</div>
        {body.detail ? (
          <>
            <div>detail</div>
            <div style={{ wordBreak: "break-all" }}>{body.detail}</div>
          </>
        ) : null}
      </div>
      {orphans || stale ? (
        <div style={{ display: "flex", gap: 8, fontSize: 11.5, lineHeight: 1.5, color: "var(--warn)" }}>
          <Icon name="warning" size={14} style={{ marginTop: 2 }} />
          <span>{stale ? t("upload.failed.staleNote") : t("upload.failed.note")}</span>
        </div>
      ) : null}
      <div className="actions">
        <button className="btn btn-primary" onClick={onClose}>
          {t("error.dismiss")}
        </button>
      </div>
    </Scrim>
  );
}

/* ----------------------------------------------------------------- expired */

/**
 * Shown for any 401, and it does not offer to sign back in silently.
 *
 * There is nothing to retry with: the password is what unwraps the master key,
 * and the browser does not have it. An app that appeared to recover on its own
 * would either be keeping the password somewhere or lying about what happened.
 */
export function ExpiredOverlay({ t, onSignIn }) {
  return (
    <div className="scrim soft" style={{ zIndex: 24 }}>
      <div className="dialog">
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <Icon name="key" size={18} style={{ color: "var(--color-accent)" }} />
          <h2 style={{ margin: 0 }}>{t("expired.title")}</h2>
        </div>
        <div style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--color-neutral-400)" }}>
          {t("expired.body")}
        </div>
        <button className="btn btn-primary btn-block" style={{ height: 32 }} onClick={onSignIn}>
          {t("expired.again")}
        </button>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- integrity */

/**
 * The integrity banner, which cannot be dismissed with a bare close button.
 *
 * The prototype had an "x" on it. Acknowledging is a different act from
 * closing: this event means somebody changed the database or the Discord side
 * without the key, and a banner that can be swatted away leaves no trace that
 * it ever appeared. Acknowledging moves it into a session-scoped list that the
 * status bar keeps a count of.
 */
export function IntegrityBanner({ t, event, count, onAcknowledge }) {
  return (
    <div className="banner banner-integrity">
      <Icon name="shieldWarning" size={17} style={{ color: "var(--integrity)", marginTop: 1 }} />
      <div className="text">
        <div className="head">{t("integrity.title")}</div>
        <div style={{ maxWidth: "80ch" }}>
          {t("integrity.body", { path: event.path || "—" })}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
          <button className="btn btn-danger" style={{ height: 27 }} onClick={onAcknowledge}>
            {t("integrity.ack")}
          </button>
          <span className="hint">{t("integrity.log", { n: count })}</span>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- too small */

export function TooSmall({ t, width, height }) {
  return (
    <div className="scrim solid" style={{ zIndex: 40 }}>
      <div style={{ maxWidth: 400, textAlign: "center", display: "grid", gap: 9, justifyItems: "center" }}>
        <Icon name="warning" size={28} style={{ color: "var(--color-neutral-600)" }} />
        <div style={{ fontFamily: "var(--font-heading)", fontWeight: 500, fontSize: 16 }}>
          {t("tooSmall.title")}
        </div>
        <div style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--color-neutral-500)" }}>
          {t("tooSmall.body")}
        </div>
        <div className="mono hint">
          {width} × {height} · min 1024 × 640
        </div>
      </div>
    </div>
  );
}

export { baseName };
