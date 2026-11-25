"""Read a Carbon PC folder, executable or bounded game-folder archive.

Archives are unpacked into Setup's work directory. The original selection is
retained for updates; the installed data is always a separate verified copy.
"""
from pathlib import Path
import shutil
import tempfile
import zipfile

from . import carbon_disc, carbon_patch, xdvdfs


FIX = "Choose a Carbon PC folder with NFSC.exe and TRACKS, NFSC.exe, a .zip/.7z of that folder, or your PC disc (.iso, ISO-compatible .mdf, or a disc folder)."
DISC_FIX = "Use Carbon update to select the official update and NFSC.exe to select your compatible game executable."


def is_xbox_iso(source):
    if source.suffix.lower() != ".iso" or not source.is_file():
        return False
    try:
        with source.open("rb") as image:
            xdvdfs.find_base(image)
    except (OSError, xdvdfs.DiscError):
        return False
    return True


def prepare(source, work, events, update=None, exe=None, extra=()):
    from . import backend as b

    source = Path(source)
    if source.is_file() and source.name.casefold() == "nfsc.exe":
        source = source.parent
    if carbon_disc.is_disc(source):
        return prepare_disc(source, work, events, update, exe, extra)
    if is_xbox_iso(source):
        raise b.SetupError("This is an Xbox 360 disc image.",
                           "Add it under Xbox 360 files (optional). Choose your PC game first.")
    if update or exe or extra:
        if looks_like_disc(source):  # a disc the player chose as one, but not the supported Carbon disc
            raise disc_refusal(source)
        raise b.SetupError("The Carbon update and a separate NFSC.exe are only used with original Carbon PC discs.", FIX)
    if source.is_file() and source.suffix.lower() == ".iso":
        # An ISO 9660 disc is not the private UDF input handled below.
        try:
            disc = b.pc_disc.Disc(source)
        except b.pc_disc.DiscError:
            pass
        else:
            disc.close()
            raise disc_refusal(source)
        try:
            from . import carbon_repack
        except ImportError:  # the public Setup does not carry the disc-image reader
            raise disc_refusal(source) from None
        return carbon_repack.prepare(source, work, events)
    if looks_like_disc(source):
        raise disc_refusal(source)
    if source.suffix.lower() not in (".zip", ".7z") or not source.is_file():
        b.validate_carbon(source)
        return source
    before = b.sha_file(source)

    def unpack(out):
        try:
            if source.suffix.lower() == ".zip":
                with b.ZipDirectoryBudget().open(source, "The Carbon archive", FIX) as archive:
                    members, total = b.zip_members(archive, "The Carbon archive", FIX)
                    space(total)
                    out.mkdir()
                    b.unpack_zip(archive, members, total, out,
                                 lambda i, written: events.progress(written, total),
                                 label="The Carbon archive", fix=FIX)
            else:
                b.unpack_pc_sevenzip(source, out, events, FIX)
            if b.sha_file(source) != before:
                raise b.SetupError("The Carbon archive changed while being unpacked.", FIX)
            root = find_root(out)
            b.validate_carbon(root)
            return root
        except (zipfile.BadZipFile, OSError) as error:
            raise b.SetupError(f"The Carbon archive can't be read: {error}", FIX) from None
        except b.Cancelled:
            raise
        except b.SetupError as error:
            raise b.SetupError(str(error), error.fix or FIX) from None

    def space(total):
        if total > b.ZIP_MAX_BYTES:
            raise b.SetupError("The Carbon archive is larger than 8 GB.", FIX)
        b.require_space(work, total + 512 * 1024**2)

    root = b.unpacked(work, dict(path=str(source), sha256=before), events,
                      "the Carbon archive", unpack, name="carbon_src")
    b.validate_carbon(root)
    return root


UNREADABLE_FIX = ("Make an .iso from your original Carbon disc (2048-byte sectors; raw .mdf/.bin images can't be read), "
                  "or choose your installed and updated Carbon folder instead.")
EDITION_FIX = ("Choose your installed Carbon folder, updated to 1.4, instead. If you report this on GitHub, include the "
               "disc id shown here.")


def looks_like_disc(source):
    """A chosen disc image (.iso/.mdf file) or a disc-shaped folder (autorun.inf and game archives, no game folders)."""
    if source.is_file():
        return source.suffix.lower() in (".iso", ".mdf")
    from . import backend as b
    return b.pc_disc.is_disc(source)


def disc_refusal(source):
    """Name why a chosen disc is not the supported Carbon PC disc. Messages that send a disc owner to "the game folder
    was not found" or "the update is only used with discs" leave them nowhere to go."""
    from . import backend as b
    try:
        disc = b.pc_disc.Disc(source)
    except b.pc_disc.DiscError:
        kind = ".mdf" if source.suffix.lower() == ".mdf" else "disc image"
        return b.SetupError(f"Setup can't read this {kind}: it has no readable ISO 9660 file system.", UNREADABLE_FIX)
    try:
        if "speed.exe" in disc.files:
            return b.SetupError("This is a Need for Speed: Most Wanted disc, not Carbon.",
                                "Choose your Carbon PC disc, or use this disc with Most Wanted Setup.")
        if not disc.archives:
            return b.SetupError("This disc has no Carbon game archives (0compressed.zip).",
                                "Choose disc 1 of your Carbon PC game, or your installed Carbon folder.")
        if disc.product != carbon_disc.PRODUCT:
            return b.SetupError(f"This disc isn't a supported Carbon PC disc edition (disc id {disc.product or 'missing'}).",
                                EDITION_FIX)
        return b.SetupError("This Carbon disc could not be used.", FIX)
    finally:
        disc.close()


def find_root(top):
    from . import backend as b

    def matches(folder):
        entries = {p.name.casefold(): p for p in folder.iterdir()}
        return ("nfsc.exe" in entries and entries["nfsc.exe"].is_file()
                and "tracks" in entries and entries["tracks"].is_dir())

    candidates = ([top] if matches(top) else
                  [p for p in top.iterdir() if p.is_dir() and matches(p)])
    if len(candidates) != 1:
        raise b.SetupError("The Carbon archive must contain exactly one PC game folder with NFSC.exe and TRACKS.", FIX)
    return candidates[0]


def prepare_disc(source, work, events, update, exe, extra=()):
    """Patch a temporary disc tree, discard its executables, then validate the player's copy."""
    from . import backend as b

    # The work lock is held. Clear a killed run's raw tree even when the updated copy is cached.
    temporary = Path(work) / "tmp"
    temporary.mkdir(exist_ok=True)
    for stale in temporary.glob("carbon-disc-*"):
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale)

    b.require_disc_inputs(update, exe, "1.4", b.CARBON_DISC_EXE, DISC_FIX)
    update, exe = Path(update), Path(exe)
    if not update.is_file():
        raise b.SetupError("The chosen Carbon update is missing or not a file.", DISC_FIX)
    if not exe.is_file() or b.sha_file(exe) != b.CARBON_EXE_SHA256:
        raise b.carbon_exe_refusal(exe)

    def inputs():
        discs = {}
        try:
            discs = b.pc_disc.disc_set(source, extra, allow_mdf=True, missing_fix=carbon_disc.MISSING_FIX)
            return dict(tools=b.extraction_tools("carbon_source.py", "carbon_disc.py", "carbon_patch.py"), layout=1, discs=[dict(path=str(d.path.resolve()),
                        sha256=b.sha_file(d.path) if d.handle else b.digest_tree(d.path))
                        for d in discs.values()], update=dict(path=str(update), sha256=b.sha_file(update)),
                        exe=dict(path=str(exe), sha256=b.sha_file(exe)))
        finally:
            for disc in discs.values():
                disc.close()

    try:
        key = inputs()
        patch = carbon_patch.Update(update)
        def unpack(out):
            # The disc executable is needed only for the update's edition check.
            # Never retain the raw extraction in Setup's resumable cache.
            with tempfile.TemporaryDirectory(prefix="carbon-disc-", dir=temporary) as td:
                # The raw tree and the updated copy below exist together until this block ends.
                raw = carbon_disc.prepare(source, Path(td), events, extra, reserve=1)
                patch.apply(raw, out, progress=events.progress)
                shutil.copyfile(exe, out / "NFSC.exe")
                b.validate_carbon(out)
                if inputs() != key:
                    raise b.SetupError("A Carbon disc, update or NFSC.exe changed while being installed.", DISC_FIX)
            return out
        root = b.unpacked(Path(work), key, events, "the Carbon discs, update and NFSC.exe", unpack, name="carbon_src")
        b.validate_carbon(root)
        return root
    except b.pc_disc.DiscError as error:
        raise b.SetupError(str(error), error.fix) from None
    except (carbon_patch.PatchError, OSError) as error:
        raise b.SetupError(str(error), DISC_FIX) from None
