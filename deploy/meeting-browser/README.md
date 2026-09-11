# Radxa browser participant

This optional component gives the Radxa a current ARM64 browser without replacing
its Debian 11 system. Debian 12, Chromium, Xvfb, PulseAudio, FFmpeg and noVNC live
under `/opt/meeting-browser/rootfs`. Debian packages are authenticated against
signed archive metadata. The host package database and libc are not upgraded.

The browser is an ordinary meeting participant. The station's Join & record flow
enters its name, disables camera/microphone, requests admission and records the
meeting automatically. An organizer may need to admit it; account requirements
remain provider-controlled. Online meetings require internet connectivity;
audio processing stays local on the Mac.

## Provision and activate

Copy this directory to `/opt/meeting-browser` and run `sudo
/opt/meeting-browser/provision.sh`. Provisioning uses a private mount namespace,
so temporary `/proc` and `/dev` mounts disappear when it finishes. The script's
Debian 11 bootstrap-tool version is chosen from the board's signed main package
index, because its cached security index references a retired download.

Configure the
generated controller token in `/opt/meeting-browser/browser.env` (root-owned,
mode 0600), with `MI_BROWSER_TOKEN` containing at least 16 characters. The station
bridge must use the same token; do not place it in Git or a viewer URL.

Run `sudo /opt/meeting-browser/activate.sh`. It creates a dedicated host account
matching the rootfs account (UID/GID 1996), installs the controller and unit, and
starts the service. It refuses to reuse a UID/GID already assigned to another
account. If 1996 is occupied, choose the same unused identity in both the host
activation and rootfs provisioning scripts first.

The service runs as the dedicated account with Chromium's sandbox enabled.
Its profile and recordings persist under `/var/lib/meeting-browser`, bound into
the isolated filesystem. Runtime sockets are under `/run/meeting-browser`.

## Interfaces

All network listeners bind only to loopback:

| Port | Function |
| --- | --- |
| 9222 | Chromium DevTools Protocol, for local controller use |
| 5900 | VNC, for the local websockify process |
| 6080 | noVNC assets and WebSocket relay |
| 8770 | Authenticated browser and capture controller |

Expose the viewer only through the station's authenticated proxy. Never expose
the debugging or VNC ports directly to the LAN. Browser sign-in sessions remain
in the dedicated profile, separate from Carelink and the employee's Mac browser.
The WebSocket relay also requires the controller bearer token and rejects browser
Origin headers. The station establishes that internal connection without an
Origin; the browser connects through the station's authenticated viewer session.

PulseAudio's private Unix socket is
`unix:/run/meeting-browser/pulse/native`. The recording source is
`meeting_output.monitor`. The browser's default microphone is a separate silent
source, `meeting_silence.monitor`, to avoid feeding meeting playback back into
the call. The controller runs FFmpeg inside the rootfs and finalizes each WAV
with SIGINT before handing it to the station for processing.

Authenticated `POST /v1/join` accepts a supported meeting URL and recording UUID.
The controller starts capture before requesting admission, then runs the bounded
local `join.js` interaction loop through CDP. It distinguishes waiting, joined,
blocked and ended states. Admission is recognized from call controls; merely
navigating is never success. A rejected or timed-out join retains a failed capture.
The station bridge polls finalized recordings and imports them without an open
browser dashboard. The provider can still require initial account setup or human
verification; these are reported instead of bypassed. The join timeout is five
minutes. Chromium permits unattended audio playback in its isolated profile;
this setting does not affect the employee's browser.

The launch flag and CDP user gesture follow Chrome's documented
[autoplay behavior](https://developer.chrome.com/blog/autoplay/). The interaction
loop resumes paused incoming audio without enabling the station microphone.
Status measures recent saved PCM and file growth separately, exposes an audio
meter and warns about quiet or stalled capture. FFmpeg uses
[`flush_packets=1`](https://ffmpeg.org/ffmpeg-formats.html#Format-Options) to keep
these measurements current. A finalized all-zero WAV is retained as an error,
not sent to the Mac as a successful recording.

## Verification and rollback

Check `systemctl status meeting-browser`, its journal, loopback-only listeners,
the authenticated controller health response, and the browser viewer. Before
using a real call, test a synthetic browser audio signal through the dedicated
PulseAudio monitor, finalize the WAV, and verify its duration and samples.
Confirm real host admission and captured remote speech separately; synthetic
capture does not demonstrate successful meeting participation.

On the Cubie A7A, the installed Chromium 152 service started with approximately
337 MiB idle memory. A synthetic browser-generated tone passed through PulseAudio
and the controller into a finalized 2.7015-second, 16 kHz mono WAV with nonzero
samples. Missing controller authentication and direct browser-origin WebSocket
access were rejected; the authenticated internal relay succeeded. These checks
verify capture and access control, not real-meeting admission or ASR accuracy.

To stop this optional component, run `sudo systemctl disable --now
meeting-browser`. The rootfs, profile and recordings remain available. Carelink
files and the base operating system are retained throughout.
