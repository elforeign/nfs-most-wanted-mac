"""Private image identities shared by the car texture writers."""
import struct


def role_label(name, name_hash):
    """Keep runtime role endings and paired normal names within 23 characters.

    The runtime hashes a diffuse label plus "_N" to find its normal map. Budget
    both labels together so shortening a stem cannot break that relationship.
    """
    role = next((r for r in ('_TIRE_N', '_BADGING_N', '_TIRE', '_BADGING') if name.endswith(r)), None)
    if role is None:
        return name
    stem, base = name[:-len(role)], role.removesuffix('_N')
    if len(stem) + len(base) + 2 <= 23:
        return name
    return f'NFSAD_{name_hash(stem) & 0xFFFFFFF:07X}{role}'


def rename(blobs, source, namespace, name_hash, require, *, label=None):
    """Rename completed v5 blobs after selecting source pixels and donor defaults.

    Include donor fallbacks and global extras: eviction frees every attached
    image, so a retained shared hash can invalidate another car's texture.
    Return all old/private lookup pairs for geometry and runtime requests.
    """
    slot, prefix = namespace
    private, mapping = {}, []
    for hs, blob in blobs:
        rec = bytearray(blob[-156:-32])
        if hs in source:
            r = source[hs][0]
            name = bytes(r[89:89 + r[88] - 1]).decode('ascii')
        else:
            name = rec[12:36].split(b'\0')[0].decode('ascii')
        target = (prefix + name[len(slot):] if name.startswith(slot + '_') else
                  name if name.startswith(prefix + '_') else prefix + '_' + name)
        # Retail debug labels may be truncated; retain distinct identities.
        if hs not in source and name_hash(name) != hs:
            target += f'_{hs:08X}'
        target = label(target) if label is not None else role_label(target, name_hash)
        private_hash = name_hash(target)
        require(private_hash not in private, 'texture namespace collision')
        rec[12:36] = target.encode('ascii')[:23].ljust(24, b'\0')
        struct.pack_into('<I', rec, 36, private_hash)
        private[private_hash] = blob[:-156] + rec + blob[-32:]
        mapping.append((hs, private_hash))
    return sorted(private.items()), mapping
