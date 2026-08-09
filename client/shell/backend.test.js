// node --test backend.test.js
//
// `readWebPort` is pure and gets ordinary unit tests. Everything else here
// runs the actual `dist-standalone/discord-drive.exe` this repo builds --
// not a stand-in -- because the entire point of this module is a process
// lifecycle protocol (stdin as both the password channel and the shutdown
// signal), and a mock of the child process would only prove this code
// agrees with my own assumptions about how that child behaves. The
// mechanism itself -- that ending a child's stdin reaches
// `_wait_for_shutdown`'s `extra_stop` and drains cleanly, and that
// `child.kill()` on Windows does not -- was verified separately against
// small standalone probe scripts before any of this was written; these
// tests are what confirm the real shipped binary still holds up its end.
//
// What they cannot cover without a real Discord bot token: a start that
// actually succeeds. "wrong-looking token gets a clean rejection" is as far
// as this goes -- it is still the same code path a real token takes, just
// stopped one HTTP round trip short of Discord's answer.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { LocalDrive, readWebPort } = require("./backend.js");

const EXE = path.join(__dirname, "..", "..", "dist-standalone", "discord-drive.exe");

test("readWebPort", async (t) => {
  await t.test("falls back to 8080 when the file is missing", () => {
    assert.equal(readWebPort(path.join(os.tmpdir(), "does-not-exist.env")), 8080);
  });

  await t.test("reads an explicit value", () => {
    const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dd-")), "drive.env");
    fs.writeFileSync(file, "SFTP_USER=x\nWEB_PORT=9090\n");
    assert.equal(readWebPort(file), 9090);
  });

  await t.test("skips a commented-out line", () => {
    const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dd-")), "drive.env");
    fs.writeFileSync(file, "# WEB_PORT=9090\n");
    assert.equal(readWebPort(file), 8080);
  });
});

test("LocalDrive against the real binary", { skip: !fs.existsSync(EXE) && "build discord-drive.exe first (PyInstaller discord-drive.spec)" }, async (t) => {
  await t.test("a fresh data directory is reported as first-run", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "dd-home-"));
    const drive = new LocalDrive();

    const result = await drive.status(EXE, home);

    assert.equal(result.state, "first-run");
    assert.ok(fs.existsSync(path.join(home, "drive.env")),
      "the template should exist even though startup refused to continue");
  });

  await t.test("a missing executable is reported distinctly, not as a crash", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "dd-home-"));
    const drive = new LocalDrive();

    const result = await drive.status(path.join(home, "nope.exe"), home);

    assert.equal(result.state, "missing");
  });

  await t.test("a filled-in config blocks on the password, not on a fixed delay", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "dd-home-"));
    const drive = new LocalDrive();
    await drive.status(EXE, home); // writes the template, exits (first-run)

    fs.writeFileSync(path.join(home, "drive.env"), [
      "DISCORD_BOT_TOKEN=not-a-real-token-for-the-shell-integration-test",
      "DISCORD_CHANNEL_ID=100000000000000000",
      "SFTP_USER=shelltest",
      "WEB_ENABLED=0",
      "SFTP_PORT=39222",
    ].join("\n"));

    const result = await drive.status(EXE, home);

    assert.equal(result.state, "awaiting-password");
    assert.ok(drive.running, "the child should still be alive, blocked on stdin");

    await drive.stop();
    assert.ok(!drive.running);
  });

  await t.test(
    "a wrong-looking token is rejected after the password is sent, proving the "
    + "piped password actually reached resolve_password and unwrapped the keystore",
    async () => {
      const home = fs.mkdtempSync(path.join(os.tmpdir(), "dd-home-"));
      const drive = new LocalDrive();
      await drive.status(EXE, home);

      fs.writeFileSync(path.join(home, "drive.env"), [
        "DISCORD_BOT_TOKEN=not-a-real-token-for-the-shell-integration-test",
        "DISCORD_CHANNEL_ID=100000000000000000",
        "SFTP_USER=shelltest",
        "WEB_ENABLED=0",
        "SFTP_PORT=39222",
        "ARGON2_TIME_COST=1",
        "ARGON2_MEMORY_KIB=64",
        "ARGON2_PARALLELISM=1",
      ].join("\n"));

      await drive.status(EXE, home, { force: true });

      const result = await drive.start("integration-test-password-long-enough");

      assert.equal(result.ok, false);
      assert.equal(result.reason, "exited");
      // The exact message a rejected token produces (src/discord_api.py):
      // proves this ran the real reachability check, not a startup failure
      // from a step before it -- which means the password it piped in
      // successfully created the account row and unwrapped the master key.
      assert.match(result.output, /DISCORD_BOT_TOKEN was rejected by Discord/);
    },
  );

  await t.test("stopping a child waiting for the password exits it cleanly", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "dd-home-"));
    const drive = new LocalDrive();
    await drive.status(EXE, home);
    fs.writeFileSync(path.join(home, "drive.env"), "SFTP_USER=x\n");
    const result = await drive.status(EXE, home, { force: true });
    assert.equal(result.state, "awaiting-password");

    const before = Date.now();
    await drive.stop();
    const elapsed = Date.now() - before;

    assert.ok(!drive.running);
    // Should be near-instant (EOF on the blocked read), not the fallback
    // force-kill timeout -- if it took anywhere near that, ending stdin did
    // not actually reach the process the way it is supposed to.
    assert.ok(elapsed < 5000, `stop() took ${elapsed}ms, expected a fast graceful exit`);
  });
});
