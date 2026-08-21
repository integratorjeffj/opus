# Building and releasing Opus

Opus ships as a single file per platform: `Opus.exe` on Windows, `Opus.app`
on macOS. The sample catalogue is bundled inside, so a fresh download runs the
whole demo without a checkout, a network connection, or a real order.

## Build it locally

```bash
pip3 install pypdf reportlab pikepdf cryptography Pillow pyinstaller pypdfium2
python3 packaging/build.py --clean
```

Output lands in `dist/`. The build takes a couple of minutes and produces a
~33 MB executable — most of that is the Python runtime and the three PDF
libraries, and it is the price of the user not having to install any of them.

PyInstaller does not cross-compile. A Windows machine builds the `.exe`, a Mac
builds the `.app`. If you only have one of those, use CI (below), which builds
both on every tag.

### Where the version number lives

`__version__` in `opus.py`, and nowhere else. `packaging/build.py` copies it
into the Windows resource block and the macOS bundle at build time, so the
number in the window title, the `--version` flag, the file properties and the
update check can never disagree. Bump it there, tag `v<that number>`, done.

## Signing

An unsigned build works, but the first-run experience is bad in a way that
matters when you are handing this to a client.

| | Unsigned | Signed |
|---|---|---|
| **Windows** | SmartScreen blocks it behind "More info → Run anyway" | Publisher name shown; warning goes away as reputation accrues |
| **macOS** | Gatekeeper refuses outright — "damaged and can't be opened" | Opens normally |

macOS is the one that actually blocks people. The message Gatekeeper shows is
misleading enough that most users conclude the download is broken and give up.

### Windows

An OV code-signing certificate runs roughly **$200–400/year** from a CA
(DigiCert, Sectigo, SSL.com). Since June 2023 the private key has to live on
approved hardware — a token they ship you, or a cloud HSM. Cloud HSM is the
option that works with CI.

An EV certificate costs more and grants SmartScreen reputation immediately
rather than accruing it over weeks. If the client will be downloading this in
front of you, that difference is worth the money.

To sign in CI, add repository secrets:

- `WINDOWS_CERT_PFX` — the `.pfx` base64-encoded
- `WINDOWS_CERT_PASSWORD`

```bash
base64 -w0 cert.pfx > cert.b64   # on macOS: base64 -i cert.pfx -o cert.b64
```

### macOS

Requires the **Apple Developer Program**, $99/year. You need a *Developer ID
Application* certificate — not the Mac App Store one, which will not work for
direct distribution.

Notarization is separate from signing and equally required: Apple's service
scans the binary and issues a ticket, which `stapler` attaches so the app opens
without a network round trip. Budget a few minutes per submission.

Secrets:

- `MACOS_CERT_P12` — the Developer ID cert, base64-encoded
- `MACOS_CERT_PASSWORD`
- `MACOS_SIGN_IDENTITY` — e.g. `Developer ID Application: Your Name (TEAMID)`
- `APPLE_ID`, `APPLE_TEAM_ID`
- `APPLE_APP_PASSWORD` — an app-specific password, not your Apple ID password

With no secrets set, the workflow still builds and still uploads. It emits a
warning on the run rather than failing, so unsigned builds stay available for
internal testing.

## Cutting a release

```bash
# bump __version__ in opus.py first
git commit -am "Opus 1.0.1"
git tag v1.0.1
git push && git push --tags
```

The workflow builds both platforms, runs the engine verification, signs if the
secrets are present, and opens a **draft** release with both binaries attached.
Draft is deliberate — check the artifacts before anyone can download them.

`workflow_dispatch` runs the same build without publishing, which is how to
test a packaging change before tagging.

### What CI checks before it packages anything

The build verifies the engine first: a dry run against the fictional export,
then a real stamping pass, then an assertion that all nine output files exist,
are encrypted, and carry the licence notice on page one. A build that ships a
broken stamper is worse than no build, so packaging does not start until that
passes.

## The update check

The app asks the GitHub Releases API once at startup, on a background thread,
and shows a clickable notice if a newer tag exists. It never blocks the window,
never shows an error, and fails silently — a version check is not worth
interrupting anyone.

Turn it off with `OPUS_NO_UPDATE_CHECK=1` or `--no-update-check`.

This means **the tag is what triggers the update notice**, so tag consistently
(`v1.0.1`, matching `__version__`). A release published without a tag is
invisible to installed copies.

## Icons

`packaging/make_icons.py` draws them; `build.py` runs it automatically.

`.ico` and `.png` generate anywhere. `.icns` needs `iconutil`, which is macOS
only — the macOS CI job regenerates icons before building, so this only bites
if you are hand-building a `.app` off-platform.

## Known rough edges

- **Windowed build, no console.** `console=False` means double-clicking opens
  the app rather than a terminal, which is right for the intended user. The
  side effect is that the CLI flags produce no visible output when run from
  `cmd.exe` against the packaged `.exe`. Run from source for CLI work, or add
  a second console-mode binary if that becomes a real need.
- **Startup time.** A one-file build unpacks to a temp directory on each
  launch, costing a second or two. A one-folder build starts faster but means
  distributing a folder instead of a file, which is the wrong trade for this
  audience.
- **Antivirus false positives.** Unsigned PyInstaller executables get flagged
  by some scanners on reputation alone. UPX compression is disabled in the spec
  because it makes this materially worse. Signing is the actual fix.
