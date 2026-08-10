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
cd client/app && npm install && npm run build && cd ../..
```

Python 3.12. The production image and the test environment are pinned to the
same version on purpose: `pytest.ini` turns `DeprecationWarning` from `src.*`
into an error, and that is exactly the thing that drifts between minor versions
and only surfaces inside the container.

## Running the tests

```bash
python -m pytest                  # 764 tests, about a minute
python -m pytest --db=sqlite      # the same suite against a real SQLite backend
cd client/shell && node --test    # 16 tests
```

No credentials and no network are needed. All three must pass before a pull
request is reviewed; CI runs the same three.

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

## Style

Match the surrounding code. There is no formatter to run and no lint config to
satisfy beyond `pyflakes` coming back clean.

Commit messages are lowercase, imperative, and say what changed rather than
which files moved — `git log` is the reference.

## Documentation

`README.md` is the only user-facing document; keep it the one that stays
correct. Numbers in it — test counts especially — must be what a fresh clone
actually produces, not what your working tree produces.

## Licensing

Contributions are accepted under the [Apache License 2.0](LICENSE), the same
licence the project ships under. You keep your copyright; you are granting the
same rights the licence grants everyone else, including its patent grant.
