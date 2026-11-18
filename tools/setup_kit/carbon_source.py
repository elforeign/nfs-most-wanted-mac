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


def prepare(source, work, events, update=None, exe=None):
    from . import backend as b

    source = Path(source)
    if source.is_file() and source.name.casefold() == "nfsc.exe":
        source = source.parent
    if carbon_disc.is_disc(source):
        return prepare_disc(source, work, events, update, exe)
    if is_xbox_iso(source):
        raise b.SetupError("This is an Xbox 360 disc image.",
                           "Add it under Xbox 360 files (optional). Choose your PC game first.")
    if update or exe:
        raise b.SetupError("The Carbon update and a separate NFSC.exe are only used with original Carbon PC discs.", FIX)
    if source.is_file() and source.suffix.lower() == ".iso":
        try:
            from . import carbon_repack
        except ImportError:  # the public Setup does not carry the disc-image reader
            raise b.SetupError("A disc image (.iso) is not supported here.", FIX)
        return carbon_repack.prepare(source, work, events)
    if b.pc_disc.is_disc(source):
        raise b.SetupError("Carbon PC disc installation is not supported yet.", FIX)
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
            raise b.SetupError(str(error), FIX) from None

    def space(total):
        if total > b.ZIP_MAX_BYTES:
            raise b.SetupError("The Carbon archive is larger than 8 GB.", FIX)
        if shutil.disk_usage(work).free < total + 512 * 1024**2:
            raise b.SetupError("There is not enough free space to unpack the Carbon archive.", FIX)

    root = b.unpacked(work, dict(path=str(source), sha256=before), events,
                      "the Carbon archive", unpack, name="carbon_src")
    b.validate_carbon(root)
    return root


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


def prepare_disc(source, work, events, update, exe):
    """Patch a temporary disc tree, discard its executables, then validate the player's copy."""
    from . import backend as b

    if not update:
        raise b.SetupError("The Carbon PC discs need the official Carbon update.", DISC_FIX)
    if not exe:
        raise b.SetupError("The Carbon PC discs need your compatible NFSC.exe.", DISC_FIX)
    update, exe = Path(update), Path(exe)
    if not update.is_file():
        raise b.SetupError("The chosen Carbon update is missing or not a file.", DISC_FIX)
    if not exe.is_file() or b.sha_file(exe) != b.CARBON_EXE_SHA256:
        raise b.SetupError("NFSC.exe is not the supported PC release of Need for Speed Carbon.", DISC_FIX)

    def inputs():
        discs = {}
        try:
            discs = b.pc_disc.disc_set(source, allow_mdf=True, missing_fix=carbon_disc.MISSING_FIX)
            return dict(layout=1, discs=[dict(path=str(d.path.resolve()),
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
            temporary = Path(work) / "tmp"
            temporary.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="carbon-disc-", dir=temporary) as td:
                raw = carbon_disc.prepare(source, Path(td), events)
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
