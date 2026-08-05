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
        <button className="btn" onClick={onClose}>
          {t("dlg.cancel")}
        </button>
        <button className={danger ? "btn btn-danger" : "btn btn-primary"} onClick={go} disabled={busy}>
          {busy ? <Spinner /> : null}
          {confirmLabel}
        </button>
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
