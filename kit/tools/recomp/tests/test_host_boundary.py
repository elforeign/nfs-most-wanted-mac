"""The host's platform boundary: Objective-C++ and Apple frameworks live only
under host/gpu/metal/ and in the host's own platform files: iPadOS
host/sdl/platform_ui_ios.mm (see host/sdl/platform_ui.h), the macOS cursor
hide host/sdl/os_cursor_macos.mm and the window screen's
frame rate host/sdl/os_display_macos.mm (NFSU1 run 8: 120 Hz). Everything else in
the host is portable C++ over gpu/gpu.h, SDL3 and the platform layer."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOST = ROOT / "host"
APPLE = re.compile(r"#import\b|<Metal/|<AppKit/|<Cocoa/|<AVFoundation/|<AudioToolbox/|<CoreText/|<CoreVideo/|<QuartzCore/")


PLATFORM_FILES = {"host/sdl/platform_ui_ios.mm", "host/sdl/os_cursor_macos.mm", "host/sdl/os_display_macos.mm"}


def outside_metal(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return "gpu/metal" not in relative and relative not in PLATFORM_FILES


class HostBoundary(unittest.TestCase):
    def test_no_objective_c_outside_the_metal_backend(self):
        stray = [p for p in HOST.rglob("*.mm") if outside_metal(p)]
        self.assertEqual(stray, [], "Objective-C++ outside gpu/metal/")

    def test_no_apple_headers_outside_the_metal_backend(self):
        stray = []
        for path in list(HOST.rglob("*.cpp")) + list(HOST.rglob("*.h")):
            if outside_metal(path) and APPLE.search(path.read_text(errors="replace")):
                stray.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(stray, [], "Apple framework headers outside gpu/metal/")


if __name__ == "__main__":
    unittest.main()
