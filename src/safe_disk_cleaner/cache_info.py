"""Plain-language descriptions of well-known folders inside ~/.cache.

key: folder name inside ~/.cache
value: (friendly name, what it is / what happens if deleted, risk, process names to check)
Risk is one of "safe", "caution", "review".
"""

from __future__ import annotations
from .i18n import _


BROWSER_NOTE = (_("Web pages, images and scripts the browser saved so sites load faster. Logins, bookmarks, history and passwords are NOT stored here. Sites will load a little slower the first time after cleaning."))

KNOWN_CACHES: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    # Browsers & mail
    "mozilla": (_("Firefox web cache"), BROWSER_NOTE, "caution", ("firefox", "firefox-bin")),
    "google-chrome": (_("Google Chrome web cache"), BROWSER_NOTE, "caution", ("chrome", "google-chrome")),
    "chromium": (_("Chromium web cache"), BROWSER_NOTE, "caution", ("chromium", "chromium-browser")),
    "BraveSoftware": (_("Brave web cache"), BROWSER_NOTE, "caution", ("brave", "brave-browser")),
    "vivaldi": (_("Vivaldi web cache"), BROWSER_NOTE, "caution", ("vivaldi-bin", "vivaldi")),
    "microsoft-edge": (_("Microsoft Edge web cache"), BROWSER_NOTE, "caution", ("msedge", "microsoft-edge")),
    "opera": (_("Opera web cache"), BROWSER_NOTE, "caution", ("opera",)),
    "epiphany": (_("GNOME Web cache"), BROWSER_NOTE, "caution", ("epiphany",)),
    "thunderbird": (_("Thunderbird cache"),
                    _("Temporary data from Thunderbird. Your mail is stored elsewhere and is not touched."),
                    "caution", ("thunderbird",)),
    "evolution": (_("Evolution mail cache"),
                  _("Evolution keeps OFFLINE COPIES of your mail and calendars here. They will be re-downloaded from the server, but anything only stored locally could be lost. Review first."),
                  "review", ("evolution",)),
    # Desktop
    "tracker3": (_("GNOME search index"),
                 _("The index that lets the Activities search find your files instantly. It is rebuilt automatically; the computer may be busy for a while afterwards."), "caution", ()),
    "fontconfig": (_("Font cache"),
                   _("A list of installed fonts so programs start quickly. Rebuilt automatically in seconds."),
                   "safe", ()),
    "gstreamer-1.0": (_("Multimedia plugin registry"),
                      _("A list of audio/video plugins. Rebuilt automatically the next time media plays."),
                      "safe", ()),
    "ibus": (_("Input method cache"), _("Keyboard input method data. Rebuilt automatically."), "safe", ()),
    "gnome-software": (_("GNOME Software cache"),
                       _("App icons, screenshots and update information shown in the Software app. Downloaded again when needed."), "safe", ("gnome-software",)),
    "ubuntu-report": (_("Ubuntu report data"), _("Leftover data from Ubuntu's optional system report."), "safe", ()),
    "libgweather": (_("Weather cache"), _("Recent weather data. Downloaded again."), "safe", ()),
    "babl": (_("Image library cache"), _("Speed-up data for GIMP's colour library. Rebuilt automatically."), "safe", ()),
    "gnome-desktop-thumbnailer": (_("Thumbnailer temp files"), _("Temporary files from making previews."), "safe", ()),
    "obexd": (_("Bluetooth transfer temp files"), _("Temporary files from Bluetooth file transfers."), "safe", ()),
    "flatpak": (_("Flatpak temporary data"), _("Temporary data from installing or updating Flatpak apps."),
                "safe", ()),
    "spotify": (_("Spotify cache"),
                _("Music Spotify streamed recently AND songs you downloaded for offline listening. Cleaning removes your offline downloads too."), "review", ("spotify",)),
    # Graphics / games
    "mesa_shader_cache": (_("Graphics shader cache (Mesa)"),
                          _("Pre-compiled graphics code for games and apps on AMD/Intel GPUs. Deleting it makes games stutter for a while until shaders are compiled again."),
                          "caution", ()),
    "mesa_shader_cache_db": (_("Graphics shader cache (Mesa)"),
                             _("Pre-compiled graphics code for games and apps on AMD/Intel GPUs. Deleting it makes games stutter for a while until shaders are compiled again."),
                             "caution", ()),
    "radv_builtin_shaders": (_("Graphics shader cache (AMD)"),
                             _("Pre-compiled shaders for AMD graphics. Rebuilt automatically."), "caution", ()),
    "nvidia": (_("Graphics shader cache (NVIDIA)"),
               _("Pre-compiled graphics code from the NVIDIA driver. Games may stutter briefly afterwards."),
               "caution", ()),
    "wine": (_("Wine installers cache"),
             _("Downloaded Wine Gecko/Mono installers. Downloaded again if a new Wine prefix needs them."),
             "safe", ()),
    "winetricks": (_("Winetricks downloads"),
                   _("Installers Winetricks downloaded (fonts, runtimes). Downloaded again when needed."),
                   "safe", ("winetricks",)),
    "lutris": (_("Lutris cache"), _("Downloaded installers and banners from Lutris. Downloaded again when needed."),
               "caution", ("lutris",)),
    # Developer tools
    "pip": (_("Python pip download cache"),
            _("Python packages pip downloaded before. Installed packages keep working; pip just downloads again next time."), "safe", ()),
    "uv": (_("Python uv cache"), _("Python packages downloaded by uv. Existing environments keep working."),
           "safe", ()),
    "pypoetry": (_("Poetry cache"),
                 _("Poetry's package cache — and, by default, Poetry's VIRTUAL ENVIRONMENTS for your projects. They can be recreated with `poetry install`."), "caution", ()),
    "go-build": (_("Go build cache"), _("Compiled Go code. Rebuilt automatically on the next `go build`."),
                 "safe", ()),
    "yarn": (_("Yarn package cache"), _("JavaScript packages Yarn downloaded. Downloaded again when needed."),
             "safe", ()),
    "pnpm": (_("pnpm cache"), _("JavaScript package metadata. Downloaded again when needed."), "safe", ()),
    "node-gyp": (_("node-gyp headers"), _("Node.js headers for building native modules. Re-downloaded."),
                 "safe", ()),
    "electron": (_("Electron downloads"), _("Electron runtime downloads from development tools."), "safe", ()),
    "typescript": (_("TypeScript type cache"), _("Downloaded type definitions for editors."), "safe", ()),
    "deno": (_("Deno cache"), _("Downloaded Deno modules. Downloaded again when needed."), "safe", ()),
    "composer": (_("PHP Composer cache"), _("Downloaded PHP packages."), "safe", ()),
    "ccache": (_("C/C++ compiler cache"), _("Speeds up recompiling C/C++ code. Builds are slower once."),
               "safe", ()),
    "sccache": (_("Compiler cache (sccache)"), _("Speeds up recompiling code. Builds are slower once."), "safe", ()),
    "bazel": (_("Bazel build outputs"), _("Build outputs and downloads of Bazel projects."), "caution", ()),
    "JetBrains": (_("JetBrains IDE caches"),
                  _("Indexes and caches of IntelliJ, PyCharm, etc. The IDE re-indexes your projects (slow the first time). Close the IDE first."), "caution", ("idea", "pycharm", "clion",
                                                                               "webstorm", "goland", "rider")),
    "vscode-cpptools": (_("VS Code C++ IntelliSense cache"), _("Rebuilt automatically by VS Code."), "safe", ()),
    "ms-playwright": (_("Playwright test browsers"),
                      _("Browsers downloaded for automated web testing. Development projects that use Playwright will need `npx playwright install` again."), "review", ()),
    "huggingface": (_("Hugging Face AI models"),
                    _("Downloaded AI models and datasets. They can be several GB each and take a long time to download again."), "review", ()),
    "torch": (_("PyTorch model downloads"), _("Downloaded machine-learning models. Re-downloaded when needed."),
              "review", ()),
    "yay": (_("yay AUR build files"), _("Build folders from installing AUR packages. Safe to delete."), "safe", ("yay",)),
    "paru": (_("paru AUR build files"), _("Build folders from installing AUR packages. Safe to delete."),
             "safe", ("paru",)),
}

# Never offered from ~/.cache: thumbnails have their own smarter category.
SKIP_IN_USER_CACHE = {"thumbnails"}

UNKNOWN_DESCRIPTION = (_("Temporary data saved by “{name}”. Programs put things here they can rebuild or re-download, so deleting it is usually harmless — the program may just be slower the next time it starts. Close it first."))
