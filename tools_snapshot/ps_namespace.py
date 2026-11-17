"""Private ProStreet added cars: v25 structural packs under their own identity.

v25_batch converts the source with MW FORDGT as the material template; the
pack's solids are renamed NFSAD_<car> and SOLIDMAP maps the donor's solid
requests to them, as carbon_namespace does for Carbon. PHYSICS.BIN and
WHEELFIT.BIN come from ProStreet's attributes (ps_attrib); paint and grip are
not written: the donor's values stay. COLLISION.BIN comes from GLOBALB when its
decoder accepts it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import carbon_car as cc

TEMPLATE = 'FORDGT'
# Wheel images ride on roles the game asks for under the donor's name (00751860:
# %s_RIM_BLUR, %s_TREAD, %s_TIRE_N). A pack image no request reaches is never
# loaded and its draws do not show. The rim atlas takes RIM_BLUR as for Carbon
# additions: under TIRE the implicit TIRE_N would put the rubber normal on rims.
WHEEL_REQUESTS = (('{car}_WHEEL', 'RIM_BLUR'), ('TIRE_STYLE01', 'TREAD'), ('TIRE_STYLE02', 'TREAD'),
                  ('METAL_SWATCH', 'RIM'),
                  ('{car}_TIRE_N', 'TIRE_N'), ('TIRE_STYLE02_N', 'TIRE_N'),
                  # SPHERIZE's normal_resource_aliases identity needs a request too.
                  ('{car}_NEON', 'NEON'))


def template(reference):
    """The MW car whose material/platform fields v25 copies (also a recorded input)."""
    return reference.parent / TEMPLATE


def donor_image_aliases(source, reference, car, donor, texture_map):
    """Answer donor image requests for this car privately, including absent lamps.

    GetUsedCarTextureInfo (00751860) names lamp images after the donor's CarParts,
    so an added car would load and release the donor pack's own entries; a release
    it never loaded leaves the donor's count wrapped and the donor later draws
    without them. Each donor image maps to this pack's image of the same role, or
    to an absent private name. Composited paint names are never mapped.
    """
    private = dict(texture_map)
    donor_images = {h for h, _ in cc.texture_blobs((reference / 'TEXTURES.BIN').read_bytes())}
    donor_images -= {cc.name_hash(f'{donor}_{s}') for s in ('SKIN1', 'SKIN1B', 'SKIN2', 'SKIN3', 'SKIN4')}
    aliases = {}
    # v8 labels stop at 31 bytes, but requests use the full role hash. The
    # loader asks for these even when the donor pack has no matching image.
    lamps = {cc.name_hash(f'{car}_KIT{k:02d}_{kind}_{state}'):
             cc.name_hash(f'{donor}_KIT{k:02d}_{kind}_{state}')
             for k in range(100)
             for kind in ('HEADLIGHT', 'HEADLIGHT_GLASS', 'BRAKELIGHT', 'BRAKELIGHT_GLASS')
             for state in ('OFF', 'ON')}
    for hs, blob in cc.texture_blobs((source / 'TEXTURES.BIN').read_bytes()):
        r = blob[-148:-24]
        # Source labels are null padded and may be truncated; trust exact ones only.
        name = bytes(r[89:89 + r[88]]).split(b'\0')[0].decode('ascii')
        exact = cc.name_hash(name) == hs and name.startswith(car + '_')
        wanted = cc.name_hash(donor + name[len(car):]) if exact else None
        if wanted in donor_images and hs in private:
            aliases[wanted] = private[hs]
        if hs in lamps and hs in private:
            aliases[lamps[hs]] = private[hs]
    for name, role in WHEEL_REQUESTS:
        image = private.get(cc.name_hash(name.format(car=car)))
        if image is not None:
            wanted = cc.name_hash(f'{donor}_{role}')
            cc.require(aliases.setdefault(wanted, image) == image, 'two wheel images answer one request')
    # At most two factory styles per car. Both need independent resident images;
    # the normal image keeps NEON while free trim requests load the rotor atlases.
    rotors = [private[h] for h in (cc.name_hash('ROTOR_' + s) for s in ('00', '01', '02', '05')) if h in private]
    free = [cc.name_hash(f'{donor}_{role}') for role in ('DOOR_HANDLE', 'SIDELIGHT')
            if cc.name_hash(f'{donor}_{role}') not in aliases]
    cc.require(len(rotors) <= len(free), 'factory rotor images exceed free donor requests')
    aliases.update(zip(free, rotors))
    for h in donor_images - aliases.keys():
        aliases[h] = cc.name_hash(f'NFSAD_{car}_ABSENT_{h:08X}')
    return sorted(aliases.items())


def source_sidecars(source, reference):
    """PHYSICS/WHEELFIT from ProStreet's attributes; COLLISION from its GLOBALB when the decoder accepts it."""
    import ps_attrib
    from carbon_repair import collision_sidecar
    files = dict(ps_attrib.sidecars(source, reference))
    try:
        files['COLLISION.BIN'] = collision_sidecar(source, reference.name.upper())
    except (ValueError, KeyError, IndexError, AssertionError):
        pass
    return files


def convert(source, reference, destination, *, decode=None, sidecars=source_sidecars):
    """decode/sidecars: another game's reader of the v25 model fields and its own sidecar source."""
    import carbon_namespace
    import v25_batch
    source, reference, destination, protected = cc.conversion_paths(source, reference, destination)
    car, donor = source.name.upper(), reference.name.upper()
    # A stock wing the donor has no SPOILER solid for would never be requested.
    reference_bytes = (reference / 'GEOMETRY.BIN').read_bytes()
    donor_solids = {h for h, _ in cc.solids(reference_bytes)}
    body_roles = () if cc.name_hash(f'{donor}_KIT00_SPOILER_A') in donor_solids else ('SPOILER',)
    result = v25_batch.convert_one(source, template(reference), destination, body_roles, namespace='NFSAD_' + car,
                                   decode=decode)
    texture_map = result.pop('texture_namespace')
    geometry, textures, sidecar = carbon_namespace.isolate(
        (destination / 'GEOMETRY.BIN').read_bytes(), (destination / 'TEXTURES.BIN').read_bytes(),
        donor, car, reference_bytes, texture_map, source_prefix=car, image_aliases=donor_image_aliases(
            source, reference, car, donor, texture_map),
        # The template's paint is generated per car type: bind the donor's skin,
        # which the game builds for this addition (the template's is never resident).
        rebind={cc.name_hash(f'{TEMPLATE}_SKIN1'): cc.name_hash(f'{donor}_SKIN1')})
    files = {'GEOMETRY.BIN': geometry, 'TEXTURES.BIN': textures, 'SOLIDMAP.BIN': sidecar}
    files.update(sidecars(source, reference))
    cc.write_outputs(destination, files, protected)
    result['namespace'] = 'NFSAD_' + car
    result.pop('manifest', None)
    return result
