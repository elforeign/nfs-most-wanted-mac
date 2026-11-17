"""Read a Carbon PC folder, executable or bounded game-folder archive.

Archives are unpacked into Setup's work directory. The original selection is
retained for updates; the installed data is always a separate verified copy.
"""
from pathlib import Path
import shutil
import zipfile


FIX = "Choose a Carbon PC folder with NFSC.exe and TRACKS, NFSC.exe, or a .zip/.7z of that folder."


def prepare(source, work, events):
    from . import backend as b

    source = Path(source)
    if source.is_file() and source.name.casefold() == "nfsc.exe":
        source = source.parent
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
                with zipfile.ZipFile(source) as archive:
                    members, total = b.zip_members(archive, "The Carbon archive", FIX)
                    space(total)
                    out.mkdir()
                    b.unpack_zip(archive, members, total, out,
                                 lambda i, written: events.progress(written, total),
                                 label="The Carbon archive", fix=FIX)
            else:
                members, total = b.sevenzip_members(source)
                space(total)
                out.mkdir()
                b.unpack_sevenzip(source, members, total, out, events)
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
