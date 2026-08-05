# Third Party Notices

## Zhalslar/astrbot_plugin_parser

- Source: https://github.com/Zhalslar/astrbot_plugin_parser
- License: MIT License
- Usage in this MaiBot port:
  - Reused parser core under `core/`
  - Replaced AstrBot message event and send APIs with MaiBot SDK hooks and OneBot HTTP sending
  - Added a small `astrbot.api.logger` compatibility shim for migrated core modules

The original MIT license text and its upstream copyright notice are included in `LICENSE`. They are retained as required by the upstream license and do not identify the current repository owner.

## BtbN/FFmpeg-Builds

- Source: https://github.com/BtbN/FFmpeg-Builds
- License: GPL 2.0 (default builds), with components under LGPL and BSD; see the project's licensing notes
- Usage in this MaiBot port:
  - When `ffmpeg.enabled = true`, the plugin downloads the static `ffmpeg` binary for the current platform from the BtbN release assets into its data directory at first use. The binary is not vendored into this repository.
  - The GPL default build is selected for compatibility with the H.264 (`libx264`) re-encode path; users can point `ffmpeg.download_url` at a LGPL or mirrored build instead.

Distribution of the GPL build follows GPL v2 terms. The downloaded binary is a runtime artifact under the end user's control; it is not committed to or redistributed by this repository.
