// node --test language.test.js
//
// The preference is read from a file the user can edit and that older builds
// of this app wrote without the key at all, so the interesting inputs here are
// the ones nobody meant to type.

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  LANGUAGES,
  DEFAULT_LANGUAGE,
  normaliseLanguage,
  otherLanguage,
  menuStrings,
} = require("./language.js");

test("the two languages this app has", () => {
  assert.deepEqual(LANGUAGES, ["zh", "en"]);
  assert.ok(LANGUAGES.includes(DEFAULT_LANGUAGE));
});

test("a language survives, whatever case and padding it arrives in", () => {
  assert.equal(normaliseLanguage("zh"), "zh");
  assert.equal(normaliseLanguage("en"), "en");
  assert.equal(normaliseLanguage("  EN\n"), "en");
});

test("anything else falls back rather than reaching a dictionary lookup", () => {
  // A config written by a build that predates the switch. This is the input
  // that actually happens, and it must not turn the window blank.
  assert.equal(normaliseLanguage(undefined), "zh");
  assert.equal(normaliseLanguage(null), "zh");
  assert.equal(normaliseLanguage(""), "zh");
  assert.equal(normaliseLanguage(42), "zh");
  assert.equal(normaliseLanguage({ lang: "en" }), "zh");
  // Close enough to look right, not close enough to be a key. Nothing writes
  // these, so accepting them would only widen what a hand-edited config can
  // put into `document.documentElement.lang`.
  assert.equal(normaliseLanguage("zh-Hant"), "zh");
  assert.equal(normaliseLanguage("en-US"), "zh");
});

test("the fallback is honoured, and is itself checked", () => {
  assert.equal(normaliseLanguage("nonsense", "en"), "en");
  assert.equal(normaliseLanguage(undefined, "en"), "en");
  // A caller passing junk as the fallback still gets a language back; the
  // whole point of this function is that its result is always usable.
  assert.equal(normaliseLanguage("nonsense", "klingon"), DEFAULT_LANGUAGE);
});

test("the switch is a toggle, and total", () => {
  assert.equal(otherLanguage("zh"), "en");
  assert.equal(otherLanguage("en"), "zh");
  // Junk normalises to the default first, so the button on a page whose
  // config was hand-edited still moves somewhere rather than sticking.
  assert.equal(otherLanguage("nonsense"), "en");
  assert.equal(otherLanguage(undefined), "en");
});

test("both menus carry the same labels", () => {
  // The failure this catches: adding an item to one table and not the other
  // renders `undefined` as a menu label, which Electron accepts without
  // complaint. Nothing about the running app says which half was forgotten.
  const zh = menuStrings("zh");
  const en = menuStrings("en");
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort());
  for (const [key, value] of Object.entries(en)) {
    assert.equal(typeof value, "string", `en.${key} is not a string`);
    assert.ok(value.length > 0, `en.${key} is empty`);
    assert.ok(zh[key].length > 0, `zh.${key} is empty`);
  }
});

test("an unusable preference still gets a menu", () => {
  assert.equal(menuStrings("nonsense"), menuStrings(DEFAULT_LANGUAGE));
  assert.equal(menuStrings(undefined), menuStrings(DEFAULT_LANGUAGE));
});
