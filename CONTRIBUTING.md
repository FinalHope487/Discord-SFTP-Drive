# Contributing

## Before a large change

Open an issue first. Several things that look like obvious improvements are
deliberate decisions with the reasoning written down — `ROADMAP.md` has a
"已拍板的長期決策" (settled decisions) section covering why the trash marker is
inside the integrity tag, why there is no backward compatibility for
pre-HMAC chunks, why the directory lock is process-level rather than optimistic,
and more. It is in Chinese; ask in an issue and it will be summarised in
English.

Small fixes — a bug, a typo, a missing test — go straight to a pull request.

## Setting up

```bash
python -m venv venv
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium
cd client/app && npm install && npm run build && cd ../..
```

Python 3.12. The production image and the test environment are pinned to the
same version on purpose: `pytest.ini` turns `DeprecationWarning` from `src.*`
into an error, and that is exactly the thing that drifts between minor versions
and only surfaces inside the container.

The last two lines are not optional for the test suite. `pip` does not download
a browser, and the browser tests are the only ones that drive the layer the user
touches; the built frontend is what they drive, and a stale `client/app/dist`
fails them on purpose rather than rebuilding itself.

## Running the tests

```bash
python -m pytest                  # 764 tests, about 45 seconds
python -m pytest --db=sqlite      # the same suite against a real SQLite backend
cd client/shell && node --test    # 16 tests, 11 without a built backend
```

No credentials and no network are needed — MongoDB and the Discord API are
faked. All three must pass before a pull request is reviewed; CI runs the same
three. Two of those counts move with the environment:

- **`pytest` drops to 759 without a browser.** `tests/test_ui_login.py` is the
  only file that needs one, and a missing browser is an error there, not a skip.
  Inside the production image, for instance:
  `python -m pytest --ignore=tests/test_ui_login.py`.
- **`node --test` drops to 11 without a built backend.** The block that drives
  the real `dist-standalone/discord-drive.exe` skips itself when the file is
  absent, which is the case on CI — PyInstaller only builds for the platform it
  runs on.

The SQLite run is how that backend is checked: rather than a second set of tests
written against someone's reading of it, the same assertions written for
MongoDB's behaviour are pointed at it. Three tests skip there and say why — they
drive MongoDB's refusal to change an index in place, which has no counterpart.
That run is what found two bugs invisible to the default one.

**A green suite is not proof the thing works.** The fakes model neither rate
limits nor attachment URL expiry, they do not enforce uniqueness, and they do
not validate index specifications — the trash once shipped with a partial unique
index MongoDB rejects outright, and the suite stayed green for three days
because nothing had restarted against a real server. Several bugs in this
project's history were only ever found by hand against a real bot token. Running
inside the production image is a separate check, which is why both are pinned to
the same Python version:

```bash
docker run --rm --user root -v "$PWD:/repo" -w /repo discord-drive-sftp-discord-server \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q --ignore=tests/test_ui_login.py"
```

## What counts as done

This project has been bitten repeatedly by green tests that proved less than
they appeared to, so the bar is specific:

- **A change to user-visible behaviour needs a test at the layer users touch.**
  Tests that call the VFS directly are immune to "the protocol layer never
  called it", which is where several real bugs lived. `tests/test_sftp_e2e.py`
  exists for that reason.
- **A new test must fail before it passes.** Break the behaviour it covers once,
  confirm red, then put it back.
- **"It looked right when I ran it" is not verification.** Browser interaction,
  screenshots and manual output reading are debugging tools, not acceptance.
- **The fakes do not model everything, and they say so.** They do not enforce
  uniqueness, validate index specifications, model rate limits, or expire
  attachment URLs. If a change depends on any of those, it needs checking
  against a real backend — `--db=sqlite` covers some of it, a real MongoDB
  restart covers the rest.

## Building from source

```bash
# Frontend (needed by both builds)
cd client/app && npm install && npm run build

# Standalone executable — needs Python 3.12
python -m pip install -r requirements-dev.txt
python -m PyInstaller discord-drive.spec --noconfirm \
  --distpath dist-standalone --workpath build-standalone

# Desktop app
cd client/shell && npm install && npm run dist
```

PyInstaller and electron-builder both **only build for the platform they run
on** — a Linux build has to happen on Linux. `discord-drive.spec` must be built
before `npm run dist`, which copies the backend executable in as a packaged
resource; that is why the desktop app can run the standalone build itself.

The frontend is a build product and is not in the repository.
`docker-compose.yml` mounts `client/app/dist` read-only, so rebuilding it costs
one command and a refresh rather than an image rebuild — which would drop every
live session and every unwrapped key with it. Until it is built, `/` serves a
page saying so; the API and SFTP are unaffected.

The desktop app carries no copy of the web client. The session cookie is
`SameSite=Strict`, and a page loaded from `file://` would never be allowed to
send it, so the shell is a window plus a first-run screen asking where the
server is — or asking for the password, when it is running the backend itself.

## Style

Match the surrounding code. There is no formatter to run and no lint config to
satisfy beyond `pyflakes` coming back clean.

Commit messages are lowercase, imperative, and say what changed rather than
which files moved — `git log` is the reference.

## Documentation

**Every fact has exactly one home, and the file that owns it is the only file
that states it.** Two copies of a number mean one of them is already wrong; the
test count in this file has been wrong twice for that reason.

| Document | Owns |
| --- | --- |
| `README.md` | What it is, the ToS risk, choosing a build, setup, everyday use. The landing page — every other document must be reachable from it. |
| `docs/OPERATIONS.md` | Remote access, backup and recovery, troubleshooting, known limits. |
| `CONTRIBUTING.md` | Building, testing, and the bar for "done". Every test number in the project lives here. |
| `.env.example` | Every setting and what getting it wrong costs. |
| `ROADMAP.md` | Why anything is the way it is, and the changelog. Chinese. |

Numbers must be what a fresh clone actually produces, not what your working tree
produces.

## Licensing

Contributions are accepted under the [Apache License 2.0](LICENSE), the same
licence the project ships under. You keep your copyright; you are granting the
same rights the licence grants everyone else, including its patent grant.
