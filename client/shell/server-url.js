// Turning what somebody typed into an origin.
//
// Its own module so it can be tested without starting Electron. The first
// version of this lived inline in main.js and was checked by a copy of itself
// pasted into a test, which agreed with the original about `file:///etc/passwd`
// -- both of them turned it into `http://file//etc/passwd`, an origin whose
// host is the word "file".

/**
 * @param {string} input  anything from the setup field
 * @returns {string} the origin, e.g. "http://127.0.0.1:8080"
 * @throws {Error} with `.message` one of "empty" | "scheme" | "url"
 */
function normaliseServerUrl(input) {
  const raw = String(input || "").trim();
  if (!raw) throw new Error("empty");

  // A scheme is a leading word followed by "://". `localhost:8080` is not one
  // -- it is a host and a port -- which is why this tests for the slashes and
  // not just the colon. Getting that wrong rejects the most common thing
  // anybody types.
  const hasScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw);
  if (hasScheme && !/^https?:\/\//i.test(raw)) throw new Error("scheme");

  let url;
  try {
    url = new URL(hasScheme ? raw : `http://${raw}`);
  } catch {
    throw new Error("url");
  }

  // Belt and braces: `javascript:alert(1)` has no "://" so it reaches here as
  // `http://javascript:alert(1)`, which `new URL` already rejects. If some
  // future input slipped past both, this is what still stops it.
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("scheme");
  }
  if (!url.hostname) throw new Error("url");

  // The origin only. A path, a query or a fragment typed here would be carried
  // into the navigation lock's comparison and quietly widen it.
  return url.origin;
}

module.exports = { normaliseServerUrl };
