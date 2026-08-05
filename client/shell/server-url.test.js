// node --test server-url.test.js
//
// The shell's only piece of logic with a security consequence: what the setup
// field is allowed to turn into. Whatever comes out of here is the origin the
// main window loads and the origin its navigation lock compares against, so a
// value that slips through wrong widens both at once.

const test = require("node:test");
const assert = require("node:assert/strict");

const { normaliseServerUrl } = require("./server-url.js");

test("bare host and port get http", () => {
  assert.equal(normaliseServerUrl("127.0.0.1:8080"), "http://127.0.0.1:8080");
  // The most common thing anybody types. An earlier version rejected it,
  // because it looked for a colon to spot a scheme and `localhost:` has one.
  assert.equal(normaliseServerUrl("localhost:8080"), "http://localhost:8080");
  assert.equal(normaliseServerUrl("drive.local"), "http://drive.local");
});

test("https survives", () => {
  assert.equal(normaliseServerUrl("https://drive.example.com"), "https://drive.example.com");
  assert.equal(
    normaliseServerUrl("https://drive.example.com:8443"),
    "https://drive.example.com:8443",
  );
});

test("only the origin is kept", () => {
  // A path typed here would be carried into the navigation lock's comparison,
  // which compares origins -- so keeping it would be storing something the
  // check cannot use, and loading a URL nobody asked for.
  assert.equal(normaliseServerUrl("http://a.b:8080/files?x=1#y"), "http://a.b:8080");
  assert.equal(normaliseServerUrl("a.b:8080/files"), "http://a.b:8080");
});

test("whitespace is trimmed", () => {
  assert.equal(normaliseServerUrl("  127.0.0.1:8080\n"), "http://127.0.0.1:8080");
});

test("a scheme that is not http(s) is refused, not rewritten", () => {
  // The bug this pins: `file:///etc/passwd` used to have `http://` glued on
  // the front and come back as an origin whose host was the word "file".
  for (const input of ["file:///etc/passwd", "ftp://host/x", "ws://host"]) {
    assert.throws(() => normaliseServerUrl(input), /scheme/, input);
  }
});

test("nonsense is refused", () => {
  for (const input of ["", "   ", null, undefined]) {
    assert.throws(() => normaliseServerUrl(input), /empty/);
  }
  for (const input of ["javascript:alert(1)", "http://", ":::"]) {
    assert.throws(() => normaliseServerUrl(input), /scheme|url/, String(input));
  }
});
