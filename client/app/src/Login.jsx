import { useEffect, useMemo, useRef, useState } from "react";

import * as api from "./api.js";
import { clock } from "./format.js";
import { Icon, Spinner } from "./icons.jsx";

/**
 * The sign-in screen.
 *
 * Nothing is prefilled. The prototype shipped with `operator` in the username
 * box and twelve bullet characters as the literal value of the password field,
 * which is the kind of placeholder that survives into a release and then
 * teaches people the box is already filled in.
 */
export default function Login({ t, lang, onLanguage, onSignedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ceilings, setCeilings] = useState(null);
  const [idleChoice, setIdleChoice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(null);
  const usernameRef = useRef(null);

  useEffect(() => {
    usernameRef.current?.focus();
  }, []);

  // The ceilings come from the server so the form can offer a shorter session
  // without offering one that would be silently clamped. A hard-coded "10 min
  // (max)" would be a lie the moment somebody changes the env var.
  useEffect(() => {
    let live = true;
    api
      .session()
      .then((body) => live && !body.signed_in && setCeilings(body))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  const options = useMemo(() => {
    const ceiling = ceilings?.max_idle_seconds || 600;
    // Offer only what is actually shorter, plus the ceiling itself. Showing a
    // 10-minute button next to a 5-minute ceiling would offer a session the
    // server will not give.
    const shorter = [120, 300, 600, 1800].filter((s) => s < ceiling);
    return [...shorter, ceiling].map((seconds) => ({
      seconds,
      label: labelFor(seconds, lang) + (seconds === ceiling ? t("login.max") : ""),
      isCeiling: seconds === ceiling,
    }));
  }, [ceilings, lang, t]);

  const chosen = idleChoice ?? options[options.length - 1]?.seconds ?? null;

  async function submit(event) {
    event.preventDefault();
    if (busy || !username || !password) return;
    setBusy(true);
    setFailure(null);
    try {
      const payload = await api.login({
        username,
        password,
        // Only sent when it is genuinely shorter than the ceiling.
        idleSeconds: chosen && !isCeiling(chosen, options) ? chosen : null,
      });
      // Dropped from this component's state the instant it is no longer
      // needed. It is the key-encryption password, not just a login.
      setPassword("");
      onSignedIn(payload);
    } catch (error) {
      setFailure(error);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  const shown = describe(failure, t);

  return (
    <div className="scrim solid" style={{ position: "fixed", zIndex: 30 }}>
      <form className="login" onSubmit={submit}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <Icon name="hardDrives" size={19} style={{ color: "var(--color-accent)" }} />
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontFamily: "var(--font-heading)",
                fontWeight: 500,
                fontSize: 16,
                letterSpacing: "-0.01em",
              }}
            >
              {t("app.name")}
            </div>
            <div className="hint">{t("app.tagline")}</div>
          </div>
          <button
            type="button"
            className="chip"
            onClick={onLanguage}
            title={t("act.language")}
            aria-label={`${t("act.language")}: ${lang === "zh" ? "中文" : "EN"}`}
          >
            <Icon name="translate" size={13} />
            {lang === "zh" ? "中文" : "EN"}
          </button>
        </div>

        <div className="field">
          <label htmlFor="dd-user">{t("login.username")}</label>
          <input
            id="dd-user"
            className="input"
            ref={usernameRef}
            value={username}
            autoComplete="username"
            onChange={(e) => setUsername(e.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="dd-pass">{t("login.password")}</label>
          <input
            id="dd-pass"
            className="input"
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
          />
          <div className="hint">{t("login.passwordNote")}</div>
        </div>

        <div>
          <div style={{ fontSize: 12, color: "var(--color-neutral-400)", marginBottom: 6 }}>
            {t("login.ttl")}
          </div>
          <div className="ttlrow">
            {options.map((option) => (
              <button
                key={option.seconds}
                type="button"
                className={option.seconds === chosen ? "on" : ""}
                onClick={() => setIdleChoice(option.seconds)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="hint" style={{ marginTop: 6 }}>
            {t("login.ttlNote", {
              idle: clock(ceilings?.max_idle_seconds || 600),
              absolute: clock(ceilings?.max_absolute_seconds || 7200),
            })}
          </div>
        </div>

        {shown ? (
          <div className={`alert${shown.warn ? " warn" : ""}`}>
            <div className="head">
              <Icon name={shown.icon} size={14} />
              {shown.title}
            </div>
            <div className="body">{shown.body}</div>
          </div>
        ) : null}

        <button
          type="submit"
          className="btn btn-primary btn-block"
          style={{ height: 34 }}
          disabled={busy || !username || !password}
        >
          {busy ? <Spinner /> : null}
          {busy ? t("login.working") : t("login.submit")}
        </button>
      </form>
    </div>
  );
}

function isCeiling(seconds, options) {
  return options.some((o) => o.seconds === seconds && o.isCeiling);
}

function labelFor(seconds, lang) {
  const minutes = Math.round(seconds / 60);
  if (lang === "en") return `${minutes} min`;
  return `${minutes} 分鐘`;
}

/**
 * Which failure this was, in the words the server's own design calls for.
 *
 * 429 and 503 are different events with different advice and the prototype
 * already had the copy for both: a lockout is keyed on (source + device) and
 * never on the account, and a full queue is the login endpoint refusing rather
 * than growing an unbounded backlog of 64 MiB Argon2 runs.
 */
function describe(error, t) {
  if (!error) return null;
  if (error.status === 401) {
    return {
      icon: "x",
      title: t("login.err.401.title"),
      body: t("login.err.401.body"),
    };
  }
  if (error.status === 429) {
    return {
      icon: "warning",
      title: t("login.err.429.title"),
      body: t("login.err.429.body", { retry: error.retryAfter || 60 }),
    };
  }
  if (error.status === 503) {
    return {
      warn: true,
      icon: "clock",
      title: t("login.err.503.title"),
      body: t("login.err.503.body"),
    };
  }
  if (error.status === 0 || error.name === "TypeError") {
    return {
      warn: true,
      icon: "cloudSlash",
      title: t("login.serverDown.title"),
      body: t("login.serverDown.body"),
    };
  }
  return { icon: "warning", title: t("login.err.other.title"), body: error.message };
}
