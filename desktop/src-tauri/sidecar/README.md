# Bundled Runtime output

`npm run build:sidecar` creates the platform-native `verilex-runtime` executable in this directory. The generated binary is intentionally not committed; `tauri build` runs the sidecar build first and embeds this directory into the application bundle.
