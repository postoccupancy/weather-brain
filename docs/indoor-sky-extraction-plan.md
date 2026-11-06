# Indoor Sky firmware extraction plan

Original plan, 2026-11-05. Source: Weather Brain `fa5d79cc5b575cca6fc69510db6205004e62658f`.
Implemented at user request in the existing Indoor Sky repository. See
[extraction validation](https://github.com/postoccupancy/indoor-sky/blob/main/docs/extraction-validation.md).
The mapping and gates below record the reviewed plan; macOS runtime/hardware
validation remains outstanding. No firmware flashed or services restarted.

## Destination and ownership

Use `C:/Users/PC1/projects/indoor-sky` as the PlatformIO project/repository root.
It already contains `.git`, has no local commits or working files, and its origin
is `https://github.com/postoccupancy/indoor-sky.git`. This inspection did not query
the remote; fetch/inspect it before import rather than assuming it is empty.
Do not overwrite any remote initialization or existing history.

Indoor Sky owns ESP32-S3 DevKit firmware, USB contract documentation and host-side
Pi flashing. Electric Sea continues to own the Pi router, serial parser and browser
interfaces. Weather Brain retains Python API, PostgreSQL, archives and analysis.
Outdoor Electric Sky ESP32-S3 CAM firmware is out of scope and unchanged; no
`electric-sky` checkout was present among the inspected local project siblings.

## Exact tracked-file mapping

All source paths below are relative to Weather Brain; destinations are relative
to Indoor Sky. There are exactly ten currently tracked files under `device/`.

| Source | Destination | Treatment |
| --- | --- | --- |
| `device/platformio.ini` | `platformio.ini` | Preserve both USB/OTA environments and all board, memory, partition and dependency settings. |
| `device/src/main.cpp` | `src/main.cpp` | Copy unchanged. |
| `device/include/SampleRing.h` | `include/SampleRing.h` | Copy unchanged. |
| `device/include/Dashboard.h` | `include/Dashboard.h` | Copy unchanged initially; retained firmware-served dashboard is used by the router fallback/cache refresh. |
| `device/scripts/build_config.py` | `scripts/build_config.py` | Fix Git root as described below. |
| `device/scripts/deploy_usb_via_pi.sh` | `scripts/deploy_usb_via_pi.sh` | Preserve working Windows/Mac USB workflow. |
| `device/scripts/espota.py` | `scripts/espota.py` | Preserve extended timeout and upstream attribution. |
| `device/scripts/use_local_espota.py` | `scripts/use_local_espota.py` | Copy unchanged; uploader resolves relative to PROJECT_DIR. |
| `device/secrets.example.py` | `secrets.example.py` | Keep only placeholder WIFI_SSID/WIFI_PASS for active firmware. |
| `device/main.py` | `legacy/micropython/main.py` | Preserve as historical, outside active build. |

Add these new tracked Indoor Sky files:

- `.gitignore`: ignore `secrets.py`, `.pio/`, `.env`, `.env.*` except intentional
  example files, `.venv/`, `__pycache__/`, `*.pyc`, `.DS_Store`, and local flash artifacts.
- `.gitattributes`: enforce LF for `*.sh`; retain executable mode on deployment script.
- `README.md`: hardware/wiring, four-repository ownership, prerequisites, both host
  workflows, local secrets, identity and verification, firmware source provenance.
- `docs/usb-protocol.md`: document existing framing, byte order, structs, units,
  version, scalar/audio/status packets and commands from firmware/router sources.
- `docs/deployment.md`: build-only/preflight versus flashing, platform paths, serial
  identity, expected interruption, restoration checks and recovery procedure.
- `legacy/micropython/README.md` and `legacy/micropython/secrets.example.py`: mark
  AM2320/direct-HTTP ingest implementation obsolete; preserve original placeholder
  settings here, with a warning that DEVICE_ID is a historical identity.

Do not copy `.git` from Weather Brain, `.pio`, binaries, generated headers, Python
caches, backend configuration, SSH keys or API secrets. Copy the existing ignored
`device/secrets.py` locally to ignored `indoor-sky/secrets.py` only after the new
ignore rules are verified, without printing its contents. Each Mac needs its own
local secret provisioning. Keep old ignored build/secrets files until rollback
is no longer needed; deletion is a separate explicit cleanup.

## Build and flashing audit

`build_config.py` currently sets `repo_dir = project_dir.parent`. This works for
Weather Brain/device but would query the workspace parent after extraction.
Set `repo_dir = project_dir` in the extracted root-level project and resolve/assert
`git rev-parse --show-toplevel` equals that project root. Fail clearly if there is
no committed Indoor Sky HEAD; never fall back to a sibling/parent repository.
The initial import must be committed before building because identity reads HEAD.

Keep generated `.pio/generated/build_config.h` relative to PROJECT_DIR and retain
the existing firmware_git_sha, firmware_git_dirty and firmware_build_utc status
fields. SHA will identify Indoor Sky commits after extraction; old Weather Brain
SHAs remain historical identities. Record the source commit above in README.
Dirty detection currently ignores untracked files; document that limitation and
verify expected sources are tracked rather than changing its semantics here.

PlatformIO src/include/extra_scripts paths already resolve from PROJECT_DIR;
the root layout above preserves them. The deployment script's ROOT is its parent
directory, so moving scripts/ intact correctly changes its build root. Build
output remains `.pio/build/esp32_s3_devkit_usb`, and all flash offsets remain
0x0/0x8000/0xe000/0x10000. Do not introduce a nested device/ directory.

Retain Windows native OpenSSH selection, Windows PlatformIO Scripts path, Unix
penv/bin/pio default, PIO/PI_HOST/PIO_ENV/INDOOR_SKY_SERIAL_DEVICE overrides, and
`MSYS_NO_PATHCONV=1` on the remote SSH command. Retain the Pi esptool path and
stable serial by-id target. Mac behavior is statically reviewed, not host-tested.

Existing assumptions to document and verify, without bundling unrelated refactors:
BOOT_APP0 assumes the default `$HOME/.platformio` package directory; PIO_ENV must
use the same partition/offset layout; remote staging uses fixed `/tmp/indoor-sky-flash`;
router restoration trap suppresses start errors, so verify service recovery
explicitly. Normal preflight must not execute the deployment script: it flashes.

New commands, from Indoor Sky root, only at the appropriate approved stage:

```bash
# Mac, build only
~/.platformio/penv/bin/pio run -d . -e esp32_s3_devkit_usb
# Mac, deployment (interrupts router)
bash scripts/deploy_usb_via_pi.sh
```

```powershell
# Windows, build only
& "$env:USERPROFILE/.platformio/penv/Scripts/platformio.exe" run -d . -e esp32_s3_devkit_usb
# Windows, deployment (interrupts router)
& 'C:/Program Files/Git/bin/bash.exe' scripts/deploy_usb_via_pi.sh
```

## Legacy and security review

MicroPython `main.py` uses AM2320, LCD, network/urequests and direct `/ingest`
authentication. It is not used by the Arduino PlatformIO build. The imported LCD
module is absent from tracked files, so do not present it as runnable or recreate
missing libraries. Keep it as an explicit legacy archive in Indoor Sky, leaving
Weather Brain free of firmware code after cutover. Do not change historical
device IDs, API compatibility, stored rows or migrations to accommodate it.

Current checks: `device/secrets.py` and `.pio/generated/build_config.h` are ignored;
secrets.py is untracked; the tracked example contains placeholder credentials.
Device path history lists the same ten file paths, not a secrets.py path. This
is not a comprehensive content/history secret scan. Before any import/push, scan
all candidate tracked content and any history to be imported, reporting paths
and credential types without exposing values. If a real secret is found, stop
publication and arrange rotation/redaction. Preserve third-party uploader notices;
verify licensing/provenance before publishing a new repository license.

The generated header and firmware images contain Wi-Fi credentials; exclude both
from Git and public artifacts. The active build only consumes WIFI_SSID/WIFI_PASS;
legacy SERVER_ENDPOINT/TOKEN/DEVICE_ID are not needed for it. Preserve AST-based
literal parsing rather than executing local secrets. Use each host's existing SSH
identity; do not copy private keys into the firmware repository or onto the Pi.
Existing Wi-Fi/OTA/status behavior remains unchanged by this extraction.

## Cross-repository updates

- Weather Brain README line 91 currently labels device/ as MicroPython. Replace
  that tree entry with an external Indoor Sky firmware link and ownership note.
- Weather Brain docs/agent-context.md: add completed extraction handoff and new
  paths; retain historical SHA/build/deployment records rather than rewriting them.
- Weather Brain docs/agent-policy.md and AGENTS.md: keep backend policies; establish
  separate firmware guidance in Indoor Sky if desired. Do not copy backend Python
  interpreter settings (`.vscode/settings.json`) into the firmware repository.
- Electric Sea README: add firmware repository/build/deployment links, keep Pi
  router/browser ownership. No router code change is required for path extraction.
- Actual VS Code multi-root workspace: add sibling Indoor Sky folder and update
  any local tasks/launch configurations from weather-brain/device to indoor-sky.
  No `.code-workspace` file was found in the inspected locations; identify the
  active workspace file during implementation rather than inventing its path.
- Searches of tracked working sources in Weather Brain, Electric Sea, esp32_ui
  and postoccupancy.com found no additional operational device/ build-path
  dependency. Electric Sea's Weather Brain archive-doc links remain valid.
- Backend docs/tests containing indoor-sky/electric-sky node names, routes and
  deployment IDs remain unchanged. They are data/protocol references, not stale
  source-directory references. Outdoor Electric Sky repository remains untouched.

## Validation and cutover gates

1. Fetch/read destination state, check local worktrees, inspect repository access,
   record source commit/file hashes and export a clean allowlisted snapshot.
   Prefer a new import commit with source provenance over publishing backend Git
   history; if history preservation is requested, audit a device-only filtered
   history separately before publication.
2. Create ignore rules first; verify secrets/generated files stay ignored and
   absent from the index. Finish candidate-content/history scan and license check.
3. Import the exact mapping and documentation, fix root resolution, then commit
   before identity validation. Compare copied C++/header/protocol code byte-for-byte
   against the source baseline. No protocol, timing, hardware or runtime edits.
4. Build both existing PlatformIO environments on Windows and on a real Mac,
   without upload targets. Keep the known toolchain/dependency versions for parity
   (last Windows build used espressif32 7.1.3); avoid upgrades during extraction.
   Confirm 8 MB flash/octal PSRAM settings, partition image and flash offsets.
   Generated SHA must match Indoor Sky HEAD; verify clean/dirty behavior and UTC
   timestamp without exposing credential-bearing header contents. Missing secrets
   and missing/wrong Git root must fail clearly. Binaries need not be identical
   because identity/timestamps change.
5. On each host, check Bash syntax and selected tool paths, native SSH identity,
   noninteractive SSH, harmless SCP round trip with matching hashes, Pi esptool,
   serial by-id existence/permissions and sudo. Confirm remote paths remain POSIX.
   Do not open the busy serial port or stop/restart router for these checks.
6. Verify unchanged USB/OSC contract against Electric Sea parser and its tests:
   INSK scalar/version 1, INJS status, existing audio framing, INRS/INP1/INP0,
   rates, timestamps, sizes, node ID indoor-sky and /batch/indoor-sky/* routes.
   Keep all API paths, database schema and archive node IDs unchanged.
7. Only after review and deployment authorization, perform one controlled USB
   flash through each host workflow as needed. Verify esptool hashes, router
   recovery, identity via /indoor-sky/status, BME/mic ~100/250 Hz, USB sequence
   continuity, no increasing failures/drops, PCM control and browser live data.
   Existing archives/deployment associations must continue under the same node.
8. Remove the ten tracked Weather Brain device files only after the new repository
   is committed/published and build checks pass; update references in the same
   cutover. Preserve old Git history and privately retained known-good artifacts
   for rollback. Do not require an API/database/service change for source cutover.

All validation above is planned unless explicitly identified as an existing check.
This task performed inspection only, not new builds, SSH probes or hardware tests.

2026-11-06 follow-up: rf-router becomes the application/service name in Electric Sea.
Its checked-in Indoor dashboard uses USB only; old firmware dashboard/cache support
remains solely for rollback. See Electric Sea docs/rf-router-migration.md.
