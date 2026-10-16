"""Plain-language descriptions of well-known folders inside ~/.cache.

key: folder name inside ~/.cache
value: (friendly name, what it is / what happens if deleted, risk, process names to check)
Risk is one of "safe", "caution", "review".
"""

from __future__ import annotations

BROWSER_NOTE = ("Web pages, images and scripts the browser saved so sites load faster. "
                "Logins, bookmarks, history and passwords are NOT stored here. "
                "Sites will load a little slower the first time after cleaning.")

KNOWN_CACHES: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    # Browsers & mail
    "mozilla": ("Firefox web cache", BROWSER_NOTE, "caution", ("firefox", "firefox-bin")),
    "google-chrome": ("Google Chrome web cache", BROWSER_NOTE, "caution", ("chrome", "google-chrome")),
    "chromium": ("Chromium web cache", BROWSER_NOTE, "caution", ("chromium", "chromium-browser")),
    "BraveSoftware": ("Brave web cache", BROWSER_NOTE, "caution", ("brave", "brave-browser")),
    "vivaldi": ("Vivaldi web cache", BROWSER_NOTE, "caution", ("vivaldi-bin", "vivaldi")),
    "microsoft-edge": ("Microsoft Edge web cache", BROWSER_NOTE, "caution", ("msedge", "microsoft-edge")),
    "opera": ("Opera web cache", BROWSER_NOTE, "caution", ("opera",)),
    "epiphany": ("GNOME Web cache", BROWSER_NOTE, "caution", ("epiphany",)),
    "thunderbird": ("Thunderbird cache",
                    "Temporary data from Thunderbird. Your mail is stored elsewhere and is not touched.",
                    "caution", ("thunderbird",)),
    "evolution": ("Evolution mail cache",
                  "Evolution keeps OFFLINE COPIES of your mail and calendars here. They will be re-downloaded "
                  "from the server, but anything only stored locally could be lost. Review first.",
                  "review", ("evolution",)),
    # Desktop
    "tracker3": ("GNOME search index",
                 "The index that lets the Activities search find your files instantly. It is rebuilt "
                 "automatically; the computer may be busy for a while afterwards.", "caution", ()),
    "fontconfig": ("Font cache",
                   "A list of installed fonts so programs start quickly. Rebuilt automatically in seconds.",
                   "safe", ()),
    "gstreamer-1.0": ("Multimedia plugin registry",
                      "A list of audio/video plugins. Rebuilt automatically the next time media plays.",
                      "safe", ()),
    "ibus": ("Input method cache", "Keyboard input method data. Rebuilt automatically.", "safe", ()),
    "gnome-software": ("GNOME Software cache",
                       "App icons, screenshots and update information shown in the Software app. "
                       "Downloaded again when needed.", "safe", ("gnome-software",)),
    "ubuntu-report": ("Ubuntu report data", "Leftover data from Ubuntu's optional system report.", "safe", ()),
    "libgweather": ("Weather cache", "Recent weather data. Downloaded again.", "safe", ()),
    "babl": ("Image library cache", "Speed-up data for GIMP's colour library. Rebuilt automatically.", "safe", ()),
    "gnome-desktop-thumbnailer": ("Thumbnailer temp files", "Temporary files from making previews.", "safe", ()),
    "obexd": ("Bluetooth transfer temp files", "Temporary files from Bluetooth file transfers.", "safe", ()),
    "flatpak": ("Flatpak temporary data", "Temporary data from installing or updating Flatpak apps.",
                "safe", ()),
    "spotify": ("Spotify cache",
                "Music Spotify streamed recently AND songs you downloaded for offline listening. "
                "Cleaning removes your offline downloads too.", "review", ("spotify",)),
    # Graphics / games
    "mesa_shader_cache": ("Graphics shader cache (Mesa)",
                          "Pre-compiled graphics code for games and apps on AMD/Intel GPUs. Deleting it "
                          "makes games stutter for a while until shaders are compiled again.",
                          "caution", ()),
    "mesa_shader_cache_db": ("Graphics shader cache (Mesa)",
                             "Pre-compiled graphics code for games and apps on AMD/Intel GPUs. Deleting it "
                             "makes games stutter for a while until shaders are compiled again.",
                             "caution", ()),
    "radv_builtin_shaders": ("Graphics shader cache (AMD)",
                             "Pre-compiled shaders for AMD graphics. Rebuilt automatically.", "caution", ()),
    "nvidia": ("Graphics shader cache (NVIDIA)",
               "Pre-compiled graphics code from the NVIDIA driver. Games may stutter briefly afterwards.",
               "caution", ()),
    "wine": ("Wine installers cache",
             "Downloaded Wine Gecko/Mono installers. Downloaded again if a new Wine prefix needs them.",
             "safe", ()),
    "winetricks": ("Winetricks downloads",
                   "Installers Winetricks downloaded (fonts, runtimes). Downloaded again when needed.",
                   "safe", ("winetricks",)),
    "lutris": ("Lutris cache", "Downloaded installers and banners from Lutris. Downloaded again when needed.",
               "caution", ("lutris",)),
    # Developer tools
    "pip": ("Python pip download cache",
            "Python packages pip downloaded before. Installed packages keep working; pip just "
            "downloads again next time.", "safe", ()),
    "uv": ("Python uv cache", "Python packages downloaded by uv. Existing environments keep working.",
           "safe", ()),
    "pypoetry": ("Poetry cache",
                 "Poetry's package cache — and, by default, Poetry's VIRTUAL ENVIRONMENTS for your projects. "
                 "They can be recreated with `poetry install`.", "caution", ()),
    "go-build": ("Go build cache", "Compiled Go code. Rebuilt automatically on the next `go build`.",
                 "safe", ()),
    "yarn": ("Yarn package cache", "JavaScript packages Yarn downloaded. Downloaded again when needed.",
             "safe", ()),
    "pnpm": ("pnpm cache", "JavaScript package metadata. Downloaded again when needed.", "safe", ()),
    "node-gyp": ("node-gyp headers", "Node.js headers for building native modules. Re-downloaded.",
                 "safe", ()),
    "electron": ("Electron downloads", "Electron runtime downloads from development tools.", "safe", ()),
    "typescript": ("TypeScript type cache", "Downloaded type definitions for editors.", "safe", ()),
    "deno": ("Deno cache", "Downloaded Deno modules. Downloaded again when needed.", "safe", ()),
    "composer": ("PHP Composer cache", "Downloaded PHP packages.", "safe", ()),
    "ccache": ("C/C++ compiler cache", "Speeds up recompiling C/C++ code. Builds are slower once.",
               "safe", ()),
    "sccache": ("Compiler cache (sccache)", "Speeds up recompiling code. Builds are slower once.", "safe", ()),
    "bazel": ("Bazel build outputs", "Build outputs and downloads of Bazel projects.", "caution", ()),
    "JetBrains": ("JetBrains IDE caches",
                  "Indexes and caches of IntelliJ, PyCharm, etc. The IDE re-indexes your projects "
                  "(slow the first time). Close the IDE first.", "caution", ("idea", "pycharm", "clion",
                                                                               "webstorm", "goland", "rider")),
    "vscode-cpptools": ("VS Code C++ IntelliSense cache", "Rebuilt automatically by VS Code.", "safe", ()),
    "ms-playwright": ("Playwright test browsers",
                      "Browsers downloaded for automated web testing. Development projects that use "
                      "Playwright will need `npx playwright install` again.", "review", ()),
    "huggingface": ("Hugging Face AI models",
                    "Downloaded AI models and datasets. They can be several GB each and take a long time "
                    "to download again.", "review", ()),
    "torch": ("PyTorch model downloads", "Downloaded machine-learning models. Re-downloaded when needed.",
              "review", ()),
    "yay": ("yay AUR build files", "Build folders from installing AUR packages. Safe to delete.", "safe", ("yay",)),
    "paru": ("paru AUR build files", "Build folders from installing AUR packages. Safe to delete.",
             "safe", ("paru",)),
}

# Never offered from ~/.cache: thumbnails have their own smarter category.
SKIP_IN_USER_CACHE = {"thumbnails"}

UNKNOWN_DESCRIPTION = ("Temporary data saved by “{name}”. Programs put things here they can rebuild "
                       "or re-download, so deleting it is usually harmless — the program may just be "
                       "slower the next time it starts. Close it first.")
