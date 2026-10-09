# Contributing

Thanks for taking an interest. This is a personal project built around specific
hardware, so please read the testing note below before opening a pull request.

## Hardware reality

Most of this code cannot be exercised without the target stack:

- Raspberry Pi 5 with a Hailo-8 AI HAT
- An RTSP camera (H.264 substream)
- `hailo-all` / `hailo-tappas-core` system packages

The GStreamer pipeline, Hailo inference, and tracker integration all need real
hardware. The zone geometry and counting logic in `driveway_counter_hailo.py`
are the exception — they are plain Python and can be reasoned about on any
machine.

If you cannot test on hardware, say so in the PR description. A clearly
described untested change is more useful than a silently untested one.

## Getting started

```bash
git clone git@github.com:natonet-labs/driveway-counter.git
cd driveway-counter
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit for your camera
```

See [`docs/setup-guide.md`](docs/setup-guide.md) for the full install, including
the Hailo system packages and the systemd service.

## Code style

No formatter is enforced automatically — there is no CI on this repository. The
existing code follows a consistent style, so please match it:

- Type annotations on module-level constants and function signatures
- Module and function docstrings, including a `Returns:` section where useful
- Comments explain *why*, not *what* — particularly for hardware workarounds
- Line length 88 (Black default)

If you have `black` and `ruff` available, running them keeps diffs small:

```bash
black .
ruff check --fix .
```

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
type(scope): short summary

Body explaining what changed and why, wrapped at 72 characters.
```

Types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `chore`.

Examples:

```
feat(zone): add second tracking zone for the sidewalk
fix(tracker): reset latch when a track leaves the frame
docs(readme): correct the hailocropper so-path guidance
```

## Pull requests

- One purpose per PR
- Describe what you tested and on what hardware
- Update the relevant file in `docs/` if behaviour or configuration changes
- Note any new `.env` key in `.env.example`

## Reporting issues

For bugs, include:

- Pi model, Hailo HAT model, and OS version
- `hailortcli fw-control identify` output
- Relevant log lines (run with `ISDEBUG=True` for verbose output)
- Camera model and the `SUBTYPE` you are using

[`docs/troubleshooting.md`](docs/troubleshooting.md) covers the failure modes
hit so far — it is worth checking first.
