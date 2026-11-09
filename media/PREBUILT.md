# Rebuilding the Setup libraries

Setup includes compiled, game-independent static libraries for Apple Silicon and
macOS 14.5 or later: FFmpeg 7.1.1 under LGPL version 2.1 or later, and this
project's host audio, media wrapper and shader translator under MIT. The final
application is compiled and linked on your Mac using your own game files.

The unchanged source archive is beside the compiled set in Setup.app at
Contents/Resources/third_party/dl/ffmpeg-7.1.1.tar.xz. The project sources and
build scripts are in Contents/Resources/source.tar.gz. The actual release
configure line is in Contents/Resources/prebuilt/configure.txt; the component
selection, target flags and source modifications are in media/build_ffmpeg.sh.
The source archive SHA-256 is
733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1.
The LGPL text is media/licenses/LGPL-2.1.txt; retained ISC, MIT, IJG and zlib
notices are media/licenses/FFmpeg-notices.txt. LICENSE contains the project's MIT
text, and NOTICE includes the third-party attributions. Help > Licences in the
installed game includes these texts. This software is based in part on the work
of the Independent JPEG Group.

To rebuild or modify the libraries, first extract source.tar.gz into a writable
folder and copy the accompanying third_party/dl directory into that folder.
With the Command Line Tools installed, run these commands from that folder
(replace /path/to/python3 with Setup.app/Contents/Resources/python/bin/python3
or another Python 3):

```
export TOOLS_PY=/path/to/python3
sh media/build_ffmpeg.sh --release
sh host/audio/build.sh lib --release
sh host/shaders/build.sh lib --release
```

FFmpeg's configure selection disables autodetection, network, GPL, nonfree and
version-3 components. The script marks and applies guards to the extracted
Makefile and AC-3 initialization call, excluding the two GPL arm64 AC-3 DSP
implementations and retaining the LGPL C fallback. The archive remains unchanged.
The release build checks every compiled source header and every archive member's
CPU and deployment target. Release archives omit compiler debug metadata, which otherwise
contains build-machine paths; matching objects are stripped before the source audit.
FFmpeg's ordinary source build remains available by
omitting --release. Its default target follows the installed compiler.

To make Setup compile from source, remove or rename the optional prebuilt
folder in its extracted source folder. Setup uses the source path automatically
when the compiled set is missing, incomplete, incompatible with the shipped
sources, or fails SHA-256 verification.
The expected manifest digest comes from a separate resource in the signed Setup
app, so replacing both the libraries and their extracted manifest also falls
back to source. An unreadable optional copy does not prevent installation.
Setup retains its build sources in the
work/cache folder selected during installation. To relink with your rebuilt or
modified libraries, use that retained build source folder, run the commands above
(or replace the archives in third_party/ffmpeg/lib), then run:

```
export TOOLS_PY=/path/to/python3
export NFS_PUBLIC=1
export NFS_ROOTS="$HOME/Library/Application Support/NFSMW-port/roots.txt"
sh runtime/build.sh
sh runtime/bundle.sh /path/to/output
```

If you keep your player profile elsewhere, set NFS_ROOTS to that profile's
roots.txt instead. It must name your own available PC game and prepared assets.
These commands link the application locally using the retained recompiled game
objects. They do not launch it. Keep your personal game-derived objects and app
on your own Mac. The project's MIT terms and FFmpeg's LGPL terms permit modifying
the libraries and debugging those modifications, including reverse engineering
for that purpose. No restriction is added to those rights.
